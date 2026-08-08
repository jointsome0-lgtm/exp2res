"""Raw-log inspection and owner-deletion lifecycle."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3

from exp2res.domain.models import RawLog
from exp2res.domain.results import InvalidatedView, invalidated_view
from exp2res.errors import (
    OperationCancelledError,
    SelectorNotFoundError,
    WorkspaceBusyError,
)
from exp2res.exports.managed import remove_all_managed_output_entries
from exp2res.services.privacy import (
    checkpoint_residuals as _delete_checkpoint_residuals,
    remove_managed_backups as _remove_managed_backups,
)
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
    purged_generation_ids: tuple[str, ...]
    invalidated_views: tuple[InvalidatedView, ...]
    residual_paths: tuple[str, ...]


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
    # §8.1: `logs delete` holds one owner-delete writer authority across the
    # purge and its §13.13 rule 5 rebuild and passes it here; a direct call
    # still acquires its own.
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, owner_delete=True, timeout_ms=timeout_ms)
    )
    with held as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            selected = get_raw_log(connection, log_id)
            if selected is None:
                raise SelectorNotFoundError()
            # §14.14 rule 5 orders reported ID groups by stable identity, not
            # by §13.1's presentation order for a record's evidence bundle.
            evidence_ids = tuple(
                sorted(
                    (item.id for item in get_evidence_for_log(connection, log_id)),
                    key=lambda value: value.encode("utf-8"),
                )
            )
            purged_fact_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM experience_facts ORDER BY CAST(id AS BLOB)"
                )
            )
            purged_gap_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM gap_questions ORDER BY CAST(id AS BLOB)"
                )
            )
            purged_contradiction_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM contradictions ORDER BY CAST(id AS BLOB)"
                )
            )
            purged_finding_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM verification_findings ORDER BY CAST(id AS BLOB)"
                )
            )
            snapshot_rows = connection.execute(
                "SELECT id, scope FROM assessment_snapshots "
                "WHERE superseded_at IS NULL ORDER BY CAST(id AS BLOB)"
            ).fetchall()
            invalidated_views = tuple(
                invalidated_view(scope=row["scope"], snapshot_id=row["id"])
                for row in snapshot_rows
            )
            purged_claim_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM self_claims ORDER BY CAST(id AS BLOB)"
                )
            )
            purged_snapshot_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM assessment_snapshots ORDER BY CAST(id AS BLOB)"
                )
            )
            purged_generation_ids = tuple(
                sorted(
                    {
                        row[0]
                        for table in (
                            "experience_facts",
                            "gap_questions",
                            "contradictions",
                            "self_claims",
                            "assessment_snapshots",
                        )
                        for row in connection.execute(
                            f"SELECT DISTINCT generation_id FROM {table}"
                        )
                    },
                    key=lambda value: value.encode("utf-8"),
                )
            )
            residual_paths.extend(_remove_managed_backups(workspace))
            # §13.13 rule 5: owner deletion attempts every final, candidate,
            # rollback, or other entry under both reserved managed parents
            # before the privacy purge; database deletion still commits.
            residual_paths.extend(remove_all_managed_output_entries(workspace))
            # §13.13 rule 5: detections and claims are generated prose and
            # leave with the facts; purging before the raw_logs delete keeps the
            # answer_log_id ON DELETE SET NULL action from firing into the
            # gap_questions answered-iff CHECK.
            connection.execute("DELETE FROM verification_findings")
            connection.execute("DELETE FROM self_claims")
            connection.execute("DELETE FROM assessment_snapshots")
            connection.execute("DELETE FROM gap_questions")
            connection.execute("DELETE FROM contradictions")
            connection.execute("DELETE FROM experience_facts")
            connection.execute(
                "UPDATE llm_calls SET input_hash = NULL, output_hash = NULL"
            )
            connection.execute("DELETE FROM raw_logs WHERE id = ?", (log_id,))
            connection.commit()
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except BaseException:
            connection.rollback()
            raise

        def build_outcome(residuals: tuple[str, ...]) -> DeleteOutcome:
            return DeleteOutcome(
                selected_log=selected,
                evidence_item_ids=evidence_ids,
                purged_fact_ids=purged_fact_ids,
                purged_gap_ids=purged_gap_ids,
                purged_contradiction_ids=purged_contradiction_ids,
                purged_finding_ids=purged_finding_ids,
                purged_claim_ids=purged_claim_ids,
                purged_snapshot_ids=purged_snapshot_ids,
                purged_generation_ids=purged_generation_ids,
                invalidated_views=invalidated_views,
                residual_paths=tuple(sorted(set(residuals), key=os.fsencode)),
            )

        database = workspace / ".exp2res" / "exp2res.sqlite"
        try:
            residual_paths.extend(
                _delete_checkpoint_residuals(connection, database)
            )
        except KeyboardInterrupt:
            # §14.14 rule 6: the privacy purge committed before checkpoint
            # work, so cancellation carries the complete durable deletion and
            # treats the WAL as residual until a later writer proves erasure.
            cancelled = OperationCancelledError()
            cancelled.delete_outcome = build_outcome(
                (
                    *residual_paths,
                    str(database.with_name(database.name + "-wal")),
                )
            )
            raise cancelled from None

        return build_outcome(tuple(residual_paths))
