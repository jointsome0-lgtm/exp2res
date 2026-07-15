"""§8/§12.14 workspace lifecycle, compatibility, and writer locking."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import time
from typing import Callable, Iterator
from urllib.parse import quote

from exp2res import __version__
from exp2res.errors import (
    PublicCheckoutError,
    SchemaCompatibilityError,
    WorkspaceBusyError,
    WorkspaceError,
)

from .schema import create_schema

CURRENT_SCHEMA_VERSION = 1
DEFAULT_BUSY_TIMEOUT_MS = 5_000
CONFIG_TEMPLATE = """[workspace]
timezone = ""

[privacy]
ignore_paths = []
"""


@dataclass(frozen=True)
class SchemaStatus:
    stored_version: int | None
    supported_version: int
    recognized: bool
    compatible: bool
    migration_path_available: bool | None
    managed_backup_path: str | None = None


def _entry_kind(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def _is_real_directory(path: Path) -> bool:
    mode = _entry_kind(path)
    return mode is not None and stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _is_real_file(path: Path) -> bool:
    mode = _entry_kind(path)
    return mode is not None and stat.S_ISREG(mode) and not stat.S_ISLNK(mode)


def discover_workspace(*, cwd: Path, override: str | None = None) -> Path:
    base = cwd.resolve(strict=True)
    if override is not None:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            root = candidate.resolve(strict=True)
        except OSError as error:
            raise WorkspaceError() from error
        if not root.is_dir() or not _is_real_directory(root / ".exp2res"):
            raise WorkspaceError()
        return root

    for candidate in (base, *base.parents):
        marker_mode = _entry_kind(candidate / ".exp2res")
        if marker_mode is not None:
            return candidate
    raise WorkspaceError()


def _database_uri(path: Path, *, readonly: bool) -> str:
    suffix = "?mode=ro" if readonly else "?mode=rw"
    return f"file:{quote(str(path), safe='/')}{suffix}"


def connect_database(
    database: Path,
    *,
    readonly: bool,
    owner_delete: bool = False,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            _database_uri(database, readonly=readonly),
            uri=True,
            timeout=max(busy_timeout_ms, 0) / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "exp2res_owner_delete", 0, lambda: 1 if owner_delete else 0
        )
        connection.execute(f"PRAGMA busy_timeout = {max(busy_timeout_ms, 0)}")
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise sqlite3.DatabaseError("foreign key enforcement unavailable")
        connection.execute("PRAGMA secure_delete = ON")
        if connection.execute("PRAGMA secure_delete").fetchone()[0] != 1:
            raise sqlite3.DatabaseError("secure delete unavailable")
        if not readonly:
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise sqlite3.DatabaseError("full synchronous mode unavailable")
        return connection
    except sqlite3.OperationalError as error:
        if connection is not None:
            connection.close()
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            raise WorkspaceBusyError() from error
        raise
    except BaseException:
        if connection is not None:
            connection.close()
        raise


def _aware_iso(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def inspect_schema(connection: sqlite3.Connection) -> SchemaStatus:
    try:
        rows = connection.execute(
            "SELECT version, applied_at, app_version FROM schema_meta ORDER BY version"
        ).fetchall()
    except sqlite3.DatabaseError:
        return SchemaStatus(None, CURRENT_SCHEMA_VERSION, False, False, None)
    if not rows:
        return SchemaStatus(None, CURRENT_SCHEMA_VERSION, False, False, None)
    versions: list[int] = []
    for row in rows:
        version, applied_at, app_version = row
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
            or not _aware_iso(applied_at)
            or not isinstance(app_version, str)
            or not app_version
        ):
            return SchemaStatus(None, CURRENT_SCHEMA_VERSION, False, False, None)
        versions.append(version)
    if versions != sorted(set(versions)):
        return SchemaStatus(None, CURRENT_SCHEMA_VERSION, False, False, None)
    stored = max(versions)
    compatible = stored == CURRENT_SCHEMA_VERSION
    migration_available = False if stored < CURRENT_SCHEMA_VERSION else None
    return SchemaStatus(
        stored,
        CURRENT_SCHEMA_VERSION,
        True,
        compatible,
        migration_available,
    )


def inspect_workspace(workspace: Path) -> SchemaStatus:
    marker = workspace / ".exp2res"
    database = marker / "exp2res.sqlite"
    required_layout = (
        _is_real_directory(marker)
        and _is_real_file(database)
        and _is_real_file(marker / "config.toml")
        and _is_real_file(marker / "lock")
        and _is_real_directory(workspace / "out")
    )
    if not required_layout:
        return SchemaStatus(None, CURRENT_SCHEMA_VERSION, False, False, None)
    try:
        connection = connect_database(database, readonly=True)
        try:
            return inspect_schema(connection)
        finally:
            connection.close()
    except (sqlite3.DatabaseError, OSError):
        return SchemaStatus(None, CURRENT_SCHEMA_VERSION, False, False, None)


def require_compatible(workspace: Path) -> SchemaStatus:
    status = inspect_workspace(workspace)
    if not status.recognized or not status.compatible:
        raise SchemaCompatibilityError()
    return status


def _is_public_checkout(target: Path) -> bool:
    return (
        _entry_kind(target / ".git") is not None
        and (target / "SDD.md").is_file()
        and (target / "spec").is_dir()
    )


def _write_private_file(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short private-file write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def initialize_workspace(
    target: Path, *, clock: Callable[[], datetime] | None = None
) -> tuple[Path, SchemaStatus, bool]:
    target = target.resolve(strict=True)
    if _is_public_checkout(target):
        raise PublicCheckoutError()
    marker_mode = _entry_kind(target / ".exp2res")
    if marker_mode is not None:
        status = inspect_workspace(target)
        if not status.recognized or not status.compatible:
            raise SchemaCompatibilityError()
        try:
            os.chmod(target / "out", 0o700)
        except OSError as error:
            raise SchemaCompatibilityError() from error
        return target, status, False

    out = target / "out"
    out_created = False
    if _entry_kind(out) is None:
        out.mkdir(mode=0o700)
        out_created = True
    elif not _is_real_directory(out):
        raise SchemaCompatibilityError()
    try:
        os.chmod(out, 0o700)
    except OSError as error:
        raise SchemaCompatibilityError() from error

    marker = target / ".exp2res"
    database = marker / "exp2res.sqlite"
    marker_created = False
    try:
        try:
            marker.mkdir(mode=0o700)
        except FileExistsError as error:
            # A concurrent creator owns this marker now; treat it like any
            # other pre-existing partial workspace and never remove it.
            raise SchemaCompatibilityError() from error
        marker_created = True
        os.chmod(marker, 0o700)
        _write_private_file(marker / "config.toml", CONFIG_TEMPLATE.encode("utf-8"))
        _write_private_file(marker / "lock", b"")
        _write_private_file(database, b"")
        connection = connect_database(database, readonly=False)
        try:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise sqlite3.DatabaseError("WAL mode unavailable")
            now = (clock or (lambda: datetime.now(timezone.utc)))()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("initialization clock must be offset-aware")
            try:
                create_schema(
                    connection,
                    version=CURRENT_SCHEMA_VERSION,
                    applied_at=now.isoformat(),
                    app_version=__version__,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()
        for candidate in (database, database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")):
            if candidate.exists():
                os.chmod(candidate, 0o600)
        status = inspect_workspace(target)
        if not status.compatible:
            raise sqlite3.DatabaseError("fresh schema failed compatibility")
        return target, status, True
    except BaseException:
        if marker_created and _is_real_directory(marker):
            shutil.rmtree(marker)
        if out_created and _is_real_directory(out):
            shutil.rmtree(out)
        raise


@contextmanager
def writer_lock(workspace: Path, *, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> Iterator[None]:
    lock_path = workspace / ".exp2res" / "lock"
    if not _is_real_file(lock_path):
        raise SchemaCompatibilityError()
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags)
    except OSError as error:
        raise SchemaCompatibilityError() from error
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise WorkspaceBusyError() from error
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def writer_database(
    workspace: Path,
    *,
    owner_delete: bool = False,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Iterator[sqlite3.Connection]:
    # The first compatibility gate precedes lock acquisition and write I/O.
    require_compatible(workspace)
    with writer_lock(workspace, timeout_ms=timeout_ms):
        connection = connect_database(
            workspace / ".exp2res" / "exp2res.sqlite",
            readonly=False,
            owner_delete=owner_delete,
            busy_timeout_ms=timeout_ms,
        )
        try:
            # Re-establish compatibility while holding the writer authority and
            # before opening the business transaction.
            status = inspect_schema(connection)
            if not status.compatible:
                raise SchemaCompatibilityError()
            yield connection
        finally:
            connection.close()


@contextmanager
def read_database(
    workspace: Path, *, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> Iterator[sqlite3.Connection]:
    if not (
        _is_real_directory(workspace / ".exp2res")
        and _is_real_file(workspace / ".exp2res" / "exp2res.sqlite")
        and _is_real_file(workspace / ".exp2res" / "config.toml")
        and _is_real_file(workspace / ".exp2res" / "lock")
        and _is_real_directory(workspace / "out")
    ):
        raise SchemaCompatibilityError()
    connection = connect_database(
        workspace / ".exp2res" / "exp2res.sqlite",
        readonly=True,
        busy_timeout_ms=timeout_ms,
    )
    try:
        connection.execute("BEGIN")
        status = inspect_schema(connection)
        if not status.compatible:
            raise SchemaCompatibilityError()
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
