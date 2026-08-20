"""Shared no-follow managed cleanup and SQLite erasure helpers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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


def managed_root_paths(workspace: Path) -> tuple[Path, ...]:
    """Managed-output roots §13.14 rule 9 binds (defined here: the lock imports this, not exports)."""

    out = workspace / "out"
    return (out, out / "assessment", out / "branch")


def backup_root_path(workspace: Path) -> Path:
    """Name the migration-backup store the writer lock also covers."""

    return workspace / ".exp2res" / "backup"


def locked_tree_paths(workspace: Path) -> tuple[Path, ...]:
    """Every directory whose identity the writer lock establishes."""

    return (*managed_root_paths(workspace), backup_root_path(workspace))


def locked_database_identity_at(marker_fd: int) -> os.stat_result | None:
    """Identity of the database behind an open `.exp2res` descriptor — the one the lock actually holds."""

    try:
        current = os.stat(DATABASE_NAME, dir_fd=marker_fd, follow_symlinks=False)
    except OSError:
        return None
    return current if stat.S_ISREG(current.st_mode) else None


def locked_database_identity(workspace: Path) -> os.stat_result | None:
    """§13.14 rule 9 anchor: identity of the database this pathname holds, by no-follow walk.

    Anchor and comparison share this walk; `None` (unreadable, symlink,
    non-regular) is never permission to remove.
    """

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    workspace_fd: int | None = None
    marker_fd: int | None = None
    try:
        workspace_fd = os.open(workspace, directory_flags | no_follow)
        marker_fd = os.open(".exp2res", directory_flags | no_follow, dir_fd=workspace_fd)
        return locked_database_identity_at(marker_fd)
    except OSError:
        return None
    finally:
        for descriptor in (marker_fd, workspace_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


_LOCKED_DATABASE_IDENTITY: ContextVar[os.stat_result | None] = ContextVar(
    "exp2res_locked_database_identity", default=None
)


@contextmanager
def anchor_locked_database_identity(
    identity: os.stat_result | None,
) -> Iterator[None]:
    """Anchor an identity the caller read through its own lock-held descriptor."""

    token = _LOCKED_DATABASE_IDENTITY.set(identity)
    try:
        yield
    finally:
        _LOCKED_DATABASE_IDENTITY.reset(token)


def locked_database_anchor() -> os.stat_result | None:
    """Read the identity anchored when the held writer lock was acquired."""

    return _LOCKED_DATABASE_IDENTITY.get()


_LOCKED_TREE_IDENTITIES: ContextVar[dict[str, tuple[int, int]] | None] = ContextVar(
    "exp2res_locked_tree_identities", default=None
)


@contextmanager
def anchor_locked_tree_identities(paths: Iterable[Path]) -> Iterator[None]:
    """Record what the managed pathnames reach when the lock is taken.

    A root replaced beside an untouched database is only distinguishable by an
    identity recorded before the swap; an absent root is recorded as nothing
    and binds at this command's own creation step.
    """

    identities: dict[str, tuple[int, int]] = {}
    for path in paths:
        try:
            info = os.stat(path, follow_symlinks=False)
        except OSError:
            continue
        identities[str(path)] = (info.st_dev, info.st_ino)
    token = _LOCKED_TREE_IDENTITIES.set(identities)
    try:
        yield
    finally:
        _LOCKED_TREE_IDENTITIES.reset(token)


def record_locked_tree_identity(path: Path, identity: tuple[int, int]) -> None:
    """Record the entry this command created under its lock (caller passes the identity it made, not a restat)."""

    identities = _LOCKED_TREE_IDENTITIES.get()
    if identities is None:
        return
    identities[str(path)] = identity


def locked_tree_identities_established() -> bool:
    """Answer whether a lock recorded what the managed pathnames reached."""

    return _LOCKED_TREE_IDENTITIES.get() is not None


def locked_tree_identity(path: Path) -> tuple[int, int] | None:
    """Identity established for one pathname under the held lock; `None` = never mutable."""

    identities = _LOCKED_TREE_IDENTITIES.get()
    if identities is None:
        return None
    return identities.get(str(path))


_UNPROVEN_RESIDUALS: ContextVar[list[str] | None] = ContextVar(
    "exp2res_unproven_residuals", default=None
)


@contextmanager
def collect_unproven_residuals(residuals: list[str]) -> Iterator[None]:
    """Collect residuals whose own pathname cannot testify about them."""

    token = _UNPROVEN_RESIDUALS.set(residuals)
    try:
        yield
    finally:
        _UNPROVEN_RESIDUALS.reset(token)


def report_unproven_residual(paths) -> None:
    """Report residuals §14.14's gone-path withdrawal must not drop.

    §13.14 rule 9's mismatch arm names sets stranded behind a pathname that
    now reaches a different workspace, so their absence there proves nothing.
    """

    sink = _UNPROVEN_RESIDUALS.get()
    if sink is not None:
        sink.extend(paths)


def workspace_database_is_live(
    workspace: Path, expected_database: os.stat_result | None
) -> bool:
    """Whether this pathname still holds the caller's locked database; `None` never matches."""

    if expected_database is None:
        return False
    current = locked_database_identity(workspace)
    if current is None:
        return False
    return (current.st_dev, current.st_ino) == (
        expected_database.st_dev,
        expected_database.st_ino,
    )


