"""Whole-workspace destructive privacy lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable

from exp2res.domain.results import (
    AffectedIds,
    Outcome,
)
from exp2res import __version__
from exp2res.exports.managed import remove_all_managed_output_entries
from exp2res.services.privacy import (
    cancelled_with,
    checkpoint_residuals,
    deletion_outcome,
    generation_ids as generation_ids_of,
    remove_managed_backups,
    table_ids,
    vacuum_residuals,
    wal_path,
)
from exp2res.services.writers import held_writer, operation
from exp2res.storage.schema import PURGE_ENTITY_TABLES, PURGE_TABLE_ORDER
from exp2res.storage.workspace import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_BUSY_TIMEOUT_MS,
    writer_database,
)


@dataclass(frozen=True)
class PurgeOutcome:
    deleted_ids: tuple[tuple[str, tuple[str, ...]], ...]
    generation_ids: tuple[str, ...]
    residual_paths: tuple[str, ...]


def _purge_time(clock: Callable[[], datetime] | None) -> datetime:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("purge clock must be offset-aware")
    return now


def _capture_deleted_ids(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    captured = []
    for table, entity_type in PURGE_ENTITY_TABLES:
        ids = table_ids(connection, table)
        if ids:
            captured.append((entity_type, ids))
    return tuple(captured)


def purge_workspace(
    workspace: Path,
    *,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    connection: sqlite3.Connection | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PurgeOutcome:
    database = workspace / ".exp2res" / "exp2res.sqlite"
    purged = PurgeOutcome(deleted_ids=(), generation_ids=(), residual_paths=())
    journal = None
    held = held_writer(
        connection, writer_database, workspace, owner_delete=True, timeout_ms=timeout_ms
    )
    # §14.14 rule 6: one cancellation boundary over erasure, result
    # construction and teardown; a committed purge is never reported empty.
    try:
        with operation(held) as op:
            journal = op.journal
            connection = op.connection
            # §14.16: managed removal before the purge rows; §13.13 rule 6: no
            # filesystem failure blocks the purge, so it becomes a residual.
            op.remove(
                remove_managed_backups,
                workspace,
                fallback=str((workspace / ".exp2res" / "backup").absolute()),
            )
            op.remove(
                lambda target, removed_ledger: remove_all_managed_output_entries(target),
                workspace,
                fallback=str((workspace / "out").absolute()),
            )
            purged = replace(
                purged,
                deleted_ids=_capture_deleted_ids(connection),
                generation_ids=generation_ids_of(
                    connection,
                    ((table, "", ()) for table in PURGE_TABLE_ORDER),
                    probe_column=True,
                ),
            )
            for table in PURGE_TABLE_ORDER:
                op.execute(f"DELETE FROM {table}")
            op.execute("DELETE FROM schema_meta")
            op.execute(
                """
                INSERT INTO schema_meta(version, applied_at, app_version)
                VALUES (?, ?, ?)
                """,
                (
                    CURRENT_SCHEMA_VERSION,
                    _purge_time(clock).isoformat(),
                    __version__,
                ),
            )
            # §8.1: unproven until the checkpoint/VACUUM sequence completes.
            op.after_commit(
                lambda: _erase(connection, database),
                unproven=(str(database), wal_path(database)),
            )
            purged = replace(purged, residual_paths=op.journal.unresolved)
        return purged
    except KeyboardInterrupt as error:
        journal = getattr(error, "operation_journal", journal)
        if journal is None:
            raise
        if not journal.committed:
            # Pre-transaction cleanup already ran: its unresolved paths survive
            # the cancellation (the v1 envelope has no removed-path field).
            if not journal.residuals:
                raise
            purged = PurgeOutcome(deleted_ids=(), generation_ids=(), residual_paths=())
        raise cancelled_with(
            replace(purged, residual_paths=journal.unresolved)
        ) from None


def _erase(connection: sqlite3.Connection, database: Path) -> tuple[str, ...]:
    # §8.1: checkpoint, VACUUM, checkpoint; every step runs regardless.
    residuals = [
        *checkpoint_residuals(connection, database),
        *vacuum_residuals(connection, database),
    ]
    # §8.1, §14.16: the VACUUM rewrite lives in the WAL, so an untruncated
    # final checkpoint leaves pre-purge bytes in the main database too.
    final_checkpoint = checkpoint_residuals(connection, database)
    if final_checkpoint:
        residuals.extend((*final_checkpoint, str(database)))
    return tuple(residuals)


def purge_affected(purged: PurgeOutcome) -> AffectedIds:
    return AffectedIds.of(deleted=purged.deleted_ids)


def purge_outcome(purged: PurgeOutcome) -> Outcome:
    return deletion_outcome(
        purged.residual_paths,
        affected_ids=purge_affected(purged),
        generation_ids=list(purged.generation_ids),
        result=None,
        # `_run_command` appends the incompleteness claim from the merged set.
        human_result=(
            "Purged the workspace database; the initialized workspace remains."
        ),
    )
