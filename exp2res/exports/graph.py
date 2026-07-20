"""Frozen §13.12 assessment export graph and render-input bundle."""

from __future__ import annotations

from collections import defaultdict
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
    validate_detection_reference,
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
    # Supplemental rows outside the claim source closure — counterevidence
    # grounding targets, gap targets and answer logs, and contradiction
    # references. They are read to validate rendering, so §13.14 folds them
    # into source_ids and the render-input bundle, while the closed §13.12
    # evidence-map and report projections keep consuming only the closure
    # fields above.
    supplemental_signals: tuple[StoredRecord[SelfSignal], ...] = ()
    supplemental_facts: tuple[StoredRecord[ExperienceFact], ...] = ()
    supplemental_fact_sources: tuple[FactSourceRecord, ...] = ()
    supplemental_evidence_items: tuple[EvidenceItem, ...] = ()
    supplemental_raw_logs: tuple[RawLog, ...] = ()
    # Unlisted current gaps whose shape-valid pre-synthesis answers gated the
    # export: read rows participate in source_ids and the render-input hash
    # (§13.14) while the closed §13.12 projections keep rendering only the
    # listed gaps above.
    supplemental_gaps: tuple[StoredRecord[GapQuestion], ...] = ()

    def source_ids(self) -> dict[str, list[str]]:
        def merged(main: list[str], extra: list[str]) -> list[str]:
            return sorted(set(main) | set(extra), key=id_key)

        return {
            "self_claim_ids": [item.value.id for item in self.claims],
            "self_signal_ids": merged(
                [item.value.id for item in self.signals],
                [item.value.id for item in self.supplemental_signals],
            ),
            "experience_fact_ids": merged(
                [item.value.id for item in self.facts],
                [item.value.id for item in self.supplemental_facts],
            ),
            "evidence_item_ids": merged(
                [item.id for item in self.evidence_items],
                [item.id for item in self.supplemental_evidence_items],
            ),
            "raw_log_ids": merged(
                [item.id for item in self.raw_logs],
                [item.id for item in self.supplemental_raw_logs],
            ),
            "gap_question_ids": merged(
                [item.value.id for item in self.gaps],
                [item.value.id for item in self.supplemental_gaps],
            ),
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


def _merged_stored(
    main: tuple[StoredRecord[T], ...], extra: tuple[StoredRecord[T], ...]
) -> list[StoredRecord[T]]:
    return sorted((*main, *extra), key=lambda item: id_key(item.value.id))


def render_input_bundle(graph: AssessmentExportGraph) -> AssessmentRenderInputBundle:
    bundle_signals = _merged_stored(graph.signals, graph.supplemental_signals)
    bundle_facts = _merged_stored(graph.facts, graph.supplemental_facts)
    bundle_evidence = sorted(
        (*graph.evidence_items, *graph.supplemental_evidence_items),
        key=lambda item: id_key(item.id),
    )
    bundle_raw_logs = sorted(
        (*graph.raw_logs, *graph.supplemental_raw_logs),
        key=lambda item: id_key(item.id),
    )
    bundle_fact_sources = sorted(
        (*graph.fact_sources, *graph.supplemental_fact_sources),
        key=lambda item: (id_key(item.fact_id), id_key(item.evidence_item_id)),
    )
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
            for item in bundle_signals
        ],
        experience_facts=[
            FactRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in bundle_facts
        ],
        evidence_items=[EvidenceRenderEntry(value=item) for item in bundle_evidence],
        raw_logs=[RawLogRenderEntry(value=item) for item in bundle_raw_logs],
        gap_questions=[
            GapRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in _merged_stored(graph.gaps, graph.supplemental_gaps)
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
            for item in bundle_fact_sources
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
    if ref_type == "self_signal":
        row = connection.execute(
            "SELECT superseded_at FROM self_signals WHERE id = ?", (ref_id,)
        ).fetchone()
        if row is None or row[0] is not None:
            raise IntegrityFailureError(diagnostic)
        return
    # §13.3: detection targets exclude displaced records and their linked
    # items, so export reuses Stage 4 insertion's reference validation.
    try:
        validate_detection_reference(
            connection, ref_type=ref_type, ref_id=ref_id, field="export"
        )
    except IntegrityFailureError as error:
        raise IntegrityFailureError(diagnostic) from error


def _validated_answer_log(
    connection: sqlite3.Connection, gap: GapQuestion
) -> RawLog:
    """§14.7 shape check: a real gap-answer record with the copied question."""

    answer_row = connection.execute(
        "SELECT * FROM raw_logs WHERE id = ?", (gap.answer_log_id,)
    ).fetchone()
    if answer_row is None:
        raise IntegrityFailureError("gap_answer_log_invalid")
    answer_log = hydrate_raw_log(answer_row)
    if (
        answer_log.entry_type != "gap_answer"
        or answer_log.metadata.get("question_text") != gap.question
        or answer_log.metadata.get("question_reason") != gap.reason
    ):
        raise IntegrityFailureError("gap_answer_log_invalid")
    return answer_log


def load_assessment_graph(
    connection: sqlite3.Connection,
    *,
    snapshot_row: sqlite3.Row,
    snapshot: AssessmentSnapshot,
) -> AssessmentExportGraph:
    snapshot_record = _stored(snapshot_row, snapshot)
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
        stored = _stored(row, claim)
        # §12 rule 13: one Stage 6 swap shares one generation and run, so a
        # member claim from another generation is a mixed graph (#97).
        if (
            stored.generation_id != snapshot_record.generation_id
            or stored.produced_by_run_id != snapshot_record.produced_by_run_id
        ):
            raise IntegrityFailureError("snapshot_claim_generation_mismatch")
        claims.append(stored)
    claims.sort(key=lambda item: id_key(item.value.id))

    fresh = _reduce_verification_status(
        [item.value.verification_status for item in claims]
    )
    if snapshot.verification_status != fresh:
        raise IntegrityFailureError("snapshot_aggregate_mismatch")
    summaries = [item.value for item in claims if item.value.claim_kind == "narrative_summary"]
    if len(summaries) != 1 or summaries[0].claim != snapshot.summary:
        raise IntegrityFailureError("snapshot_narrative_gate_failed")

    supplemental_refs: dict[str, set[str]] = {}

    def note_supplemental(ref_type: str, ref_id: str) -> None:
        supplemental_refs.setdefault(ref_type, set()).add(ref_id)

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
        note_supplemental(gap.target_type, gap.target_id)
        if gap.answer_log_id is not None:
            answer_log = _validated_answer_log(connection, gap)
            # Complement of the unlisted-gap check below: Stage 6 lists only
            # gaps unanswered at synthesis, so a listed answer recorded at or
            # before the snapshot instant is an inconsistent input.
            if answer_log.recorded_at <= snapshot.created_at:
                raise IntegrityFailureError("snapshot_gap_set_stale")
            note_supplemental("raw_log", gap.answer_log_id)
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
        note_supplemental(contradiction.left_ref_type, contradiction.left_ref_id)
        _require_reference(
            connection,
            contradiction.right_ref_type,
            contradiction.right_ref_id,
            "contradiction_right_ref_invalid",
        )
        note_supplemental(
            contradiction.right_ref_type, contradiction.right_ref_id
        )
        contradiction_records.append(_stored(row, contradiction))
    contradiction_records.sort(key=lambda item: id_key(item.value.id))

    # §13.12 inconsistent-input gate (evals 21.4/21.33): while this snapshot
    # is current, Stage 4 cannot have changed the detection sets without
    # superseding it, so the complete current contradiction set must equal the
    # referenced set and every current unanswered gap must be referenced. A
    # gap answered before synthesis legitimately stays current and unlisted.
    current_contradictions = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM contradictions WHERE superseded_at IS NULL"
        )
    }
    if current_contradictions != set(snapshot.contradiction_ids):
        raise IntegrityFailureError("snapshot_contradiction_set_stale")
    listed_gap_ids = set(snapshot.gap_question_ids)
    omitted_gap_records: list[StoredRecord[GapQuestion]] = []
    for row in connection.execute(
        "SELECT * FROM gap_questions WHERE superseded_at IS NULL"
    ).fetchall():
        gap = hydrate_gap_question(row)
        if gap.id in listed_gap_ids:
            continue
        # An unlisted current gap is legal only when a shape-valid §14.7
        # answer already existed at synthesis: unanswered rows and rows whose
        # answer was recorded after the snapshot instant were writer inputs
        # and must be listed.
        if gap.answer_log_id is None:
            raise IntegrityFailureError("snapshot_gap_set_stale")
        answer_log = _validated_answer_log(connection, gap)
        if answer_log.recorded_at > snapshot.created_at:
            raise IntegrityFailureError("snapshot_gap_set_stale")
        # §13.14: both rows gated the export, so they join the hash surface.
        note_supplemental("raw_log", gap.answer_log_id)
        omitted_gap_records.append(_stored(row, gap))
    omitted_gap_records.sort(key=lambda item: id_key(item.value.id))

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
        # §16.1: a cited signal must ground in at least one fact, or its
        # evidence-map signal_links entry would carry no fact path.
        if not signal.supporting_fact_ids and not signal.counter_fact_ids:
            raise IntegrityFailureError("claim_signal_chain_empty")
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
            # Same reference validation as gap targets and contradiction
            # references: §13.3-displaced rows are not current sources.
            _require_reference(
                connection,
                counterevidence.source_ref_type,
                counterevidence.source_ref_id,
                "export_source_reference_invalid",
            )
            note_supplemental(
                counterevidence.source_ref_type, counterevidence.source_ref_id
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

    # §13.12: the evidence map must agree exactly with the persisted §11
    # relations — per fact, the fact_sources rows, the hydrated
    # evidence_item_ids, and the derived raw-log set are equal, not subsets.
    log_by_evidence = {item.id: item.raw_log_id for item in evidence_items}
    rows_by_fact: dict[str, list[str]] = defaultdict(list)
    for source in fact_source_records:
        rows_by_fact[source.fact_id].append(source.evidence_item_id)
    for fact_record in fact_records:
        fact = fact_record.value
        row_evidence = sorted(set(rows_by_fact.get(fact.id, [])), key=id_key)
        if row_evidence != list(fact.evidence_item_ids):
            raise IntegrityFailureError("fact_evidence_closure_incomplete")
        derived_logs = sorted(
            {log_by_evidence[item] for item in row_evidence}, key=id_key
        )
        if derived_logs != list(fact.source_log_ids):
            raise IntegrityFailureError("fact_raw_log_closure_incomplete")

    # §16.1: a supplemental row entering export — counterevidence grounding,
    # gap target, gap answer log, contradiction reference — resolves its own
    # complete current chain, so out-of-closure targets cascade one level —
    # signal → facts → fact_sources/evidence → raw logs — and every row read
    # here joins the manifest source lists and the render-input bundle.
    ce_signals: list[StoredRecord[SelfSignal]] = []
    ce_facts: list[StoredRecord[ExperienceFact]] = []
    ce_fact_sources: list[FactSourceRecord] = []
    ce_evidence: list[EvidenceItem] = []
    ce_raw_logs: list[RawLog] = []
    invalid = "export_source_reference_invalid"
    ce_fact_ids: set[str] = set(
        supplemental_refs.get("experience_fact", set()) - fact_ids
    )
    for signal_id in sorted(
        supplemental_refs.get("self_signal", set()) - set(signals_by_id), key=id_key
    ):
        row = connection.execute(
            "SELECT * FROM self_signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError(invalid)
        signal = hydrate_self_signal(row)
        if signal.superseded_at is not None:
            raise IntegrityFailureError(invalid)
        # §16.1: a supplemental signal is subject to the same complete-chain
        # requirement as a claim-cited one.
        if not signal.supporting_fact_ids and not signal.counter_fact_ids:
            raise IntegrityFailureError(invalid)
        ce_signals.append(_stored(row, signal))
        ce_fact_ids.update(set(signal.supporting_fact_ids) - fact_ids)
        ce_fact_ids.update(set(signal.counter_fact_ids) - fact_ids)
    ce_evidence_ids: set[str] = set(
        supplemental_refs.get("evidence_item", set()) - set(evidence_ids)
    )
    ce_fact_evidence: dict[str, list[str]] = {}
    for fact_id in sorted(ce_fact_ids, key=id_key):
        row = connection.execute(
            "SELECT * FROM experience_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        fact = get_experience_fact(connection, fact_id)
        if row is None or fact is None or fact.superseded_at is not None:
            raise IntegrityFailureError(invalid)
        ce_facts.append(_stored(row, fact))
        source_rows = connection.execute(
            "SELECT fact_id, evidence_item_id, support_type "
            "FROM fact_sources WHERE fact_id = ?",
            (fact_id,),
        ).fetchall()
        row_evidence: list[str] = []
        for source in source_rows:
            support_type = source["support_type"]
            if support_type not in {"direct", "corroborating"}:
                raise IntegrityFailureError(invalid)
            ce_fact_sources.append(
                FactSourceRecord(
                    fact_id=source["fact_id"],
                    evidence_item_id=source["evidence_item_id"],
                    support_type=support_type,
                )
            )
            row_evidence.append(source["evidence_item_id"])
        if sorted(set(row_evidence), key=id_key) != list(fact.evidence_item_ids):
            raise IntegrityFailureError("fact_evidence_closure_incomplete")
        ce_fact_evidence[fact_id] = list(fact.evidence_item_ids)
        ce_evidence_ids.update(set(fact.evidence_item_ids) - set(evidence_ids))
    ce_fact_sources.sort(
        key=lambda item: (id_key(item.fact_id), id_key(item.evidence_item_id))
    )
    ce_log_ids: set[str] = set(
        supplemental_refs.get("raw_log", set()) - set(raw_log_ids)
    )
    for evidence_id in sorted(ce_evidence_ids, key=id_key):
        row = connection.execute(
            "SELECT * FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError(invalid)
        item = hydrate_evidence_item(row)
        ce_evidence.append(item)
        if item.raw_log_id not in set(raw_log_ids):
            ce_log_ids.add(item.raw_log_id)
    for raw_log_id in sorted(ce_log_ids, key=id_key):
        row = connection.execute(
            "SELECT * FROM raw_logs WHERE id = ?", (raw_log_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError(invalid)
        ce_raw_logs.append(hydrate_raw_log(row))
    # Same per-fact raw-log equality as the claim closure: the evidence-derived
    # log set of each counterevidence-reached fact equals its stored value.
    ce_log_by_evidence = {
        **log_by_evidence,
        **{item.id: item.raw_log_id for item in ce_evidence},
    }
    for fact_record in ce_facts:
        fact = fact_record.value
        derived_logs = sorted(
            {ce_log_by_evidence[item] for item in ce_fact_evidence[fact.id]},
            key=id_key,
        )
        if derived_logs != list(fact.source_log_ids):
            raise IntegrityFailureError("fact_raw_log_closure_incomplete")
    unknown_types = set(supplemental_refs) - {
        "self_signal",
        "experience_fact",
        "evidence_item",
        "raw_log",
    }
    if unknown_types:
        raise IntegrityFailureError(invalid)

    return AssessmentExportGraph(
        snapshot=snapshot_record,
        snapshot_created_at_text=snapshot_row["created_at"],
        claims=tuple(claims),
        signals=tuple(signal_records),
        facts=tuple(fact_records),
        evidence_items=tuple(evidence_items),
        raw_logs=tuple(raw_logs),
        gaps=tuple(gap_records),
        contradictions=tuple(contradiction_records),
        fact_sources=tuple(fact_source_records),
        supplemental_signals=tuple(ce_signals),
        supplemental_facts=tuple(ce_facts),
        supplemental_fact_sources=tuple(ce_fact_sources),
        supplemental_evidence_items=tuple(ce_evidence),
        supplemental_raw_logs=tuple(ce_raw_logs),
        supplemental_gaps=tuple(omitted_gap_records),
    )