def purge_managed_backups(
    workspace: Path,
    *,
    expected_database: os.stat_result | None = None,
    removed_ledger: list[str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Remove every regular migration backup; report `(removed, residual)`.

    One `O_NOFOLLOW` descriptor boundary for enumeration and removal: a
    symlinked root is refused as one residual (§13.13 rule 6).
    `expected_database` (stat'd by the caller under the §8.1 lock) binds the
    pass to its workspace; a binding lost mid-pass reports nothing removed,
    ledger included, since the names now reach a replacement tree.
    `removed_ledger` receives each unlink as it happens so a cancellation
    mid-pass can still report durable effects (§14.14 rule 6).
    Unclosable window: POSIX cannot ask an open SQLite connection for its
    inode, so a swap between connection open and stat is bounded only by
    §8.1's single writer and §29's local boundary.
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
            # Only a second ENOENT confirms absence; any other error is not
            # evidence the entry is gone, so it reports residual.
            recorded_absent = (
                expected_database is None or locked_tree_identity(backup_root) is None
            )
            try:
                os.stat("backup", dir_fd=marker_fd, follow_symlinks=False)
            except FileNotFoundError:
                if recorded_absent:
                    return (), ()
                return (), (str(backup_root.absolute()),)
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

        def workspace_is_named() -> bool:
            # The topmost level has no parent descriptor to match against, so
            # a workspace-root rename needs this explicit check.

            try:
                named = os.stat(workspace, follow_symlinks=False)
                opened = os.fstat(workspace_fd)
            except OSError:
                return False
            return (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)

        def backup_is_established() -> bool:
            # Name-to-descriptor matching is satisfied by a replacement too;
            # only the lock-recorded identity tells the original apart.

            if expected_database is None:
                return True
            recorded = locked_tree_identity(backup_root)
            if recorded is None:
                return False
            try:
                opened = os.fstat(backup_fd)
            except OSError:
                return False
            return (opened.st_dev, opened.st_ino) == recorded

        def root_is_live() -> bool:
            # Each level matched back to its name before removal and again
            # before completion (§13.14 rule 6), anchored at the locked database.

            return (
                workspace_is_named()
                and _same_entry(".exp2res", workspace_fd, marker_fd)
                and _same_entry("backup", marker_fd, backup_fd)
                and backup_is_established()
                and database_is_live()
            )

        if not root_is_live():
            return (), (str(backup_root.absolute()),)

        removed: list[str] = []
        refused: list[str] = []
        ledger_mark = 0 if removed_ledger is None else len(removed_ledger)
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
                # Pin the inode before unlinking by name (§13.14 rule 6);
                # `O_NONBLOCK` in case the name became a FIFO since the stat.
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
                if not root_is_live():
                    refused.append(managed_path)
                    break
                os.unlink(entry.name, dir_fd=backup_fd)
                # A removal is durable only once the directory is flushed (§13.13 rule 6).
                os.fsync(backup_fd)
                removed.append(managed_path)
                if removed_ledger is not None:
                    removed_ledger.append(managed_path)
            except OSError:
                refused.append(managed_path)
        # Completeness is proven by re-enumeration, not by the first pass
        # (§13.13 rule 6, §13.14 rule 6).
        try:
            with os.scandir(backup_fd) as iterator:
                surviving = {
                    str((backup_root / entry.name).absolute()) for entry in iterator
                }
        except OSError:
            surviving = {str(backup_root.absolute())}
        if not root_is_live():
            # Root moved mid-pass: the survivors scan describes another directory.
            if removed_ledger is not None:
                del removed_ledger[ledger_mark:]
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
    """Remove every regular migration backup under the lock's §13.14 rule 9 anchor; report residuals."""

    expected_database = locked_database_anchor()
    if expected_database is None:
        return (str((workspace / ".exp2res" / "backup").absolute()),)
    _removed, residuals = purge_managed_backups(
        workspace, expected_database=expected_database
    )
    return residuals
