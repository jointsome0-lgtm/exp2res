"""Raw-log inspection and owner-deletion lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from exp2res.domain.canonical import id_key
from exp2res.domain.models import RawLog
from exp2res.domain.results import (
    InvalidatedBranch,
    InvalidatedView,
    invalidated_branch,
    invalidated_view,
)
from exp2res.errors import SelectorNotFoundError
from exp2res.exports.managed import remove_all_managed_output_entries
from exp2res.services.privacy import (
    cancelled_with,
    checkpoint_residuals as _delete_checkpoint_residuals,
    remove_managed_backups as _remove_managed_backups,
    sorted_paths,
    table_ids,
    wal_path,
)
from exp2res.services.writers import business_transaction, held_writer
from exp2res.storage.repository import (
    RawLogBundle,
    get_bundle,
    get_evidence_for_log,
    get_raw_log,
    list_raw_logs,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    read_database,
    writer_database,
)


@dataclass(frozen=True)
class DeleteOutcome:
    selected_log: RawLog
    evidence_item_ids: tuple[str, ...]
    purged_fact_ids: tuple[str, ...]
    purged_gap_ids: tuple[str, ...]
    purged_contradiction_ids: tuple[str, ...]
    purged_finding_ids: tuple[str, ...]
    purged_claim_ids: tuple[str, ...]
    purged_snapshot_ids: tuple[str, ...]
    purged_branch_ids: tuple[str, ...]
    purged_bullet_ids: tuple[str, ...]
    purged_generation_ids: tuple[str, ...]
    invalidated_views: tuple[InvalidatedView, ...]
    invalidated_branches: tuple[InvalidatedBranch, ...]
    residual_paths: tuple[str, ...]


_PURGED_TABLES = (
    "experience_facts",
    "gap_questions",
    "contradictions",
    "verification_findings",
    "self_claims",
    "assessment_snapshots",
    "resume_branches",
    "resume_bullets",
)


def list_logs(
    workspace: Path, *, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> tuple[RawLog, ...]:
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        return list_raw_logs(connection)


def show_log(
    workspace: Path, *, log_id: str, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> RawLogBundle:
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        bundle = get_bundle(connection, log_id)
        if bundle is None:
            raise SelectorNotFoundError()
        return bundle


def delete_log(
    workspace: Path,
    *,
    log_id: str,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    connection: sqlite3.Connection | None = None,
) -> DeleteOutcome:
    residual_paths: list[str] = []
    # §8.1: `logs delete` passes the owner-delete authority it holds across rebuild.
    with held_writer(
        connection, writer_database, workspace, owner_delete=True, timeout_ms=timeout_ms
    ) as connection:
        with business_transaction(connection):
            selected = get_raw_log(connection, log_id)
            if selected is None:
                raise SelectorNotFoundError()
            # §14.14 rule 5: report order is stable identity, not §13.1 order.
            evidence_ids = tuple(
                sorted(
                    (item.id for item in get_evidence_for_log(connection, log_id)),
                    key=id_key,
                )
            )
            purged = {table: table_ids(connection, table) for table in _PURGED_TABLES}
            snapshot_rows = connection.execute(
                "SELECT id, scope FROM assessment_snapshots "
                "WHERE superseded_at IS NULL ORDER BY CAST(id AS BLOB)"
            ).fetchall()
            invalidated_views = tuple(
                invalidated_view(scope=row["scope"], snapshot_id=row["id"])
                for row in snapshot_rows
            )
            # §13.13 rule 9: command output only, never persisted.
            invalidated_branches = tuple(
                invalidated_branch(
                    name=row["name"],
                    job_description_id=row["job_description_id"],
                    scope=row["scope"],
                    snapshot_id=row["assessment_snapshot_id"],
                )
                for row in connection.execute(
                    "SELECT branch.name, branch.job_description_id, "
                    "branch.assessment_snapshot_id, snapshot.scope "
                    "FROM resume_branches AS branch "
                    "JOIN assessment_snapshots AS snapshot "
                    "ON snapshot.id = branch.assessment_snapshot_id "
                    "WHERE branch.superseded_at IS NULL "
                    "ORDER BY CAST(branch.name AS BLOB)"
                )
            )
            purged_generation_ids = tuple(
                sorted(
                    {
                        row[0]
                        for table in _PURGED_TABLES
                        if table != "verification_findings"
                        for row in connection.execute(
                            f"SELECT DISTINCT generation_id FROM {table}"
                        )
                    },
                    key=id_key,
                )
            )
            residual_paths.extend(_remove_managed_backups(workspace))
            # §13.13 rule 5: every managed entry goes before the privacy purge;
            # the database deletion commits regardless.
            residual_paths.extend(remove_all_managed_output_entries(workspace))
            # §13.13 rule 5: purge before the raw_logs delete so answer_log_id's
            # ON DELETE SET NULL never fires into the answered-iff CHECK.
            connection.execute("DELETE FROM verification_findings")
            # Bullets and branches go before the snapshots their FK names.
            connection.execute("DELETE FROM resume_bullets")
            connection.execute("DELETE FROM resume_branches")
            connection.execute("DELETE FROM self_claims")
            connection.execute("DELETE FROM assessment_snapshots")
            connection.execute("DELETE FROM gap_questions")
            connection.execute("DELETE FROM contradictions")
            connection.execute("DELETE FROM experience_facts")
            connection.execute(
                "UPDATE llm_calls SET input_hash = NULL, output_hash = NULL"
            )
            connection.execute("DELETE FROM raw_logs WHERE id = ?", (log_id,))

        def build_outcome(residuals: tuple[str, ...]) -> DeleteOutcome:
            return DeleteOutcome(
                selected_log=selected,
                evidence_item_ids=evidence_ids,
                purged_fact_ids=purged["experience_facts"],
                purged_gap_ids=purged["gap_questions"],
                purged_contradiction_ids=purged["contradictions"],
                purged_finding_ids=purged["verification_findings"],
                purged_claim_ids=purged["self_claims"],
                purged_snapshot_ids=purged["assessment_snapshots"],
                purged_branch_ids=purged["resume_branches"],
                purged_bullet_ids=purged["resume_bullets"],
                purged_generation_ids=purged_generation_ids,
                invalidated_views=invalidated_views,
                invalidated_branches=invalidated_branches,
                residual_paths=sorted_paths(residuals),
            )

        database = workspace / ".exp2res" / "exp2res.sqlite"
        try:
            residual_paths.extend(
                _delete_checkpoint_residuals(connection, database)
            )
        except KeyboardInterrupt:
            # §14.14 rule 6: already committed; the WAL stays residual until a
            # later writer proves erasure.
            raise cancelled_with(
                delete_outcome=build_outcome((*residual_paths, wal_path(database)))
            ) from None

        return build_outcome(tuple(residual_paths))
