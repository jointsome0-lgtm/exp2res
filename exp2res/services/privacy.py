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
    """Name the managed-output roots §13.14 rule 9's binding has to cover.

    They live here rather than beside the export code that owns their contract
    because this module is what the writer lock reaches to record identities,
    and the export module reads them back from here — one definition, imported
    in the direction that already exists.
    """

    out = workspace / "out"
    return (out, out / "assessment", out / "branch")


def backup_root_path(workspace: Path) -> Path:
    """Name the migration-backup store the writer lock also covers."""

    return workspace / ".exp2res" / "backup"


def locked_tree_paths(workspace: Path) -> tuple[Path, ...]:
    """Name every directory whose identity the writer lock establishes.

    The managed-output roots plus the backup store: each is re-resolved by name
    at every removal, and each holds owner data a substitution would leave
    behind while the pass reported success against a replacement.
    """

    return (*managed_root_paths(workspace), backup_root_path(workspace))


def locked_database_identity_at(marker_fd: int) -> os.stat_result | None:
    """Read the identity of the database this open `.exp2res` entry holds.

    Taking the identity through a descriptor rather than a pathname is what
    lets `writer_lock` anchor the workspace it actually locked: the lock file
    and the database are opened through one `.exp2res` entry, so a workspace
    renamed and replaced between the two cannot substitute one for the other.
    """

    try:
        current = os.stat(DATABASE_NAME, dir_fd=marker_fd, follow_symlinks=False)
    except OSError:
        return None
    return current if stat.S_ISREG(current.st_mode) else None


def locked_database_identity(workspace: Path) -> os.stat_result | None:
    """Read the identity of the database this pathname currently holds.

    §13.14 rule 9's anchor: a caller takes this while it holds the §8.1 writer
    lock, then passes it to `workspace_database_is_live` before removing
    anything, so a workspace renamed and replaced in between is refused rather
    than having a foreign tree cleaned while this workspace's own sets survive.

    Anchor and comparison share one no-follow walk so that the two can only
    ever disagree about the bytes. Following a symlink here would let a
    `.exp2res` pointed back at the renamed tree answer for a database the
    surrounding `out/` no longer belongs to, which is the substitution the
    check exists to catch. An unreadable or non-conforming path yields `None`,
    which rule 9 never treats as permission to remove.

    A non-regular final entry is one of those non-conforming paths. `os.stat`
    with `follow_symlinks=False` answers happily about a symlink, and a
    database file replaced by one after SQLite opened the original would
    otherwise become the anchor: every later read finds the same symlink, so
    the comparison would authorize cleanup of whatever tree that name reaches
    while the mutations stay in the original open file.
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
def anchor_locked_database(workspace: Path) -> Iterator[None]:
    """Anchor §13.14 rule 9's identity for the span of one held writer lock.

    The anchor belongs to the lock, not to the frame that wants to clean up.
    A stage handed an already-open connection runs arbitrarily long after the
    lock was taken — a whole LLM invocation, in the §13 stages — so an identity
    it read for itself could describe a workspace already renamed and replaced,
    and would then authorize cleanup of the replacement while every mutation
    stayed in the original open database. `writer_lock` establishes the value
    once, where the authority is acquired, and every cleanup below it reads
    that one.

    Outside a held lock the anchor is absent, which rule 9 treats as a refusal
    rather than as permission: a removal that cannot name the database it
    belongs to is exactly the removal the rule exists to stop.
    """

    with anchor_locked_database_identity(locked_database_identity(workspace)):
        with anchor_locked_tree_identities(locked_tree_paths(workspace)):
            yield


@contextmanager
def anchor_locked_database_identity(
    identity: os.stat_result | None,
) -> Iterator[None]:
    """Anchor an identity the caller already read through its own descriptor.

    `writer_lock` reads the database beside the lock file it holds rather than
    beside the pathname it was given. A workspace renamed and replaced between
    the lock and this anchor would otherwise install the replacement's database
    as the identity every later check compares against, so the replacement
    would answer "still live" to a command holding none of its authority.
    """

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
    """Record what the given pathnames reach at the moment the lock is taken.

    The database anchor answers whether the pathname still reaches the same
    database, and nothing else. The directories the managed paths are built
    from are re-resolved by name at every mutation, so one of them renamed and
    replaced beside an untouched database is indistinguishable from the one the
    command committed against — no later check can tell them apart, because
    both answer to the same name and neither is the database. Recording the
    identity before a substitution could happen is what makes them distinct,
    and the lock is the only moment early enough to be sure of it.

    A pathname that is absent here is recorded as nothing rather than as an
    identity: the reserved parents are created under this same lock, so their
    first appearance is this command's own work and binds from there.
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
    """Record the entry this command created under the lock it holds.

    A root absent when the lock was taken is established by the holding
    command's own creation step and by nothing else. Adopting whatever answers
    to the name at the next check would hand the binding to any entry that
    appeared meanwhile, which is the substitution the record exists to catch —
    one level down and with the command's own authority behind it.

    The caller supplies the identity rather than the pathname alone, because a
    pathname restatted here would answer about whatever holds the name by then;
    the creating step held the entry open and knows which one it made.
    """

    identities = _LOCKED_TREE_IDENTITIES.get()
    if identities is None:
        return
    identities[str(path)] = identity


