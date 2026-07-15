"""Raw-log inspection and owner-deletion lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat

from exp2res.domain.models import RawLog
from exp2res.errors import SelectorNotFoundError, WorkspaceBusyError
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
    workspace: Path, *, log_id: str, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> DeleteOutcome:
    residual_paths: list[str] = []
    with writer_database(
        workspace, owner_delete=True, timeout_ms=timeout_ms
    ) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            selected = get_raw_log(connection, log_id)
            if selected is None:
                raise SelectorNotFoundError()
            evidence_ids = tuple(
                item.id for item in get_evidence_for_log(connection, log_id)
            )
            residual_paths.extend(_remove_managed_backups(workspace))
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

        database = workspace / ".exp2res" / "exp2res.sqlite"
        try:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                residual_paths.append(str(database.with_name(database.name + "-wal")))
        except sqlite3.DatabaseError:
            residual_paths.append(str(database.with_name(database.name + "-wal")))

    return DeleteOutcome(
        selected_log=selected,
        evidence_item_ids=evidence_ids,
        residual_paths=tuple(sorted(set(residual_paths))),
    )
