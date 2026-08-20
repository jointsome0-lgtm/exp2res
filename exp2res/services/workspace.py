"""Whole-workspace destructive privacy lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable

from exp2res.domain.canonical import id_key
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
    remove_managed_backups,
    sorted_paths,
    table_ids,
    vacuum_residuals,
    wal_path,
)
from exp2res.services.writers import banked_transaction, held_writer
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


def _capture_generation_ids(connection: sqlite3.Connection) -> tuple[str, ...]:
    generation_ids: set[str] = set()
    for table in PURGE_TABLE_ORDER:
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if "generation_id" not in columns:
            continue
        generation_ids.update(
            row[0]
            for row in connection.execute(
                f"SELECT DISTINCT generation_id FROM {table}"
            )
            if row[0] is not None
        )
    return tuple(sorted(generation_ids, key=id_key))


def purge_workspace(
    workspace: Path,
    *,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    connection: sqlite3.Connection | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PurgeOutcome:
    residual_paths: list[str] = []
    held = held_writer(
        connection, writer_database, workspace, owner_delete=True, timeout_ms=timeout_ms
    )
    # §14.14 rule 6: one cancellation boundary over erasure, result
    # construction and teardown; a committed purge is never reported empty.
    committed: list[PurgeOutcome] = []
    try:
        return _purge_locked(
            workspace,
            held=held,
            residual_paths=residual_paths,
            committed=committed,
            clock=clock,
        )
    except KeyboardInterrupt:
        if committed:
            interrupted = committed[-1]
        elif residual_paths:
            # Pre-transaction cleanup already ran: its unresolved paths survive
            # the cancellation (the v1 envelope has no removed-path field).
            interrupted = PurgeOutcome(
                deleted_ids=(),
                generation_ids=(),
                residual_paths=sorted_paths(residual_paths),
            )
        else:
            raise
        raise cancelled_with(purge_outcome=interrupted) from None


def _purge_locked(
    workspace: Path,
    *,
    held,
    residual_paths: list[str],
    committed: list[PurgeOutcome],
    clock: Callable[[], datetime] | None,
) -> PurgeOutcome:
    with held as connection:
        # §14.16: managed removal before the purge transaction; §13.13 rule 6:
        # no filesystem failure blocks the purge, so it becomes a residual.
        for remove, fallback in (
            (remove_managed_backups, workspace / ".exp2res" / "backup"),
            (remove_all_managed_output_entries, workspace / "out"),
        ):
            try:
                residual_paths.extend(remove(workspace))
            except OSError:
                residual_paths.append(str(fallback.absolute()))

        database = workspace / ".exp2res" / "exp2res.sqlite"
        deleted_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()
        generation_ids: tuple[str, ...] = ()

        def outcome(extra_residuals: tuple[str, ...] = ()) -> PurgeOutcome:
            return PurgeOutcome(
                deleted_ids=deleted_ids,
                generation_ids=generation_ids,
                residual_paths=sorted_paths([*residual_paths, *extra_residuals]),
            )

        # §8.1: unproven until the checkpoint/VACUUM sequence completes.
        unproven = (str(database), wal_path(database))
        with banked_transaction(
            connection, lambda: committed.append(outcome(unproven))
        ):
            deleted_ids = _capture_deleted_ids(connection)
            generation_ids = _capture_generation_ids(connection)
            for table in PURGE_TABLE_ORDER:
                connection.execute(f"DELETE FROM {table}")
            connection.execute("DELETE FROM schema_meta")
            connection.execute(
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

        # §8.1: checkpoint, VACUUM, checkpoint; every step runs regardless.
        residual_paths.extend(checkpoint_residuals(connection, database))
        residual_paths.extend(vacuum_residuals(connection, database))
        # §8.1, §14.16: the VACUUM rewrite lives in the WAL, so an untruncated
        # final checkpoint leaves pre-purge bytes in the main database too.
        final_checkpoint = checkpoint_residuals(connection, database)
        if final_checkpoint:
            residual_paths.extend((*final_checkpoint, str(database)))

        final = outcome()
        committed.append(final)
        return final


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