def locked_tree_identities_established() -> bool:
    """Answer whether a lock recorded what the managed pathnames reached."""

    return _LOCKED_TREE_IDENTITIES.get() is not None


def locked_tree_identity(path: Path) -> tuple[int, int] | None:
    """Read the identity established for one pathname under the held lock.

    Nothing is recorded for a pathname that was absent when the lock was taken
    and that this command has not created since, which is the same answer as
    for a pathname no lock covers: not established, and so never something this
    command may mutate.
    """

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
    """Report residual paths no later existence check may withdraw.

    §14.14's envelope assembly drops a reported residual whose path is gone,
    because the ordinary reason for that is a later step in the same command
    completing the invalidation. §13.14 rule 9's mismatch arm breaks that
    reasoning: it names sets stranded in the workspace the mutation committed
    to, spelled through a pathname that now reaches a different one, so their
    absence there is a fact about the replacement and no evidence at all about
    the sets. Those reports come through here instead and are never withdrawn.
    """

    sink = _UNPROVEN_RESIDUALS.get()
    if sink is not None:
        sink.extend(paths)


def workspace_database_is_live(
    workspace: Path, expected_database: os.stat_result | None
) -> bool:
    """Answer whether this pathname still holds the caller's locked database.

    The same anchor `purge_managed_backups` applies to its own tree, for the
    callers that clean managed output rather than backups. `None` — the caller
    could not establish the anchor at all — is never treated as a match. The
    residual window in `purge_managed_backups`'s docstring applies here
    unchanged.
    """

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

    A store the lock recorded never reads as absence here: it was moved, and
    it still holds the backups this purge was required to remove, so it is
    reported residual rather than treated as nothing left to do.

    A pass that loses the binding partway reports nothing removed, here and in
    the ledger alike. The names are pathnames, and after the substitution they
    reach whatever holds them now: reporting them as removed would tell the
    owner that files still sitting untouched in the tree the command is now
    looking at had been deleted.

    `removed_ledger` receives each name as it is unlinked. The return value is
    only produced once the pass finishes, so a caller that must report durable
    effects after a cancellation mid-pass has no other way to learn what this
    function already removed (§14.14 rule 6).

    Residual window this cannot close: `expected_database` is an identity taken
    from a pathname, and POSIX offers no way to ask an open SQLite connection
    for the inode it holds. A workspace renamed and replaced in the instant
    between the caller's connection open and its stat is therefore outside what
    this check can prove. §8.1's single business writer and §29's local
    boundary — anything able to rename the workspace root is already inside it
    — are what bound that window.
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
            """Answer whether the workspace pathname still names this root.

            The chain below matches each level back to its parent, and the
            topmost level has no parent descriptor to be matched against — so
            a rename of the workspace root itself is invisible to it. Every
            path this function reports is built from the pathname, so without
            this the pass would unlink the detached original store while
            naming files in the untouched replacement.
            """

            try:
                named = os.stat(workspace, follow_symlinks=False)
                opened = os.fstat(workspace_fd)
            except OSError:
                return False
            return (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)

        def backup_is_established() -> bool:
            """Answer whether this store is the entry the lock recorded.

            Matching the descriptor back to its name proves only that the name
            still reaches it, which a replacement satisfies as readily as the
            original. A caller that binds its database binds this too: without
            it, a store renamed aside after the lock would have its replacement
            emptied and reported purged while the owner data this command was
            deleting survived in the detached original.
            """

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
                if not root_is_live():
                    refused.append(managed_path)
                    break
                os.unlink(entry.name, dir_fd=backup_fd)
                # A removal is only durable once the directory entry itself is
                # failed flush is reported rather than assumed (§13.13 rule 6).
                os.fsync(backup_fd)
                removed.append(managed_path)
                if removed_ledger is not None:
                    removed_ledger.append(managed_path)
            except OSError:
                refused.append(managed_path)
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
    """Remove every regular migration backup, reporting only its residuals.

    §13.14 rule 9 binds this like every other managed removal, and supplies the
    anchor from the held writer lock rather than from the caller — the same
    identity `jd delete` already passes explicitly for its partial purge. An
    anchor that could not be established is a refusal, so the store is reported
    residual rather than a replacement's backups being purged while this
    workspace's own survive.
    """

    expected_database = locked_database_anchor()
    if expected_database is None:
        return (str((workspace / ".exp2res" / "backup").absolute()),)
    _removed, residuals = purge_managed_backups(
        workspace, expected_database=expected_database
    )
    return residuals
