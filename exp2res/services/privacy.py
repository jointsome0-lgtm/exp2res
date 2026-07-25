"""Shared no-follow managed cleanup and SQLite erasure helpers."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat


def checkpoint_residuals(
    connection: sqlite3.Connection, database: Path
) -> tuple[str, ...]:
    """Run the required truncating checkpoint and report its WAL on failure."""

    wal_path = str(database.with_name(database.name + "-wal"))
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return () if checkpoint is not None and checkpoint[0] == 0 else (wal_path,)
    except sqlite3.DatabaseError:
        return (wal_path,)


def vacuum_residuals(
    connection: sqlite3.Connection, database: Path
) -> tuple[str, ...]:
    """Run purge VACUUM outside a transaction and report the live database."""

    try:
        connection.execute("VACUUM")
        return ()
    except sqlite3.DatabaseError:
        return (str(database),)


def remove_managed_backups(workspace: Path) -> tuple[str, ...]:
    """Remove every regular migration backup without following any symlink."""

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
            return ()
        descriptors.append(backup_fd)

        residuals: list[str] = []
        with os.scandir(backup_fd) as iterator:
            entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        for entry in entries:
            managed_path = str((backup_root / entry.name).absolute())
            try:
                entry_mode = os.stat(
                    entry.name, dir_fd=backup_fd, follow_symlinks=False
                ).st_mode
                if stat.S_ISREG(entry_mode) and not stat.S_ISLNK(entry_mode):
                    os.unlink(entry.name, dir_fd=backup_fd)
                else:
                    residuals.append(managed_path)
            except OSError:
                residuals.append(managed_path)
        return tuple(residuals)
    except OSError:
        return (str(backup_root.absolute()),)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
