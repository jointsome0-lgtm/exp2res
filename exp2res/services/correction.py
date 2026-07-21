"""Atomic correction capture and §13.13 source-change invalidation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable

from pydantic import ValidationError

from exp2res.domain.models import EvidenceItem, OccurredAt, RawLog
from exp2res.domain.results import InvalidatedView, invalidated_view
from exp2res.errors import (
    IdCollisionError,
    InvalidInputError,
    SelectorNotFoundError,
    WorkspaceBusyError,
)
from exp2res.exports.managed import assessment_set_paths, remove_assessment_sets
from exp2res.pipeline.lineage import plan_lineages
from exp2res.pipeline.orchestration import withdraw_pending_unless_superseded
from exp2res.services.capture import new_id, validate_project_label
from exp2res.storage.repository import (
    get_raw_log,
    insert_evidence_item,
    insert_raw_log,
    list_assessment_snapshots,
    list_contradictions,
    list_gap_questions,
    list_self_claims_for_snapshot,
    list_self_signals,
    mark_assessment_snapshots_superseded,
    mark_contradictions_superseded,
    mark_facts_superseded,
    mark_gap_questions_superseded,
    mark_self_claims_superseded,
    mark_self_signals_superseded,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    read_database,
    report_managed_residuals,
    require_compatible,
    writer_database,
)


IdFactory = Callable[[str], str]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class CorrectionOutcome:
    raw_log: RawLog
    evidence_item: EvidenceItem
    superseded_fact_ids: tuple[str, ...]
    superseded_gap_ids: tuple[str, ...]
    superseded_contradiction_ids: tuple[str, ...]
    superseded_signal_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    superseded_snapshot_ids: tuple[str, ...]
    superseded_generation_ids: tuple[str, ...]
    invalidated_views: tuple[InvalidatedView, ...]
    residual_paths: tuple[str, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _capture_error(error: BaseException) -> InvalidInputError:
    failure = InvalidInputError()
    failure.diagnostic_class = "capture_validation_failed"
    failure.public_message = "Correction capture failed strict validation."
    failure.__cause__ = error
    return failure


def validate_correction_selection(workspace: Path, *, log_id: str) -> RawLog:
    """Resolve the retained raw selector before prompts, consent, or adapter work."""

    require_compatible(workspace)
    with read_database(workspace) as connection:
        selected = get_raw_log(connection, log_id)
    if selected is None:
        raise SelectorNotFoundError()
    return selected


def _current_fact_ids(
    connection: sqlite3.Connection, member_ids: tuple[str, ...]
) -> tuple[str, ...]:
    placeholders = ",".join("?" for _ in member_ids)
    rows = connection.execute(
        "SELECT DISTINCT ef.id FROM experience_facts AS ef "
        "JOIN fact_sources AS fs ON fs.fact_id = ef.id "
        "JOIN evidence_items AS ei ON ei.id = fs.evidence_item_id "
        "WHERE ef.superseded_at IS NULL "
        f"AND ei.raw_log_id IN ({placeholders}) "
        "ORDER BY CAST(ef.id AS BLOB)",
        member_ids,
    ).fetchall()
    return tuple(row[0] for row in rows)


def _generation_ids(
    connection: sqlite3.Connection, table_and_ids: tuple[tuple[str, tuple[str, ...]], ...]
) -> tuple[str, ...]:
    values: set[str] = set()
    for table, ids in table_and_ids:
        if not ids:
            continue
        placeholders = ",".join("?" for _ in ids)
        values.update(
            row[0]
            for row in connection.execute(
                f"SELECT DISTINCT generation_id FROM {table} "
                f"WHERE id IN ({placeholders})",
                ids,
            )
        )
    return tuple(sorted(values, key=_id_key))


def capture_correction(
    workspace: Path,
    *,
    log_id: str,
    raw_text: str,
    occurred: OccurredAt,
    project: str | None,
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> CorrectionOutcome:
    """Commit correction + complete current-graph invalidation in one transaction."""

    require_compatible(workspace)
    validate_project_label(project)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    last_collision: IdCollisionError | None = None

    with writer_database(workspace, timeout_ms=timeout_ms) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            target = get_raw_log(connection, log_id)
            if target is None:
                raise SelectorNotFoundError()
            context = plan_lineages(connection, log_id=log_id)[0]
            superseded_fact_ids = _current_fact_ids(connection, context.member_ids)
            gaps = list_gap_questions(connection)
            contradictions = list_contradictions(connection)
            signals = list_self_signals(connection)
            snapshots = list_assessment_snapshots(connection)
            superseded_gap_ids = tuple(item.id for item in gaps)
            superseded_contradiction_ids = tuple(item.id for item in contradictions)
            superseded_signal_ids = tuple(item.id for item in signals)
            superseded_snapshot_ids = tuple(item.id for item in snapshots)
            superseded_claim_ids = tuple(
                claim.id
                for snapshot in snapshots
                for claim in list_self_claims_for_snapshot(connection, snapshot.id)
            )
            invalidated_views = tuple(
                invalidated_view(
                    scope=snapshot.scope,
                    scope_target=snapshot.scope_target,
                    snapshot_id=snapshot.id,
                )
                for snapshot in snapshots
            )
            superseded_generation_ids = _generation_ids(
                connection,
                (
                    ("experience_facts", superseded_fact_ids),
                    ("gap_questions", superseded_gap_ids),
                    ("contradictions", superseded_contradiction_ids),
                    ("self_signals", superseded_signal_ids),
                    ("self_claims", superseded_claim_ids),
                    ("assessment_snapshots", superseded_snapshot_ids),
                ),
            )

            raw_log: RawLog | None = None
            evidence_item: EvidenceItem | None = None
            for attempt in range(3):
                raw_id = id_factory("raw_log")
                evidence_id = id_factory("evidence_item")
                try:
                    raw_log = RawLog(
                        id=raw_id,
                        recorded_at=now,
                        entry_type="correction",
                        source_type="manual_entry",
                        occurred=occurred,
                        raw_text=raw_text,
                        project=project,
                        external_ref=None,
                        corrects_log_id=target.id,
                        metadata={},
                    )
                    evidence_item = EvidenceItem(
                        id=evidence_id,
                        created_at=now,
                        raw_log_id=raw_id,
                        title=None,
                        summary="Owner-authored manual claim.",
                        uri=None,
                        path=None,
                        strength="manual_claim",
                        metadata={},
                    )
                except (ValidationError, ValueError, TypeError) as error:
                    raise _capture_error(error) from error
                savepoint = f"correction_{attempt}"
                connection.execute(f"SAVEPOINT {savepoint}")
                try:
                    insert_raw_log(connection, raw_log)
                    insert_evidence_item(connection, evidence_item)
                except IdCollisionError as error:
                    connection.execute(f"ROLLBACK TO {savepoint}")
                    connection.execute(f"RELEASE {savepoint}")
                    last_collision = error
                    continue
                connection.execute(f"RELEASE {savepoint}")
                break
            else:
                raise IdCollisionError() from last_collision

            assert raw_log is not None and evidence_item is not None
            mark_facts_superseded(connection, superseded_fact_ids, now)
            mark_gap_questions_superseded(connection, superseded_gap_ids, now)
            mark_contradictions_superseded(
                connection, superseded_contradiction_ids, now
            )
            mark_self_signals_superseded(connection, superseded_signal_ids, now)
            mark_self_claims_superseded(connection, superseded_claim_ids, now)
            mark_assessment_snapshots_superseded(
                connection, superseded_snapshot_ids, now
            )
            # Pre-commit pending report (same pattern as the Stage 3-7
            # trigger sites): an interrupt in the commit-to-cleanup window
            # still reports the stale sets; a proven rollback withdraws.
            pending_stale_paths = assessment_set_paths(
                workspace, superseded_snapshot_ids
            )
            report_managed_residuals(pending_stale_paths)
            try:
                connection.commit()
            except BaseException:
                withdraw_pending_unless_superseded(
                    connection, pending_stale_paths, superseded_snapshot_ids
                )
                raise
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except BaseException:
            connection.rollback()
            raise

        residual_paths = remove_assessment_sets(workspace, superseded_snapshot_ids)

    return CorrectionOutcome(
        raw_log=raw_log,
        evidence_item=evidence_item,
        superseded_fact_ids=superseded_fact_ids,
        superseded_gap_ids=tuple(sorted(superseded_gap_ids, key=_id_key)),
        superseded_contradiction_ids=tuple(
            sorted(superseded_contradiction_ids, key=_id_key)
        ),
        superseded_signal_ids=tuple(sorted(superseded_signal_ids, key=_id_key)),
        superseded_claim_ids=tuple(sorted(superseded_claim_ids, key=_id_key)),
        superseded_snapshot_ids=tuple(
            sorted(superseded_snapshot_ids, key=_id_key)
        ),
        superseded_generation_ids=superseded_generation_ids,
        invalidated_views=tuple(
            sorted(invalidated_views, key=lambda item: _id_key(item.snapshot_id))
        ),
        residual_paths=residual_paths,
    )
