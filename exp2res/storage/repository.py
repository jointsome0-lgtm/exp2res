"""Strict persistence and hydration for the implemented §11 entity subset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterable

from pydantic import ValidationError

from exp2res.domain.calibration import claim_confidence_cap, signal_confidence_cap
from exp2res.domain.enums import VerificationStatus
from exp2res.domain.models import (
    Contradiction,
    AssessmentSnapshot,
    EvidenceItem,
    ExperienceFact,
    GapQuestion,
    OccurredAt,
    RawLog,
    SelfClaim,
    SelfSignal,
    VerificationFinding,
    canonical_project_key,
)
from exp2res.domain.temporal import (
    confidence_exceeds,
    interval_contains,
    occurred_interval,
    placement_supports,
)
from exp2res.errors import HydrationFailureError, IdCollisionError, IntegrityFailureError


@dataclass(frozen=True)
class RawLogBundle:
    raw_log: RawLog
    evidence_items: tuple[EvidenceItem, ...]
    residual_paths: tuple[str, ...] = ()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat()  # type: ignore[union-attr]


def _utc_instant(stored: object) -> datetime:
    """Decode one stored §12 rule 3 datetime TEXT for UTC-instant comparison."""

    if not isinstance(stored, str):
        raise IntegrityFailureError()
    try:
        parsed = datetime.fromisoformat(stored.replace("Z", "+00:00"))
    except ValueError as error:
        raise IntegrityFailureError() from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IntegrityFailureError()
    return parsed.astimezone(timezone.utc)


def lineage_root(connection: sqlite3.Connection, raw_log_id: str) -> str:
    """Resolve a retained record's correction-lineage root (§13.3 rule 10).

    Capture rejects correction cycles and owner deletion re-roots orphans, so
    the walk terminates; a cycle or missing link in stored rows fails closed.
    """

    current = raw_log_id
    seen = {current}
    while True:
        row = connection.execute(
            "SELECT corrects_log_id FROM raw_logs WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError()
        target = row["corrects_log_id"]
        if target is None:
            return current
        if target in seen:
            raise IntegrityFailureError()
        seen.add(target)
        current = target


def insert_raw_log(connection: sqlite3.Connection, raw_log: RawLog) -> None:
    project_key = (
        None if raw_log.project is None else canonical_project_key(raw_log.project)
    )
    try:
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, raw_text, project, project_key,
                external_ref, corrects_log_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_log.id,
                _iso(raw_log.recorded_at),
                raw_log.entry_type,
                raw_log.source_type,
                _iso(raw_log.occurred.start),
                _iso(raw_log.occurred.end),
                raw_log.occurred.precision,
                raw_log.occurred.confidence,
                raw_log.raw_text,
                raw_log.project,
                project_key,
                raw_log.external_ref,
                raw_log.corrects_log_id,
                _json(raw_log.metadata),
            ),
        )
    except sqlite3.IntegrityError as error:
        if "raw_logs.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError() from error


def insert_evidence_item(connection: sqlite3.Connection, item: EvidenceItem) -> None:
    try:
        connection.execute(
            """
            INSERT INTO evidence_items(
                id, created_at, raw_log_id, title, summary, uri, path,
                strength, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                _iso(item.created_at),
                item.raw_log_id,
                item.title,
                item.summary,
                item.uri,
                item.path,
                item.strength,
                _json(item.metadata),
            ),
        )
    except sqlite3.IntegrityError as error:
        if "evidence_items.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError() from error


