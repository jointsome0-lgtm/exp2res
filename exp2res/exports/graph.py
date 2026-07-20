"""Frozen §13.12 assessment export graph and render-input bundle."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Generic, Literal, TypeVar, cast

from pydantic import ConfigDict

from exp2res.domain.models import (
    AssessmentSnapshot,
    Contradiction,
    EvidenceItem,
    ExperienceFact,
    GapQuestion,
    RawLog,
    SelfClaim,
    SelfSignal,
    StrictModel,
)
from exp2res.domain.enums import VerificationStatus
from exp2res.errors import (
    IntegrityFailureError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
)
from exp2res.storage.repository import (
    get_experience_fact,
    hydrate_assessment_snapshot,
    hydrate_contradiction,
    hydrate_evidence_item,
    hydrate_gap_question,
    hydrate_raw_log,
    hydrate_self_claim,
    hydrate_self_signal,
)


T = TypeVar("T")

_AGGREGATE_PRECEDENCE = (
    "rejected",
    "unsupported",
    "contradicted",
    "needs_clarification",
    "partially_supported",
    "inferred_but_acceptable",
    "supported",
)


def id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _reduce_verification_status(
    statuses: list[VerificationStatus],
) -> VerificationStatus:
    values = set(statuses)
    if not values:
        raise IntegrityFailureError("snapshot_claim_set_empty")
    if "unverified" in values:
        return "unverified"
    for status in _AGGREGATE_PRECEDENCE:
        if status in values:
            return cast(VerificationStatus, status)
    raise IntegrityFailureError("snapshot_status_invalid")


@dataclass(frozen=True)
class StoredRecord(Generic[T]):
    value: T
    generation_id: str
    produced_by_run_id: str


@dataclass(frozen=True)
class FactSourceRecord:
    fact_id: str
    evidence_item_id: str
    support_type: Literal["direct", "corroborating"]


@dataclass(frozen=True)
class AssessmentExportGraph:
    snapshot: StoredRecord[AssessmentSnapshot]
    snapshot_created_at_text: str
    claims: tuple[StoredRecord[SelfClaim], ...]
    signals: tuple[StoredRecord[SelfSignal], ...]
    facts: tuple[StoredRecord[ExperienceFact], ...]
    evidence_items: tuple[EvidenceItem, ...]
    raw_logs: tuple[RawLog, ...]
    gaps: tuple[StoredRecord[GapQuestion], ...]
    contradictions: tuple[StoredRecord[Contradiction], ...]
    fact_sources: tuple[FactSourceRecord, ...]

    def source_ids(self) -> dict[str, list[str]]:
        return {
            "self_claim_ids": [item.value.id for item in self.claims],
            "self_signal_ids": [item.value.id for item in self.signals],
            "experience_fact_ids": [item.value.id for item in self.facts],
            "evidence_item_ids": [item.id for item in self.evidence_items],
            "raw_log_ids": [item.id for item in self.raw_logs],
            "gap_question_ids": [item.value.id for item in self.gaps],
            "contradiction_ids": [item.value.id for item in self.contradictions],
        }


class _BundleModel(StrictModel):
    # Export completeness lists are intentionally not subject to the ordinary
    # §11 per-list model boundary cap.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SnapshotRenderEntry(_BundleModel):
    value: AssessmentSnapshot
    stored_created_at: str
    generation_id: str
    produced_by_run_id: str


class ClaimRenderEntry(_BundleModel):
    value: SelfClaim
    generation_id: str
    produced_by_run_id: str


class SignalRenderEntry(_BundleModel):
    value: SelfSignal
    generation_id: str
    produced_by_run_id: str


class FactRenderEntry(_BundleModel):
    value: ExperienceFact
    generation_id: str
    produced_by_run_id: str


class GapRenderEntry(_BundleModel):
    value: GapQuestion
    generation_id: str
    produced_by_run_id: str


class ContradictionRenderEntry(_BundleModel):
    value: Contradiction
    generation_id: str
    produced_by_run_id: str


class EvidenceRenderEntry(_BundleModel):
    value: EvidenceItem


class RawLogRenderEntry(_BundleModel):
    value: RawLog


class FactSourceRenderEntry(_BundleModel):
    fact_id: str
    evidence_item_id: str
    support_type: Literal["direct", "corroborating"]


class AssessmentRenderInputBundle(_BundleModel):
    manifest_version: Literal[1] = 1
    output_kind: Literal["assessment"] = "assessment"
    assessment_snapshots: list[SnapshotRenderEntry]
    self_claims: list[ClaimRenderEntry]
    self_signals: list[SignalRenderEntry]
    experience_facts: list[FactRenderEntry]
    evidence_items: list[EvidenceRenderEntry]
    raw_logs: list[RawLogRenderEntry]
    gap_questions: list[GapRenderEntry]
    contradictions: list[ContradictionRenderEntry]
    fact_sources: list[FactSourceRenderEntry]


def render_input_bundle(graph: AssessmentExportGraph) -> AssessmentRenderInputBundle:
    return AssessmentRenderInputBundle(
        assessment_snapshots=[
            SnapshotRenderEntry(
                value=graph.snapshot.value,
                stored_created_at=graph.snapshot_created_at_text,
                generation_id=graph.snapshot.generation_id,
                produced_by_run_id=graph.snapshot.produced_by_run_id,
            )
        ],
        self_claims=[
            ClaimRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in graph.claims
        ],
        self_signals=[
            SignalRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in graph.signals
        ],
        experience_facts=[
            FactRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in graph.facts
        ],
        evidence_items=[EvidenceRenderEntry(value=item) for item in graph.evidence_items],
        raw_logs=[RawLogRenderEntry(value=item) for item in graph.raw_logs],
        gap_questions=[
            GapRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in graph.gaps
        ],
        contradictions=[
            ContradictionRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in graph.contradictions
        ],
        fact_sources=[
            FactSourceRenderEntry(
                fact_id=item.fact_id,
                evidence_item_id=item.evidence_item_id,
                support_type=item.support_type,
            )
            for item in graph.fact_sources
        ],
    )


def _stored(row: sqlite3.Row, value: T) -> StoredRecord[T]:
    generation_id = row["generation_id"]
    produced_by_run_id = row["produced_by_run_id"]
    if not isinstance(generation_id, str) or not generation_id:
        raise IntegrityFailureError("export_generation_id_invalid")
    if not isinstance(produced_by_run_id, str) or not produced_by_run_id:
        raise IntegrityFailureError("export_produced_by_run_id_invalid")
    return StoredRecord(
        value=value,
        generation_id=generation_id,
        produced_by_run_id=produced_by_run_id,
    )


def load_current_snapshot(
    connection: sqlite3.Connection, snapshot_id: str
) -> tuple[sqlite3.Row, AssessmentSnapshot]:
    row = connection.execute(
        "SELECT * FROM assessment_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    if row is None:
        raise SelectorNotFoundError()
    snapshot = hydrate_assessment_snapshot(row)
    if snapshot.superseded_at is not None:
        raise SnapshotNotCurrentError()
    return row, snapshot


def _require_reference(
    connection: sqlite3.Connection, ref_type: str, ref_id: str, diagnostic: str
) -> None:
    table_and_lifecycle = {
        "raw_log": ("raw_logs", False),
        "evidence_item": ("evidence_items", False),
        "experience_fact": ("experience_facts", True),
        "self_signal": ("self_signals", True),
    }
    try:
        table, lifecycle = table_and_lifecycle[ref_type]
    except KeyError as error:
        raise IntegrityFailureError(diagnostic) from error
    selected = "superseded_at" if lifecycle else "id"
    row = connection.execute(
        f"SELECT {selected} FROM {table} WHERE id = ?", (ref_id,)
    ).fetchone()
    if row is None or (lifecycle and row[0] is not None):
        raise IntegrityFailureError(diagnostic)


def load_assessment_graph(
    connection: sqlite3.Connection,
    *,
    snapshot_row: sqlite3.Row,
    snapshot: AssessmentSnapshot,
) -> AssessmentExportGraph:
    claim_rows = connection.execute(
        "SELECT * FROM self_claims WHERE snapshot_id = ?", (snapshot.id,)
    ).fetchall()
    if not claim_rows:
        raise IntegrityFailureError("snapshot_claim_set_empty")
    claims: list[StoredRecord[SelfClaim]] = []
    for row in claim_rows:
        claim = hydrate_self_claim(row)
        if claim.superseded_at is not None:
            raise IntegrityFailureError("snapshot_claim_not_current")
        claims.append(_stored(row, claim))
    claims.sort(key=lambda item: id_key(item.value.id))

    fresh = _reduce_verification_status(
        [item.value.verification_status for item in claims]
    )
    if snapshot.verification_status != fresh:
        raise IntegrityFailureError("snapshot_aggregate_mismatch")
    summaries = [item.value for item in claims if item.value.claim_kind == "narrative_summary"]
    if len(summaries) != 1 or summaries[0].claim != snapshot.summary:
        raise IntegrityFailureError("snapshot_narrative_gate_failed")

    gap_records: list[StoredRecord[GapQuestion]] = []
    for gap_id in snapshot.gap_question_ids:
        row = connection.execute(
            "SELECT * FROM gap_questions WHERE id = ?", (gap_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("snapshot_gap_reference_invalid")
        gap = hydrate_gap_question(row)
        if gap.superseded_at is not None:
            raise IntegrityFailureError("snapshot_gap_reference_invalid")
        _require_reference(
            connection, gap.target_type, gap.target_id, "gap_target_invalid"
        )
        if gap.answer_log_id is not None:
            _require_reference(
                connection, "raw_log", gap.answer_log_id, "gap_answer_log_invalid"
            )
        gap_records.append(_stored(row, gap))
    gap_records.sort(key=lambda item: id_key(item.value.id))

    contradiction_records: list[StoredRecord[Contradiction]] = []
    for contradiction_id in snapshot.contradiction_ids:
        row = connection.execute(
            "SELECT * FROM contradictions WHERE id = ?", (contradiction_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("snapshot_contradiction_reference_invalid")
        contradiction = hydrate_contradiction(row)
        if contradiction.superseded_at is not None:
            raise IntegrityFailureError("snapshot_contradiction_reference_invalid")
        _require_reference(
            connection,
            contradiction.left_ref_type,
            contradiction.left_ref_id,
            "contradiction_left_ref_invalid",
        )
        _require_reference(
            connection,
            contradiction.right_ref_type,
            contradiction.right_ref_id,
            "contradiction_right_ref_invalid",
        )
        contradiction_records.append(_stored(row, contradiction))
    contradiction_records.sort(key=lambda item: id_key(item.value.id))

    signal_ids = sorted(
        {signal_id for item in claims for signal_id in item.value.source_signal_ids},
        key=id_key,
    )
    signal_records: list[StoredRecord[SelfSignal]] = []
    for signal_id in signal_ids:
        row = connection.execute(
            "SELECT * FROM self_signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("claim_signal_missing")
        signal = hydrate_self_signal(row)
        if signal.superseded_at is not None:
            raise IntegrityFailureError("claim_signal_superseded")
        signal_records.append(_stored(row, signal))
    signals_by_id = {item.value.id: item.value for item in signal_records}

    fact_ids = {
        fact_id for item in claims for fact_id in item.value.source_fact_ids
    }
    for signal in signal_records:
        fact_ids.update(signal.value.supporting_fact_ids)
        fact_ids.update(signal.value.counter_fact_ids)

    fact_records: list[StoredRecord[ExperienceFact]] = []
    fact_source_records: list[FactSourceRecord] = []
    direct_fact_ids: set[str] = set()
    for fact_id in sorted(fact_ids, key=id_key):
        row = connection.execute(
            "SELECT * FROM experience_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("claim_fact_missing")
        fact = get_experience_fact(connection, fact_id)
        if fact is None or fact.superseded_at is not None:
            raise IntegrityFailureError("claim_fact_superseded")
        source_rows = connection.execute(
            "SELECT fact_id, evidence_item_id, support_type "
            "FROM fact_sources WHERE fact_id = ?",
            (fact_id,),
        ).fetchall()
        for source in source_rows:
            support_type = source["support_type"]
            if support_type not in {"direct", "corroborating"}:
                raise IntegrityFailureError("fact_source_support_type_invalid")
            if support_type == "direct":
                direct_fact_ids.add(fact_id)
            fact_source_records.append(
                FactSourceRecord(
                    fact_id=source["fact_id"],
                    evidence_item_id=source["evidence_item_id"],
                    support_type=support_type,
                )
            )
        fact_records.append(_stored(row, fact))
    fact_source_records.sort(
        key=lambda item: (id_key(item.fact_id), id_key(item.evidence_item_id))
    )

    for claim_record in claims:
        claim = claim_record.value
        reached = set(claim.source_fact_ids)
        for signal_id in claim.source_signal_ids:
            signal = signals_by_id[signal_id]
            reached.update(signal.supporting_fact_ids)
            reached.update(signal.counter_fact_ids)
        if not reached or not (reached & direct_fact_ids):
            raise IntegrityFailureError("claim_direct_chain_missing")
        for counterevidence in claim.counterevidence:
            _require_reference(
                connection,
                counterevidence.source_ref_type,
                counterevidence.source_ref_id,
                "claim_counterevidence_reference_invalid",
            )

    evidence_ids = sorted(
        {source.evidence_item_id for source in fact_source_records},
        key=id_key,
    )
    evidence_items: list[EvidenceItem] = []
    for evidence_id in evidence_ids:
        row = connection.execute(
            "SELECT * FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("fact_evidence_missing")
        evidence_items.append(hydrate_evidence_item(row))

    raw_log_ids = sorted({item.raw_log_id for item in evidence_items}, key=id_key)
    raw_logs: list[RawLog] = []
    for raw_log_id in raw_log_ids:
        row = connection.execute(
            "SELECT * FROM raw_logs WHERE id = ?", (raw_log_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("fact_raw_log_missing")
        raw_logs.append(hydrate_raw_log(row))

    reached_evidence = set(evidence_ids)
    reached_logs = set(raw_log_ids)
    for fact_record in fact_records:
        fact = fact_record.value
        if set(fact.evidence_item_ids) - reached_evidence:
            raise IntegrityFailureError("fact_evidence_closure_incomplete")
        if set(fact.source_log_ids) - reached_logs:
            raise IntegrityFailureError("fact_raw_log_closure_incomplete")

    return AssessmentExportGraph(
        snapshot=_stored(snapshot_row, snapshot),
        snapshot_created_at_text=snapshot_row["created_at"],
        claims=tuple(claims),
        signals=tuple(signal_records),
        facts=tuple(fact_records),
        evidence_items=tuple(evidence_items),
        raw_logs=tuple(raw_logs),
        gaps=tuple(gap_records),
        contradictions=tuple(contradiction_records),
        fact_sources=tuple(fact_source_records),
    )
