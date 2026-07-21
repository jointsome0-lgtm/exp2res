"""Raw-log inspection and owner-deletion lifecycle."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat

from exp2res.domain.models import RawLog
from exp2res.domain.results import InvalidatedView, invalidated_view
from exp2res.errors import (
    OperationCancelledError,
    SelectorNotFoundError,
    WorkspaceBusyError,
)
from exp2res.exports.managed import remove_all_managed_output_entries
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
    purged_signal_ids: tuple[str, ...]
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


def _delete_checkpoint_residuals(
    connection: sqlite3.Connection, database: Path
) -> tuple[str, ...]:
    wal_path = str(database.with_name(database.name + "-wal"))
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return () if checkpoint is not None and checkpoint[0] == 0 else (wal_path,)
    except sqlite3.DatabaseError:
        return (wal_path,)


def _remove_managed_backups(workspace: Path) -> list[str]:
    backup_root = workspace / ".exp2res" / "backup"
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        workspace_fd = os.open(workspace, directory_flags | no_follow)
        descriptors.append(workspace_fd)
        marker_fd = os.open(
            ".exp2res", directory_flags | no_follow, dir_fd=workspace_fd
        )
        descriptors.append(marker_fd)
        try:
            backup_fd = os.open("backup", directory_flags | no_follow, dir_fd=marker_fd)
        except FileNotFoundError:
            return []
        descriptors.append(backup_fd)

        residual: list[str] = []
        with os.scandir(backup_fd) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name.encode("utf-8"))
        for entry in entries:
            managed_path = str((backup_root / entry.name).absolute())
            try:
                entry_mode = os.stat(
                    entry.name, dir_fd=backup_fd, follow_symlinks=False
                ).st_mode
                if stat.S_ISREG(entry_mode) and not stat.S_ISLNK(entry_mode):
                    os.unlink(entry.name, dir_fd=backup_fd)
                else:
                    residual.append(managed_path)
            except OSError:
                residual.append(managed_path)
        return residual
    except OSError:
        return [str(backup_root.absolute())]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


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
            evidence_ids = tuple(
                item.id for item in get_evidence_for_log(connection, log_id)
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
            purged_signal_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM self_signals ORDER BY CAST(id AS BLOB)"
                )
            )
            purged_finding_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT id FROM verification_findings ORDER BY CAST(id AS BLOB)"
                )
            )
            snapshot_rows = connection.execute(
                "SELECT id, scope, scope_target FROM assessment_snapshots "
                "WHERE superseded_at IS NULL ORDER BY CAST(id AS BLOB)"
            ).fetchall()
            invalidated_views = tuple(
                invalidated_view(
                    scope=row["scope"],
                    scope_target=row["scope_target"],
                    snapshot_id=row["id"],
                )
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
                            "self_signals",
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
            # §13.13 rule 5: detections and signals are generated prose and
            # leave with the facts; purging before the raw_logs delete keeps the
            # answer_log_id ON DELETE SET NULL action from firing into the
            # gap_questions answered-iff CHECK.
            connection.execute("DELETE FROM verification_findings")
            connection.execute("DELETE FROM self_claims")
            connection.execute("DELETE FROM assessment_snapshots")
            connection.execute("DELETE FROM gap_questions")
            connection.execute("DELETE FROM contradictions")
            connection.execute("DELETE FROM self_signals")
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
                purged_signal_ids=purged_signal_ids,
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
