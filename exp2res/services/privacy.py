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


DATABASE_NAME = "exp2res.sqlite"


def purge_managed_backups(
    workspace: Path, *, expected_database: os.stat_result | None = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Remove every regular migration backup and report `(removed, residual)`.

    Enumeration and removal share one `O_NOFOLLOW` directory-descriptor
    boundary, so a symlinked `backup/` root is never traversed: it is
    refused at the open and reported as one residual path (§13.13 rule 6).

    `expected_database` binds this cleanup to the workspace the caller's
    writer authority is actually deleting from. The caller stats its database
    file under the §8.1 writer lock and passes that identity here; if the
    workspace directory was renamed and replaced in between, the `.exp2res`
    reached from the path now holds a different database file, the identities
    disagree, and the store is reported residual instead of a foreign tree
    being purged while the original's backups survive.
    """

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

        def database_is_live() -> bool:
            """Answer whether this marker holds the caller's locked database."""

            if expected_database is None:
                return True
            try:
                current = os.stat(
                    DATABASE_NAME, dir_fd=marker_fd, follow_symlinks=False
                )
            except OSError:
                return False
            return (current.st_dev, current.st_ino) == (
                expected_database.st_dev,
                expected_database.st_ino,
            )

        if not database_is_live():
            return (), (str(backup_root.absolute()),)
        try:
            backup_fd = os.open("backup", directory_flags | no_follow, dir_fd=marker_fd)
        except FileNotFoundError:
            # Nothing to purge, and no descriptor the checks below could use.
            # The caller commits the database deletion next, so a root that
            # appears in between is reported as residual rather than assumed
            # away. §8.1's single business writer — held by this caller — is
            # what bounds the remaining window: no exp2res command can create
            # a backup while this lock is out.
            # Only a second ENOENT confirms absence: any other error means the
            # entry could not be read, which is not evidence that it is gone.
            try:
                os.stat("backup", dir_fd=marker_fd, follow_symlinks=False)
            except FileNotFoundError:
                return (), ()
            except OSError:
                return (), (str(backup_root.absolute()),)
            return (), (str(backup_root.absolute()),)
        descriptors.append(backup_fd)

        def _same_entry(name: str, parent_fd: int, opened_fd: int) -> bool:
            try:
                named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                opened = os.fstat(opened_fd)
            except OSError:
                return False
            return (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)

        def root_is_live() -> bool:
            """Answer whether the open descriptors still are the live store.

            Every scan and unlink below travels through these descriptors, so
            a directory renamed out from under one of them would let the work
            continue in a detached tree while a replacement kept the purged
            vacancy. Each level is therefore matched back to its name before
            removal and again before cleanup is declared complete (§13.14
            rule 6). The chain is anchored at the bottom by the caller's
            locked database rather than by the workspace path, so a workspace
            renamed and replaced under a caller that already holds the writer
            authority is caught here too.
            """

            return (
                _same_entry(".exp2res", workspace_fd, marker_fd)
                and _same_entry("backup", marker_fd, backup_fd)
                and database_is_live()
            )

        if not root_is_live():
            return (), (str(backup_root.absolute()),)

        removed: list[str] = []
        refused: list[str] = []
        with os.scandir(backup_fd) as iterator:
            entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        for entry in entries:
            managed_path = str((backup_root / entry.name).absolute())
            try:
                scanned = os.stat(
                    entry.name, dir_fd=backup_fd, follow_symlinks=False
                )
                if not stat.S_ISREG(scanned.st_mode):
                    refused.append(managed_path)
                    continue
                # Pin the inode before removing it: the open refuses a symlink
                # outright, and the descriptor's own identity is what decides
                # whether the name still holds the file this pass classified
                # (§13.14 rule 6). POSIX unlinks by name and offers no
                # unlink-by-inode, so the check narrows the window to its
                # minimum rather than closing it; the re-enumeration below is
                # what proves the outcome.
                # `O_NONBLOCK` because the name may have become a FIFO since
                # the stat above: a blocking open would wait for a writer that
                # never comes instead of skipping the changed entry.
                entry_fd = os.open(
                    entry.name,
                    os.O_RDONLY | os.O_NONBLOCK | no_follow,
                    dir_fd=backup_fd,
                )
                try:
                    pinned = os.fstat(entry_fd)
                finally:
                    os.close(entry_fd)
                if not stat.S_ISREG(pinned.st_mode) or (
                    pinned.st_dev,
                    pinned.st_ino,
                    pinned.st_nlink,
                ) != (
                    scanned.st_dev,
                    scanned.st_ino,
                    scanned.st_nlink,
                ) or pinned.st_nlink != 1:
                    refused.append(managed_path)
                    continue
                os.unlink(entry.name, dir_fd=backup_fd)
                removed.append(managed_path)
            except OSError:
                refused.append(managed_path)
        if removed:
            # A removal is only durable once the directory entry itself is
            # flushed: the database deletion commits right after this call, so
            # a crash in between could otherwise leave a backup holding the
            # purged vacancy while the envelope claims it was removed. A
            # failed flush is reported rather than assumed (§13.13 rule 6).
            try:
                os.fsync(backup_fd)
            except OSError:
                refused.append(str(backup_root.absolute()))
        # POSIX unlinks by name, so no removal is atomic with the `stat` that
        # classified it: a concurrent rename or recreation can leave a file
        # under a name this pass already visited. Completeness is therefore
        # proven by re-enumeration, not by the first pass — every name still
        # present afterwards is residual and none of them counts as removed
        # (§13.13 rule 6, §13.14 rule 6).
        try:
            with os.scandir(backup_fd) as iterator:
                surviving = {
                    str((backup_root / entry.name).absolute()) for entry in iterator
                }
        except OSError:
            surviving = {str(backup_root.absolute())}
        if not root_is_live():
            # The root moved during the pass, so the surviving-name scan
            # describes a directory that is no longer the workspace's backup
            # store: nothing here counts as proven removal.
            return (), (str(backup_root.absolute()),)
        residuals = sorted({*refused, *surviving}, key=os.fsencode)
        return (
            tuple(path for path in removed if path not in surviving),
            tuple(residuals),
        )
    except OSError:
        return (), (str(backup_root.absolute()),)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def remove_managed_backups(workspace: Path) -> tuple[str, ...]:
    """Remove every regular migration backup, reporting only its residuals."""

    _removed, residuals = purge_managed_backups(workspace)
    return residuals