def validate_experience_fact_sources(
    connection: sqlite3.Connection, fact: ExperienceFact
) -> dict[str, sqlite3.Row]:
    """Revalidate §13.3's retained-row source-selection predicate.

    Facts copy their selected evidence and raw-log IDs at creation time, but
    corrections can later displace one of those retained source rows.  Every
    consumer that treats a fact as current must therefore rerun the same
    selectability checks rather than trusting the copied IDs alone.
    """

    selections: list[tuple[str, str]] = []
    for evidence_id in fact.evidence_item_ids:
        source = connection.execute(
            "SELECT raw_log_id, strength FROM evidence_items WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        if source is None:
            raise IntegrityFailureError()
        selections.append((source["raw_log_id"], source["strength"]))
    reached_ids = {raw_log_id for raw_log_id, _ in selections}
    if reached_ids != set(fact.source_log_ids):
        raise IntegrityFailureError()
    reached_logs: dict[str, sqlite3.Row] = {}
    for raw_log_id in reached_ids:
        row = connection.execute(
            """
            SELECT id, recorded_at, project, project_key,
                   occurred_start, occurred_end, temporal_precision,
                   temporal_confidence,
                   EXISTS(
                       SELECT 1 FROM raw_logs AS correction
                       WHERE correction.corrects_log_id = raw_logs.id
                   ) AS displaced
            FROM raw_logs WHERE id = ?
            """,
            (raw_log_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError()
        reached_logs[raw_log_id] = row
    for raw_log_id, strength in selections:
        if reached_logs[raw_log_id]["displaced"] and strength == "manual_claim":
            raise IntegrityFailureError()
    effective = [row for row in reached_logs.values() if not row["displaced"]]
    if not effective:
        raise IntegrityFailureError()
    if len({lineage_root(connection, raw_log_id) for raw_log_id in reached_ids}) != 1:
        raise IntegrityFailureError()
    return reached_logs


def _hydrate(model: type, payload: dict[str, object]):
    try:
        return model.model_validate_json(_json(payload))
    except (ValidationError, ValueError, TypeError, UnicodeError) as error:
        raise HydrationFailureError() from error


def hydrate_raw_log(row: sqlite3.Row) -> RawLog:
    try:
        metadata = json.loads(row["metadata_json"])
        project = row["project"]
        project_key = row["project_key"]
        expected_key = None if project is None else canonical_project_key(project)
        if project_key != expected_key or (project is not None and not expected_key):
            raise ValueError("stored project key disagrees with project label")
    except (json.JSONDecodeError, IndexError, TypeError, ValueError, UnicodeError) as error:
        raise HydrationFailureError() from error
    payload = {
        "id": row["id"],
        "recorded_at": row["recorded_at"],
        "entry_type": row["entry_type"],
        "source_type": row["source_type"],
        "occurred": {
            "start": row["occurred_start"],
            "end": row["occurred_end"],
            "precision": row["temporal_precision"],
            "confidence": row["temporal_confidence"],
        },
        "raw_text": row["raw_text"],
        "project": row["project"],
        "external_ref": row["external_ref"],
        "corrects_log_id": row["corrects_log_id"],
        "metadata": metadata,
    }
    return _hydrate(RawLog, payload)


def hydrate_evidence_item(row: sqlite3.Row) -> EvidenceItem:
    try:
        metadata = json.loads(row["metadata_json"])
    except (json.JSONDecodeError, TypeError) as error:
        raise HydrationFailureError() from error
    payload = {
        "id": row["id"],
        "created_at": row["created_at"],
        "raw_log_id": row["raw_log_id"],
        "title": row["title"],
        "summary": row["summary"],
        "uri": row["uri"],
        "path": row["path"],
        "strength": row["strength"],
        "metadata": metadata,
    }
    return _hydrate(EvidenceItem, payload)


def insert_experience_fact(
    connection: sqlite3.Connection,
    fact: ExperienceFact,
    *,
    produced_by_run_id: str,
    generation_id: str,
) -> None:
    if not produced_by_run_id or not generation_id:
        raise IntegrityFailureError()
    # §15.11: `superseded_at` is service-owned lifecycle state — a fact row
    # is born current and only §13.3 rule 11 supersession may close it.
    if fact.superseded_at is not None:
        raise IntegrityFailureError()
    if len(fact.evidence_item_ids) != len(set(fact.evidence_item_ids)):
        raise IntegrityFailureError()
    # §12 rule 10 with §13.3 rules 6/10/13: resolve every selected item,
    # enforce the retained-rows selectability predicate — no displaced
    # manual_claim selection, at least one effective selection, one
    # correction lineage — and copy `project`/`project_key` from the
    # governing record rather than trusting the caller's fact values.
    reached_logs = validate_experience_fact_sources(connection, fact)
    effective = [row for row in reached_logs.values() if not row["displaced"]]
    governing = max(
        effective,
        key=lambda row: (_utc_instant(row["recorded_at"]), row["id"].encode("utf-8")),
    )
    if fact.project != governing["project"]:
        raise IntegrityFailureError()
    project_key = governing["project_key"]
    expected_key = (
        None if fact.project is None else canonical_project_key(fact.project)
    )
    if project_key != expected_key or (fact.project is not None and not expected_key):
        raise IntegrityFailureError()
    # §13.3 rule 2 / §15.2 / §16.7 at the commit boundary: the persisted
    # placement never widens beyond the governing window, never raises
    # temporal confidence above the governing record's, and must be entailed
    # by some selected effective placement — the governing copy trivially
    # satisfies this via the governing record itself, while a narrowing needs
    # a selected record asserting an interval inside it at equal-or-stronger
    # §16.7 strength, so equal-width relocation (July 5 support, July 10
    # claim) fails alongside width upgrades.
    def stored_occurred(row: sqlite3.Row) -> OccurredAt:
        try:
            return OccurredAt(
                start=(
                    None
                    if row["occurred_start"] is None
                    else datetime.fromisoformat(
                        row["occurred_start"].replace("Z", "+00:00")
                    )
                ),
                end=(
                    None
                    if row["occurred_end"] is None
                    else datetime.fromisoformat(
                        row["occurred_end"].replace("Z", "+00:00")
                    )
                ),
                precision=row["temporal_precision"],
                confidence=row["temporal_confidence"],
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise IntegrityFailureError() from error

    governing_occurred = stored_occurred(governing)
    if not interval_contains(
        occurred_interval(governing_occurred), occurred_interval(fact.occurred)
    ):
        raise IntegrityFailureError()
    if confidence_exceeds(fact.occurred.confidence, governing_occurred.confidence):
        raise IntegrityFailureError()
    if not any(
        placement_supports(fact.occurred, stored_occurred(row)) for row in effective
    ):
        raise IntegrityFailureError()
    try:
        connection.execute(
            """
            INSERT INTO experience_facts(
                id, created_at, superseded_at, claim, claim_kind, project,
                project_key, role, company, context, ownership_level, action,
                object, outcome, skills_json, technologies_json, themes_json,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, confidence, metadata_json,
                produced_by_run_id, generation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.id,
                _iso(fact.created_at),
                _iso(fact.superseded_at),
                fact.claim,
                fact.claim_kind,
                fact.project,
                project_key,
                fact.role,
                fact.company,
                fact.context,
                fact.ownership_level,
                fact.action,
                fact.object,
                fact.outcome,
                _json(fact.skills),
                _json(fact.technologies),
                _json(fact.themes),
                _iso(fact.occurred.start),
                _iso(fact.occurred.end),
                fact.occurred.precision,
                fact.occurred.confidence,
                fact.confidence,
                _json(fact.metadata),
                produced_by_run_id,
                generation_id,
            ),
        )
        connection.executemany(
            """
            INSERT INTO fact_sources(fact_id, evidence_item_id, support_type)
            VALUES (?, ?, 'direct')
            """,
            ((fact.id, evidence_id) for evidence_id in fact.evidence_item_ids),
        )
    except sqlite3.IntegrityError as error:
        if "experience_facts.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError() from error


def hydrate_experience_fact(
    row: sqlite3.Row, source_rows: Iterable[sqlite3.Row]
) -> ExperienceFact:
    try:
        metadata = json.loads(row["metadata_json"])
        skills = json.loads(row["skills_json"])
        technologies = json.loads(row["technologies_json"])
        themes = json.loads(row["themes_json"])
        project = row["project"]
        project_key = row["project_key"]
        expected_key = None if project is None else canonical_project_key(project)
        if project_key != expected_key or (project is not None and not expected_key):
            raise ValueError("stored project key disagrees with project label")

        sources = list(source_rows)
        if not sources:
            raise ValueError("experience fact has no fact_sources rows")
        evidence_ids: list[str] = []
        raw_log_ids: set[str] = set()
        direct = False
        for source in sources:
            if source["fact_id"] != row["id"]:
                raise ValueError("fact source belongs to a different fact")
            support_type = source["support_type"]
            if support_type not in {"direct", "corroborating"}:
                raise ValueError("invalid fact source support type")
            direct = direct or support_type == "direct"
            evidence_ids.append(source["evidence_item_id"])
            raw_log_ids.add(source["raw_log_id"])
        if not direct or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("invalid fact source set")
        evidence_ids.sort(key=lambda value: value.encode("utf-8"))
        source_log_ids = sorted(raw_log_ids, key=lambda value: value.encode("utf-8"))
    except (
        AttributeError,
        json.JSONDecodeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
    ) as error:
        raise HydrationFailureError() from error

    payload = {
        "id": row["id"],
        "created_at": row["created_at"],
        "superseded_at": row["superseded_at"],
        "claim": row["claim"],
        "claim_kind": row["claim_kind"],
        "project": project,
        "role": row["role"],
        "company": row["company"],
        "context": row["context"],
        "ownership_level": row["ownership_level"],
        "action": row["action"],
        "object": row["object"],
        "outcome": row["outcome"],
        "skills": skills,
        "technologies": technologies,
        "themes": themes,
        "occurred": {
            "start": row["occurred_start"],
            "end": row["occurred_end"],
            "precision": row["temporal_precision"],
            "confidence": row["temporal_confidence"],
        },
        "source_log_ids": source_log_ids,
        "evidence_item_ids": evidence_ids,
        "confidence": row["confidence"],
        "metadata": metadata,
    }
    return _hydrate(ExperienceFact, payload)


def _fact_source_rows(
    connection: sqlite3.Connection, fact_id: str
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT fs.fact_id, fs.evidence_item_id, fs.support_type, ei.raw_log_id
        FROM fact_sources AS fs
        JOIN evidence_items AS ei ON ei.id = fs.evidence_item_id
        WHERE fs.fact_id = ?
        """,
        (fact_id,),
    ).fetchall()


def get_experience_fact(
    connection: sqlite3.Connection, fact_id: str
) -> ExperienceFact | None:
    row = connection.execute(
        "SELECT * FROM experience_facts WHERE id = ?", (fact_id,)
    ).fetchone()
    if row is None:
        return None
    return hydrate_experience_fact(row, _fact_source_rows(connection, fact_id))


def list_experience_facts(
    connection: sqlite3.Connection, *, current_only: bool = True
) -> tuple[ExperienceFact, ...]:
    where = " WHERE superseded_at IS NULL" if current_only else ""
    rows = connection.execute("SELECT * FROM experience_facts" + where).fetchall()
    facts = [
        hydrate_experience_fact(row, _fact_source_rows(connection, row["id"]))
        for row in rows
    ]
    facts.sort(
        key=lambda item: (
            item.created_at.astimezone(timezone.utc),
            item.id.encode("utf-8"),
        )
    )
    return tuple(facts)


def mark_facts_superseded(
    connection: sqlite3.Connection,
    fact_ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    if superseded_at.tzinfo is None or superseded_at.utcoffset() is None:
        raise IntegrityFailureError()
    ids = list(fact_ids)
    if len(ids) != len(set(ids)):
        raise IntegrityFailureError()
    for fact_id in ids:
        row = connection.execute(
            "SELECT superseded_at FROM experience_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if row is None or row[0] is not None:
            raise IntegrityFailureError()
    try:
        for fact_id in ids:
            cursor = connection.execute(
                """
                UPDATE experience_facts SET superseded_at = ?
                WHERE id = ? AND superseded_at IS NULL
                """,
                (_iso(superseded_at), fact_id),
            )
            if cursor.rowcount != 1:
                raise IntegrityFailureError()
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError() from error


def insert_self_signal(
    connection: sqlite3.Connection,
    signal: SelfSignal,
    *,
    produced_by_run_id: str,
    generation_id: str,
) -> None:
    if not produced_by_run_id or not generation_id:
        raise IntegrityFailureError("signal_production_identity_invalid")
    if signal.superseded_at is not None:
        raise IntegrityFailureError("signal_initial_lifecycle_invalid")

    referenced: dict[str, sqlite3.Row] = {}
    for fact_id in (*signal.supporting_fact_ids, *signal.counter_fact_ids):
        if fact_id in referenced:
            continue
        row = connection.execute(
            "SELECT confidence, superseded_at FROM experience_facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("signal_fact_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError("signal_fact_superseded")
        referenced[fact_id] = row

    distinct_source_log_count = 0
    if signal.supporting_fact_ids:
        placeholders = ",".join("?" for _ in signal.supporting_fact_ids)
        distinct_source_log_count = connection.execute(
            "SELECT COUNT(DISTINCT ei.raw_log_id) "
            "FROM fact_sources AS fs "
            "JOIN evidence_items AS ei ON ei.id = fs.evidence_item_id "
            f"WHERE fs.fact_id IN ({placeholders})",
            signal.supporting_fact_ids,
        ).fetchone()[0]
    cap = signal_confidence_cap(
        supporting_confidences=(
            referenced[fact_id]["confidence"]
            for fact_id in signal.supporting_fact_ids
        ),
        distinct_source_log_count=distinct_source_log_count,
        has_counter_facts=bool(signal.counter_fact_ids),
    )
    if confidence_exceeds(signal.confidence, cap):
        raise IntegrityFailureError("signal_confidence_above_cap")

    try:
        connection.execute(
            """
            INSERT INTO self_signals(
                id, created_at, superseded_at, signal_type, statement,
                supporting_fact_ids_json, counter_fact_ids_json, confidence,
                metadata_json, produced_by_run_id, generation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.id,
                _iso(signal.created_at),
                _iso(signal.superseded_at),
                signal.signal_type,
                signal.statement,
                _json(signal.supporting_fact_ids),
                _json(signal.counter_fact_ids),
                signal.confidence,
                _json(signal.metadata),
                produced_by_run_id,
                generation_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        if "self_signals.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError("signal_insert_failed") from error


def hydrate_self_signal(row: sqlite3.Row) -> SelfSignal:
    try:
        supporting_fact_ids = json.loads(row["supporting_fact_ids_json"])
        counter_fact_ids = json.loads(row["counter_fact_ids_json"])
        metadata = json.loads(row["metadata_json"])
    except (json.JSONDecodeError, IndexError, TypeError) as error:
        raise HydrationFailureError() from error
    payload = {
        "id": row["id"],
        "created_at": row["created_at"],
        "superseded_at": row["superseded_at"],
        "signal_type": row["signal_type"],
        "statement": row["statement"],
        "supporting_fact_ids": supporting_fact_ids,
        "counter_fact_ids": counter_fact_ids,
        "confidence": row["confidence"],
        "metadata": metadata,
    }
    return _hydrate(SelfSignal, payload)


def list_self_signals(
    connection: sqlite3.Connection, *, current_only: bool = True
) -> tuple[SelfSignal, ...]:
    where = " WHERE superseded_at IS NULL" if current_only else ""
    rows = connection.execute("SELECT * FROM self_signals" + where).fetchall()
    signals = [hydrate_self_signal(row) for row in rows]
    signals.sort(
        key=lambda item: (
            item.created_at.astimezone(timezone.utc),
            item.id.encode("utf-8"),
        )
    )
    return tuple(signals)


def insert_assessment_snapshot(
    connection: sqlite3.Connection,
    snapshot: AssessmentSnapshot,
    *,
    produced_by_run_id: str,
    generation_id: str,
) -> None:
    if not produced_by_run_id or not generation_id:
        raise IntegrityFailureError("snapshot_production_identity_invalid")
    if snapshot.superseded_at is not None:
        raise IntegrityFailureError("snapshot_initial_lifecycle_invalid")
    if snapshot.verification_status != "unverified":
        raise IntegrityFailureError("snapshot_initial_verification_invalid")
    for gap_id in snapshot.gap_question_ids:
        row = connection.execute(
            "SELECT superseded_at, answered FROM gap_questions WHERE id = ?",
            (gap_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("snapshot_gap_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError("snapshot_gap_superseded")
        if row["answered"] != 0:
            raise IntegrityFailureError("snapshot_gap_answered")
    for contradiction_id in snapshot.contradiction_ids:
        row = connection.execute(
            "SELECT superseded_at FROM contradictions WHERE id = ?",
            (contradiction_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("snapshot_contradiction_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError("snapshot_contradiction_superseded")
    try:
        connection.execute(
            """
            INSERT INTO assessment_snapshots(
                id, created_at, superseded_at, scope, scope_target, title,
                summary, gap_question_ids_json, contradiction_ids_json,
                verification_status, metadata_json, produced_by_run_id,
                generation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.id,
                _iso(snapshot.created_at),
                _iso(snapshot.superseded_at),
                snapshot.scope,
                snapshot.scope_target,
                snapshot.title,
                snapshot.summary,
                _json(snapshot.gap_question_ids),
                _json(snapshot.contradiction_ids),
                snapshot.verification_status,
                _json(snapshot.metadata),
                produced_by_run_id,
                generation_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        if "assessment_snapshots.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError("snapshot_insert_failed") from error


def insert_self_claim(
    connection: sqlite3.Connection,
    claim: SelfClaim,
    *,
    produced_by_run_id: str,
    generation_id: str,
) -> None:
    if not produced_by_run_id or not generation_id:
        raise IntegrityFailureError("claim_production_identity_invalid")
    if claim.superseded_at is not None:
        raise IntegrityFailureError("claim_initial_lifecycle_invalid")
    if claim.verification_status != "unverified":
        raise IntegrityFailureError("claim_initial_verification_invalid")
    if claim.counterevidence:
        raise IntegrityFailureError("claim_initial_counterevidence_invalid")
    owner = connection.execute(
        "SELECT superseded_at FROM assessment_snapshots WHERE id = ?",
        (claim.snapshot_id,),
    ).fetchone()
    if owner is None:
        raise IntegrityFailureError("claim_snapshot_missing")
    if owner["superseded_at"] is not None:
        raise IntegrityFailureError("claim_snapshot_superseded")

    confidences: list[str] = []
    for signal_id in claim.source_signal_ids:
        row = connection.execute(
            "SELECT confidence, superseded_at FROM self_signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("claim_signal_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError("claim_signal_superseded")
        confidences.append(row["confidence"])
    for fact_id in claim.source_fact_ids:
        row = connection.execute(
            "SELECT confidence, superseded_at FROM experience_facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("claim_fact_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError("claim_fact_superseded")
        confidences.append(row["confidence"])
    cap = claim_confidence_cap(source_confidences=confidences)
    if confidence_exceeds(claim.confidence, cap):
        raise IntegrityFailureError("claim_confidence_above_cap")
    try:
        connection.execute(
            """
            INSERT INTO self_claims(
                id, created_at, superseded_at, snapshot_id, claim, claim_kind,
                dimension, source_signal_ids_json, source_fact_ids_json,
                confidence, verification_status, counterevidence_json,
                uncertainty, metadata_json, produced_by_run_id, generation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim.id,
                _iso(claim.created_at),
                _iso(claim.superseded_at),
                claim.snapshot_id,
                claim.claim,
                claim.claim_kind,
                claim.dimension,
                _json(claim.source_signal_ids),
                _json(claim.source_fact_ids),
                claim.confidence,
                claim.verification_status,
                _json([item.model_dump(mode="json") for item in claim.counterevidence]),
                claim.uncertainty,
                _json(claim.metadata),
                produced_by_run_id,
                generation_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        if "self_claims.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError("claim_insert_failed") from error


def hydrate_assessment_snapshot(row: sqlite3.Row) -> AssessmentSnapshot:
    try:
        gap_ids = json.loads(row["gap_question_ids_json"])
        contradiction_ids = json.loads(row["contradiction_ids_json"])
        metadata = json.loads(row["metadata_json"])
    except (json.JSONDecodeError, IndexError, TypeError) as error:
        raise HydrationFailureError() from error
    return _hydrate(
        AssessmentSnapshot,
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "superseded_at": row["superseded_at"],
            "scope": row["scope"],
            "scope_target": row["scope_target"],
            "title": row["title"],
            "summary": row["summary"],
            "gap_question_ids": gap_ids,
            "contradiction_ids": contradiction_ids,
            "verification_status": row["verification_status"],
            "metadata": metadata,
        },
    )


def hydrate_self_claim(row: sqlite3.Row) -> SelfClaim:
    try:
        signal_ids = json.loads(row["source_signal_ids_json"])
        fact_ids = json.loads(row["source_fact_ids_json"])
        counterevidence = json.loads(row["counterevidence_json"])
        metadata = json.loads(row["metadata_json"])
    except (json.JSONDecodeError, IndexError, TypeError) as error:
        raise HydrationFailureError() from error
    return _hydrate(
        SelfClaim,
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "superseded_at": row["superseded_at"],
            "snapshot_id": row["snapshot_id"],
            "claim": row["claim"],
            "claim_kind": row["claim_kind"],
            "dimension": row["dimension"],
            "source_signal_ids": signal_ids,
            "source_fact_ids": fact_ids,
            "confidence": row["confidence"],
            "verification_status": row["verification_status"],
            "counterevidence": counterevidence,
            "uncertainty": row["uncertainty"],
            "metadata": metadata,
        },
    )


def list_assessment_snapshots(
    connection: sqlite3.Connection, *, current_only: bool = True
) -> tuple[AssessmentSnapshot, ...]:
    where = " WHERE superseded_at IS NULL" if current_only else ""
    rows = connection.execute("SELECT * FROM assessment_snapshots" + where).fetchall()
    return tuple(
        sorted((hydrate_assessment_snapshot(row) for row in rows), key=lambda item: item.id.encode("utf-8"))
    )


def list_self_claims_for_snapshot(
    connection: sqlite3.Connection,
    snapshot_id: str,
    *,
    current_only: bool = True,
) -> tuple[SelfClaim, ...]:
    current = " AND superseded_at IS NULL" if current_only else ""
    rows = connection.execute(
        "SELECT * FROM self_claims WHERE snapshot_id = ?" + current,
        (snapshot_id,),
    ).fetchall()
    return tuple(
        sorted((hydrate_self_claim(row) for row in rows), key=lambda item: item.id.encode("utf-8"))
    )


def get_assessment_snapshot(
    connection: sqlite3.Connection,
    snapshot_id: str,
    *,
    current_only: bool = True,
) -> AssessmentSnapshot | None:
    current = " AND superseded_at IS NULL" if current_only else ""
    row = connection.execute(
        "SELECT * FROM assessment_snapshots WHERE id = ?" + current,
        (snapshot_id,),
    ).fetchone()
    return None if row is None else hydrate_assessment_snapshot(row)


def _validate_verifier_reference(
    connection: sqlite3.Connection,
    *,
    ref_type: str,
    ref_id: str,
) -> None:
    if ref_type == "experience_fact":
        row = connection.execute(
            "SELECT superseded_at FROM experience_facts WHERE id = ?", (ref_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("finding_counterevidence_fact_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError("finding_counterevidence_fact_superseded")
        return
    if ref_type == "self_signal":
        row = connection.execute(
            "SELECT superseded_at FROM self_signals WHERE id = ?", (ref_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("finding_counterevidence_signal_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError("finding_counterevidence_signal_superseded")
        return
    if ref_type == "evidence_item":
        row = connection.execute(
            "SELECT 1 FROM evidence_items WHERE id = ?", (ref_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("finding_counterevidence_item_missing")
        return
    if ref_type == "raw_log":
        row = connection.execute(
            "SELECT 1 FROM raw_logs WHERE id = ?", (ref_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("finding_counterevidence_log_missing")
        return
    raise IntegrityFailureError("finding_counterevidence_type_invalid")


def insert_verification_finding(
    connection: sqlite3.Connection,
    finding: VerificationFinding,
    *,
    bundle_refs: set[tuple[str, str]] | frozenset[tuple[str, str]],
) -> None:
    if finding.status == "unverified":
        raise IntegrityFailureError("finding_status_unverified")
    if connection.execute(
        "SELECT 1 FROM processing_runs WHERE id = ?", (finding.produced_by_run_id,)
    ).fetchone() is None:
        raise IntegrityFailureError("finding_run_missing")
    if finding.target_type == "resume_bullet":
        raise IntegrityFailureError("finding_resume_bullet_target_unavailable")
    target = connection.execute(
        "SELECT superseded_at FROM self_claims WHERE id = ?", (finding.target_id,)
    ).fetchone()
    if target is None:
        raise IntegrityFailureError("finding_self_claim_target_missing")
    if target["superseded_at"] is not None:
        raise IntegrityFailureError("finding_self_claim_target_superseded")

    pairs = [
        (item.source_ref_type, item.source_ref_id)
        for item in finding.counterevidence
    ]
    if len(pairs) != len(set(pairs)):
        raise IntegrityFailureError("finding_counterevidence_duplicate")
    for pair in pairs:
        if pair not in bundle_refs:
            raise IntegrityFailureError("finding_counterevidence_out_of_bundle")
        _validate_verifier_reference(
            connection, ref_type=pair[0], ref_id=pair[1]
        )
    try:
        connection.execute(
            """
            INSERT INTO verification_findings(
                id, created_at, produced_by_run_id, target_type, target_id,
                status, reason, unsupported_phrases_json, suggested_rewrite,
                counterevidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding.id,
                _iso(finding.created_at),
                finding.produced_by_run_id,
                finding.target_type,
                finding.target_id,
                finding.status,
                finding.reason,
                _json(finding.unsupported_phrases),
                finding.suggested_rewrite,
                _json(
                    [
                        item.model_dump(mode="json")
                        for item in finding.counterevidence
                    ]
                ),
            ),
        )
    except sqlite3.IntegrityError as error:
        if "verification_findings.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError("finding_insert_failed") from error


def hydrate_verification_finding(row: sqlite3.Row) -> VerificationFinding:
    try:
        unsupported_phrases = json.loads(row["unsupported_phrases_json"])
        counterevidence = json.loads(row["counterevidence_json"])
    except (json.JSONDecodeError, IndexError, TypeError) as error:
        raise HydrationFailureError() from error
    return _hydrate(
        VerificationFinding,
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "produced_by_run_id": row["produced_by_run_id"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "status": row["status"],
            "reason": row["reason"],
            "unsupported_phrases": unsupported_phrases,
            "suggested_rewrite": row["suggested_rewrite"],
            "counterevidence": counterevidence,
        },
    )


def list_verification_findings(
    connection: sqlite3.Connection,
    *,
    run_id: str | None = None,
    target_id: str | None = None,
) -> tuple[VerificationFinding, ...]:
    clauses: list[str] = []
    parameters: list[str] = []
    if run_id is not None:
        clauses.append("produced_by_run_id = ?")
        parameters.append(run_id)
    if target_id is not None:
        clauses.append("target_id = ?")
        parameters.append(target_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = connection.execute(
        "SELECT * FROM verification_findings" + where + " ORDER BY CAST(id AS BLOB)",
        parameters,
    ).fetchall()
    return tuple(hydrate_verification_finding(row) for row in rows)


def update_self_claim_verification(
    connection: sqlite3.Connection,
    *,
    claim_id: str,
    verification_status: VerificationStatus,
    counterevidence: Iterable[object],
) -> None:
    payload = [item.model_dump(mode="json") for item in counterevidence]  # type: ignore[attr-defined]
    try:
        cursor = connection.execute(
            """
            UPDATE self_claims
            SET verification_status = ?, counterevidence_json = ?
            WHERE id = ? AND superseded_at IS NULL
            """,
            (verification_status, _json(payload), claim_id),
        )
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError("claim_verification_update_failed") from error
    if cursor.rowcount != 1:
        raise IntegrityFailureError("claim_verification_update_failed")


def update_assessment_snapshot_verification(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    verification_status: VerificationStatus,
) -> None:
    try:
        cursor = connection.execute(
            """
            UPDATE assessment_snapshots SET verification_status = ?
            WHERE id = ? AND superseded_at IS NULL
            """,
            (verification_status, snapshot_id),
        )
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError("snapshot_verification_update_failed") from error
    if cursor.rowcount != 1:
        raise IntegrityFailureError("snapshot_verification_update_failed")


def validate_detection_reference(
    connection: sqlite3.Connection,
    *,
    ref_type: str,
    ref_id: str,
    field: str,
) -> None:
    if ref_type == "experience_fact":
        row = connection.execute(
            "SELECT superseded_at FROM experience_facts WHERE id = ?",
            (ref_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError(f"{field}_experience_fact_missing")
        if row["superseded_at"] is not None:
            raise IntegrityFailureError(f"{field}_experience_fact_superseded")
        return
    elif ref_type == "raw_log":
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM raw_logs AS correction
                WHERE correction.corrects_log_id = target.id
            ) AS displaced
            FROM raw_logs AS target
            WHERE target.id = ?
            """,
            (ref_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError(f"{field}_raw_log_missing")
        if row["displaced"]:
            raise IntegrityFailureError(f"{field}_raw_log_displaced")
        return
    elif ref_type == "evidence_item":
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM raw_logs AS correction
                WHERE correction.corrects_log_id = owner.id
            ) AS owner_displaced
            FROM evidence_items AS item
            JOIN raw_logs AS owner ON owner.id = item.raw_log_id
            WHERE item.id = ?
            """,
            (ref_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError(f"{field}_evidence_item_missing")
        if row["owner_displaced"]:
            raise IntegrityFailureError(f"{field}_evidence_item_owner_displaced")
        return
    else:
        raise IntegrityFailureError(f"{field}_unknown_reference_type")


def insert_gap_question(
    connection: sqlite3.Connection,
    gap: GapQuestion,
    *,
    produced_by_run_id: str,
    generation_id: str,
) -> None:
    if not produced_by_run_id or not generation_id:
        raise IntegrityFailureError("gap_production_identity_invalid")
    if (
        gap.superseded_at is not None
        or gap.answered
        or gap.answer_log_id is not None
    ):
        raise IntegrityFailureError("gap_initial_lifecycle_invalid")
    validate_detection_reference(
        connection,
        ref_type=gap.target_type,
        ref_id=gap.target_id,
        field="gap_target",
    )
    try:
        connection.execute(
            """
            INSERT INTO gap_questions(
                id, created_at, superseded_at, target_type, target_id,
                question, reason, priority, answered, answer_log_id,
                produced_by_run_id, generation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gap.id,
                _iso(gap.created_at),
                _iso(gap.superseded_at),
                gap.target_type,
                gap.target_id,
                gap.question,
                gap.reason,
                gap.priority,
                int(gap.answered),
                gap.answer_log_id,
                produced_by_run_id,
                generation_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        if "gap_questions.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError("gap_insert_failed") from error


def insert_contradiction(
    connection: sqlite3.Connection,
    contradiction: Contradiction,
    *,
    produced_by_run_id: str,
    generation_id: str,
) -> None:
    if not produced_by_run_id or not generation_id:
        raise IntegrityFailureError("contradiction_production_identity_invalid")
    if contradiction.superseded_at is not None:
        raise IntegrityFailureError("contradiction_initial_lifecycle_invalid")
    validate_detection_reference(
        connection,
        ref_type=contradiction.left_ref_type,
        ref_id=contradiction.left_ref_id,
        field="contradiction_left_ref",
    )
    validate_detection_reference(
        connection,
        ref_type=contradiction.right_ref_type,
        ref_id=contradiction.right_ref_id,
        field="contradiction_right_ref",
    )
    try:
        connection.execute(
            """
            INSERT INTO contradictions(
                id, created_at, superseded_at, title, description,
                left_ref_type, left_ref_id, right_ref_type, right_ref_id,
                metadata_json, produced_by_run_id, generation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contradiction.id,
                _iso(contradiction.created_at),
                _iso(contradiction.superseded_at),
                contradiction.title,
                contradiction.description,
                contradiction.left_ref_type,
                contradiction.left_ref_id,
                contradiction.right_ref_type,
                contradiction.right_ref_id,
                _json(contradiction.metadata),
                produced_by_run_id,
                generation_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        if "contradictions.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError("contradiction_insert_failed") from error


def hydrate_gap_question(row: sqlite3.Row) -> GapQuestion:
    if row["answered"] not in (0, 1) or isinstance(row["answered"], bool):
        raise HydrationFailureError()
    payload = {
        "id": row["id"],
        "created_at": row["created_at"],
        "superseded_at": row["superseded_at"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "question": row["question"],
        "reason": row["reason"],
        "priority": row["priority"],
        "answered": bool(row["answered"]),
        "answer_log_id": row["answer_log_id"],
    }
    return _hydrate(GapQuestion, payload)


def hydrate_contradiction(row: sqlite3.Row) -> Contradiction:
    try:
        metadata = json.loads(row["metadata_json"])
    except (json.JSONDecodeError, TypeError) as error:
        raise HydrationFailureError() from error
    payload = {
        "id": row["id"],
        "created_at": row["created_at"],
        "superseded_at": row["superseded_at"],
        "title": row["title"],
        "description": row["description"],
        "left_ref_type": row["left_ref_type"],
        "left_ref_id": row["left_ref_id"],
        "right_ref_type": row["right_ref_type"],
        "right_ref_id": row["right_ref_id"],
        "metadata": metadata,
    }
    return _hydrate(Contradiction, payload)


def list_gap_questions(
    connection: sqlite3.Connection, *, current_only: bool = True
) -> tuple[GapQuestion, ...]:
    where = " WHERE superseded_at IS NULL" if current_only else ""
    rows = connection.execute("SELECT * FROM gap_questions" + where).fetchall()
    gaps = [hydrate_gap_question(row) for row in rows]
    gaps.sort(
        key=lambda item: (
            item.created_at.astimezone(timezone.utc),
            item.id.encode("utf-8"),
        )
    )
    return tuple(gaps)


def list_contradictions(
    connection: sqlite3.Connection, *, current_only: bool = True
) -> tuple[Contradiction, ...]:
    where = " WHERE superseded_at IS NULL" if current_only else ""
    rows = connection.execute("SELECT * FROM contradictions" + where).fetchall()
    contradictions = [hydrate_contradiction(row) for row in rows]
    contradictions.sort(
        key=lambda item: (
            item.created_at.astimezone(timezone.utc),
            item.id.encode("utf-8"),
        )
    )
    return tuple(contradictions)


def get_gap_question(
    connection: sqlite3.Connection,
    gap_id: str,
    *,
    current_only: bool = True,
) -> GapQuestion | None:
    current = " AND superseded_at IS NULL" if current_only else ""
    row = connection.execute(
        "SELECT * FROM gap_questions WHERE id = ?" + current,
        (gap_id,),
    ).fetchone()
    return None if row is None else hydrate_gap_question(row)


def get_contradiction(
    connection: sqlite3.Connection,
    contradiction_id: str,
    *,
    current_only: bool = True,
) -> Contradiction | None:
    current = " AND superseded_at IS NULL" if current_only else ""
    row = connection.execute(
        "SELECT * FROM contradictions WHERE id = ?" + current,
        (contradiction_id,),
    ).fetchone()
    return None if row is None else hydrate_contradiction(row)


def mark_gap_answered(
    connection: sqlite3.Connection,
    *,
    gap_id: str,
    answer_log_id: str,
) -> None:
    try:
        cursor = connection.execute(
            """
            UPDATE gap_questions
            SET answered = 1, answer_log_id = ?
            WHERE id = ? AND superseded_at IS NULL
              AND answered = 0 AND answer_log_id IS NULL
            """,
            (answer_log_id, gap_id),
        )
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError("gap_answer_update_failed") from error
    if cursor.rowcount != 1:
        raise IntegrityFailureError("gap_answer_update_failed")


def _mark_rows_superseded(
    connection: sqlite3.Connection,
    *,
    table: str,
    ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    if superseded_at.tzinfo is None or superseded_at.utcoffset() is None:
        raise IntegrityFailureError()
    values = list(ids)
    if len(values) != len(set(values)):
        raise IntegrityFailureError()
    for entity_id in values:
        row = connection.execute(
            f"SELECT superseded_at FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None or row[0] is not None:
            raise IntegrityFailureError()
    try:
        for entity_id in values:
            cursor = connection.execute(
                f"UPDATE {table} SET superseded_at = ? "
                "WHERE id = ? AND superseded_at IS NULL",
                (_iso(superseded_at), entity_id),
            )
            if cursor.rowcount != 1:
                raise IntegrityFailureError()
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError() from error


def mark_gap_questions_superseded(
    connection: sqlite3.Connection,
    gap_ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    _mark_rows_superseded(
        connection,
        table="gap_questions",
        ids=gap_ids,
        superseded_at=superseded_at,
    )


def mark_contradictions_superseded(
    connection: sqlite3.Connection,
    contradiction_ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    _mark_rows_superseded(
        connection,
        table="contradictions",
        ids=contradiction_ids,
        superseded_at=superseded_at,
    )


def mark_self_signals_superseded(
    connection: sqlite3.Connection,
    signal_ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    _mark_rows_superseded(
        connection,
        table="self_signals",
        ids=signal_ids,
        superseded_at=superseded_at,
    )


def mark_self_claims_superseded(
    connection: sqlite3.Connection,
    claim_ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    _mark_rows_superseded(
        connection,
        table="self_claims",
        ids=claim_ids,
        superseded_at=superseded_at,
    )


def mark_assessment_snapshots_superseded(
    connection: sqlite3.Connection,
    snapshot_ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    _mark_rows_superseded(
        connection,
        table="assessment_snapshots",
        ids=snapshot_ids,
        superseded_at=superseded_at,
    )


def get_raw_log(connection: sqlite3.Connection, log_id: str) -> RawLog | None:
    row = connection.execute("SELECT * FROM raw_logs WHERE id = ?", (log_id,)).fetchone()
    return None if row is None else hydrate_raw_log(row)


def get_evidence_for_log(
    connection: sqlite3.Connection, log_id: str
) -> tuple[EvidenceItem, ...]:
    rows = connection.execute(
        "SELECT * FROM evidence_items WHERE raw_log_id = ?", (log_id,)
    ).fetchall()
    items = [hydrate_evidence_item(row) for row in rows]
    items.sort(key=lambda item: (item.created_at.astimezone(timezone.utc), item.id))
    return tuple(items)


def get_bundle(connection: sqlite3.Connection, log_id: str) -> RawLogBundle | None:
    raw_log = get_raw_log(connection, log_id)
    if raw_log is None:
        return None
    return RawLogBundle(raw_log, get_evidence_for_log(connection, log_id))


def list_raw_logs(connection: sqlite3.Connection) -> tuple[RawLog, ...]:
    rows = connection.execute("SELECT * FROM raw_logs").fetchall()
    logs = [hydrate_raw_log(row) for row in rows]
    logs.sort(key=lambda item: (item.recorded_at.astimezone(timezone.utc), item.id))
    return tuple(logs)
