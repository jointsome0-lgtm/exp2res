"""Atomic correction capture and §13.13 source-change invalidation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable

from exp2res.config import load_workspace_config
from exp2res.domain.canonical import byte_sorted, id_key
from exp2res.domain.models import EvidenceItem, OccurredAt, RawLog
from exp2res.domain.results import (
    InvalidatedBranch,
    InvalidatedView,
    invalidated_view,
)
from exp2res.errors import SelectorNotFoundError
from exp2res.exports.managed import (
    assessment_set_paths,
    branch_set_paths,
    remove_managed_sets_for_locked_database,
)
from exp2res.pipeline.branch_lifecycle import supersede_current_branches
from exp2res.pipeline.lineage import plan_lineages
from exp2res.pipeline.orchestration import withdraw_pending_unless_superseded
from exp2res.services.capture import (
    build_capture_pair,
    new_id,
    validate_project_label,
)
from exp2res.services.privacy import cancelled_with, generation_ids
from exp2res.services.source_files import (
    authorize_artifact_locators,
    read_capture_file,
)
from exp2res.services.writers import (
    held_writer,
    operation,
    retry_id_collisions,
    savepoint,
)
from exp2res.storage.repository import (
    get_raw_log,
    insert_evidence_item,
    insert_raw_log,
    list_assessment_snapshots,
    list_contradictions,
    list_gap_questions,
    list_self_claims_for_snapshot,
    mark_assessment_snapshots_superseded,
    mark_contradictions_superseded,
    mark_facts_superseded,
    mark_gap_questions_superseded,
    mark_self_claims_superseded,
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
    evidence_items: tuple[EvidenceItem, ...]
    superseded_fact_ids: tuple[str, ...]
    superseded_gap_ids: tuple[str, ...]
    superseded_contradiction_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    superseded_snapshot_ids: tuple[str, ...]
    superseded_branch_ids: tuple[str, ...]
    superseded_bullet_ids: tuple[str, ...]
    superseded_generation_ids: tuple[str, ...]
    invalidated_views: tuple[InvalidatedView, ...]
    invalidated_branches: tuple[InvalidatedBranch, ...]
    residual_paths: tuple[str, ...]


def validate_correction_selection(workspace: Path, *, log_id: str) -> RawLog:
    require_compatible(workspace)
    with read_database(workspace) as connection:
        selected = get_raw_log(connection, log_id)
    if selected is None:
        raise SelectorNotFoundError()
    return selected


def read_correction_source(
    workspace: Path, *, source_path: str
) -> tuple[str, str | None]:
    # §14.2 gates, read outside the writer lock. No timezone check here: a
    # correction copying the target's placement uses no local-time feature
    # (§14.14 rule 8 applies to the explicit temporal-replacement branch only).
    require_compatible(workspace)
    config = load_workspace_config(workspace)
    return read_capture_file(source_path, config=config)


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


def capture_correction(
    workspace: Path,
    *,
    log_id: str,
    raw_text: str,
    occurred: OccurredAt,
    project: str | None,
    external_ref: str | None = None,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    connection: sqlite3.Connection | None = None,
) -> CorrectionOutcome:
    require_compatible(workspace)
    validate_project_label(project)
    authorized_artifacts = authorize_artifact_locators(
        artifacts, config=load_workspace_config(workspace)
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    pending_stale_paths: tuple[str, ...] = ()
    superseded_snapshot_ids: tuple[str, ...] = ()
    captured: CorrectionOutcome | None = None

    # §8.1: `correction add` passes the writer authority it holds across rebuild.
    held = held_writer(connection, writer_database, workspace, timeout_ms=timeout_ms)
    try:
        # Pending report before commit: the commit-to-cleanup window still
        # reports the stale sets; a proven rollback withdraws.
        with operation(
            held,
            on_rollback=lambda: withdraw_pending_unless_superseded(
                connection, pending_stale_paths, superseded_snapshot_ids
            ),
        ) as op:
            connection = op.connection
            target = get_raw_log(connection, log_id)
            if target is None:
                raise SelectorNotFoundError()
            context = plan_lineages(connection, log_id=log_id)[0]
            superseded_fact_ids = _current_fact_ids(connection, context.member_ids)
            gaps = list_gap_questions(connection)
            contradictions = list_contradictions(connection)
            snapshots = list_assessment_snapshots(connection)
            superseded_gap_ids = tuple(item.id for item in gaps)
            superseded_contradiction_ids = tuple(item.id for item in contradictions)
            superseded_snapshot_ids = tuple(item.id for item in snapshots)
            superseded_claim_ids = tuple(
                claim.id
                for snapshot in snapshots
                for claim in list_self_claims_for_snapshot(connection, snapshot.id)
            )
            invalidated_views = tuple(
                invalidated_view(
                    scope=snapshot.scope, snapshot_id=snapshot.id
                )
                for snapshot in snapshots
            )
            superseded_generation_ids = generation_ids(
                connection,
                (
                    (table, f"id IN ({','.join('?' for _ in ids)})", tuple(ids))
                    for table, ids in (
                        ("experience_facts", superseded_fact_ids),
                        ("gap_questions", superseded_gap_ids),
                        ("contradictions", superseded_contradiction_ids),
                        ("self_claims", superseded_claim_ids),
                        ("assessment_snapshots", superseded_snapshot_ids),
                    )
                    if ids
                ),
            )

            def attempt(index: int) -> tuple[RawLog, tuple[EvidenceItem, ...]]:
                raw_log, evidence_items = build_capture_pair(
                    recorded_at=now,
                    artifacts=authorized_artifacts,
                    id_factory=id_factory,
                    message="Correction capture failed strict validation.",
                    entry_type="correction",
                    source_type="manual_entry",
                    occurred=occurred,
                    raw_text=raw_text,
                    project=project,
                    # §14.4/§29.4: the authorized real path, file form only.
                    external_ref=external_ref,
                    corrects_log_id=target.id,
                    metadata={},
                )

                def insert() -> None:
                    insert_raw_log(connection, raw_log)
                    for evidence_item in evidence_items:
                        insert_evidence_item(connection, evidence_item)

                savepoint(connection, f"correction_{index}", insert)
                return raw_log, evidence_items

            raw_log, evidence_items = retry_id_collisions(attempt)
            mark_facts_superseded(connection, superseded_fact_ids, now)
            mark_gap_questions_superseded(connection, superseded_gap_ids, now)
            mark_contradictions_superseded(
                connection, superseded_contradiction_ids, now
            )
            # §13.13 rule 4: branches and bullets go before their anchoring
            # snapshots stop being current.
            branch_swap = supersede_current_branches(connection, superseded_at=now)
            superseded_generation_ids = byte_sorted(
                {*superseded_generation_ids, *branch_swap.superseded_generation_ids}
            )
            mark_self_claims_superseded(connection, superseded_claim_ids, now)
            mark_assessment_snapshots_superseded(
                connection, superseded_snapshot_ids, now
            )
            pending_stale_paths = (
                *assessment_set_paths(workspace, superseded_snapshot_ids),
                *branch_set_paths(workspace, branch_swap.branch_ids),
            )
            report_managed_residuals(pending_stale_paths)
            captured = CorrectionOutcome(
                raw_log=raw_log,
                evidence_items=evidence_items,
                superseded_fact_ids=superseded_fact_ids,
                superseded_gap_ids=byte_sorted(superseded_gap_ids),
                superseded_contradiction_ids=byte_sorted(superseded_contradiction_ids),
                superseded_claim_ids=byte_sorted(superseded_claim_ids),
                superseded_snapshot_ids=byte_sorted(superseded_snapshot_ids),
                superseded_branch_ids=branch_swap.branch_ids,
                superseded_bullet_ids=branch_swap.bullet_ids,
                superseded_generation_ids=superseded_generation_ids,
                invalidated_views=tuple(
                    sorted(
                        invalidated_views,
                        key=lambda item: id_key(item.snapshot_id),
                    )
                ),
                invalidated_branches=branch_swap.invalidated_branches,
                residual_paths=(),
            )
            # §14.14 rule 5: a pending set the interrupted cleanup already
            # unlinked must not travel as though retained.
            op.after_commit(
                lambda: remove_managed_sets_for_locked_database(
                    workspace,
                    snapshot_ids=superseded_snapshot_ids,
                    branch_ids=branch_swap.branch_ids,
                    removed_ledger=op.journal.unlinks,
                ),
                unproven=pending_stale_paths,
            )
            captured = replace(captured, residual_paths=op.journal.unresolved)
        return captured
    except KeyboardInterrupt as error:
        # §14.14 rule 6: already committed, so the cancellation carries it.
        if not error.operation_journal.committed or captured is None:
            raise
        raise cancelled_with(
            replace(captured, residual_paths=error.operation_journal.unresolved)
        ) from None
