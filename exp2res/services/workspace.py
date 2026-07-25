"""Whole-workspace destructive privacy lifecycle."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Callable

from exp2res import __version__
from exp2res.errors import OperationCancelledError, WorkspaceBusyError
from exp2res.exports.managed import remove_all_managed_output_entries
from exp2res.services.privacy import (
    checkpoint_residuals,
    remove_managed_backups,
    vacuum_residuals,
)
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


def _sorted_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(paths), key=os.fsencode))


def _capture_deleted_ids(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    captured = []
    for table, entity_type in PURGE_ENTITY_TABLES:
        ids = tuple(
            row[0]
            for row in connection.execute(
                f"SELECT id FROM {table} ORDER BY CAST(id AS BLOB)"
            )
        )
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
    return tuple(sorted(generation_ids, key=lambda value: value.encode("utf-8")))


def purge_workspace(
    workspace: Path,
    *,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    connection: sqlite3.Connection | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PurgeOutcome:
    """Remove all managed workspace content while retaining initialization."""

    residual_paths: list[str] = []
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, owner_delete=True, timeout_ms=timeout_ms)
    )
    with held as connection:
        # §14.16: enumerate and attempt every managed removal before the one
        # database purge transaction. The managed-output helper covers final,
        # candidate, rollback, and other entries under both reserved parents.
        # §13.13 rule 6: no filesystem failure may block the database purge, so
        # an error the helpers cannot classify still becomes a residual path.
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
                residual_paths=_sorted_paths(
                    [*residual_paths, *extra_residuals]
                ),
            )

        try:
            connection.execute("BEGIN IMMEDIATE")
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
            connection.commit()
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except BaseException:
            connection.rollback()
            raise

        # §8.1: checkpoint, VACUUM outside any transaction, then checkpoint
        # again. Each step is attempted even if an earlier erasure step reports
        # incomplete, because the committed privacy deletion is never restored.
        # §14.14 rule 6: the whole post-commit region is one cancellation
        # boundary — an interrupt between two erasure steps must still carry
        # the committed deletion and the unproven paths, never an empty
        # cancelled envelope.
        try:
            residual_paths.extend(checkpoint_residuals(connection, database))
            residual_paths.extend(vacuum_residuals(connection, database))
            residual_paths.extend(checkpoint_residuals(connection, database))
        except KeyboardInterrupt:
            cancelled = OperationCancelledError()
            cancelled.purge_outcome = outcome(
                (
                    str(database),
                    str(database.with_name(database.name + "-wal")),
                )
            )
            raise cancelled from None

        return outcome()
