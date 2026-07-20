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
    MigrationFailedError,
    MigrationInterrupted,
    PublicCheckoutError,
    SchemaCompatibilityError,
    WorkspaceBusyError,
    WorkspaceError,
)

from .schema import (
    SCHEMA_V4_SQL,
    apply_migration_1_to_2,
    apply_migration_2_to_3,
    apply_migration_3_to_4,
    create_schema,
)

CURRENT_SCHEMA_VERSION = 4
DEFAULT_BUSY_TIMEOUT_MS = 5_000
CONFIG_TEMPLATE = """[workspace]
timezone = ""

[llm]
adapter = "codex-cli"
model = "gpt-5.6-sol"
codex_home_env = "CODEX_HOME"
claude_config_dir_env = "CLAUDE_CONFIG_DIR"
reasoning_effort = "high"
transport_attempt_cap = 2
backoff_lower_seconds = 0.25
backoff_upper_seconds = 2.0
invocation_deadline_seconds = 120.0
max_input_bytes = 1048576
input_token_budget = 120000
output_token_budget = 8192
per_run_call_ceiling = 100
per_invocation_cost_ceiling = 5.0
per_run_cost_ceiling = 25.0

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


@dataclass(frozen=True)
class MigrationStep:
    from_version: int
    to_version: int
    apply: Callable[[sqlite3.Connection], None]
    requires_foreign_keys_off: bool = False


MIGRATION_REGISTRY = (
    MigrationStep(1, 2, apply_migration_1_to_2),
    MigrationStep(2, 3, apply_migration_2_to_3, requires_foreign_keys_off=True),
    MigrationStep(3, 4, apply_migration_3_to_4),
)


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
    migration_available = (
        _migration_path_available(stored)
        if stored < CURRENT_SCHEMA_VERSION
        else None
    )
    return SchemaStatus(
        stored,
        CURRENT_SCHEMA_VERSION,
        True,
        compatible,
        migration_available,
    )


def _migration_path_available(stored_version: int) -> bool:
    return _migration_path(stored_version) is not None


def _migration_path(stored_version: int) -> tuple[MigrationStep, ...] | None:
    version = stored_version
    path: list[MigrationStep] = []
    while version < CURRENT_SCHEMA_VERSION:
        matches = [
            step for step in MIGRATION_REGISTRY if step.from_version == version
        ]
        if len(matches) != 1:
            return None
        step = matches[0]
        if step.to_version <= step.from_version:
            return None
        path.append(step)
        version = step.to_version
    return tuple(path) if version == CURRENT_SCHEMA_VERSION else None


def migration_available(stored_version: int) -> bool:
    """Return whether the registered migrations reach the current schema."""

    return (
        stored_version < CURRENT_SCHEMA_VERSION
        and _migration_path_available(stored_version)
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
    # The target arrives canonicalized; any containing engine checkout makes
    # every directory beneath it a forbidden data destination (AGENTS.md).
    for candidate in (target, *target.parents):
        if (
            _entry_kind(candidate / ".git") is not None
            and (candidate / "SDD.md").is_file()
            and (candidate / "spec").is_dir()
        ):
            return True
    return False


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


def _migration_time(clock: Callable[[], datetime] | None) -> datetime:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("migration clock must be offset-aware")
    return now


def _create_verified_backup(
    connection: sqlite3.Connection,
    *,
    workspace: Path,
    from_version: int,
    now: datetime,
) -> Path:
    backup_directory = workspace / ".exp2res" / "backup"
    backup_created = False
    try:
        backup_directory.mkdir(mode=0o700, exist_ok=True)
        if not _is_real_directory(backup_directory):
            raise OSError("backup directory is not a real directory")
        os.chmod(backup_directory, 0o700)
        timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup_path = backup_directory / (
            f"exp2res-v{from_version}-{timestamp}.sqlite"
        )
        _write_private_file(backup_path, b"")
        backup_created = True
        destination = sqlite3.connect(backup_path)
        try:
            connection.backup(destination)
            # A rollback-journal backup restores from one self-contained file
            # with no WAL/SHM siblings, and the read-only verifier below
            # creates none next to it.
            destination.execute("PRAGMA journal_mode=DELETE")
        finally:
            destination.close()
        os.chmod(backup_path, 0o600)
        verifier = sqlite3.connect(
            f"file:{quote(str(backup_path), safe='/')}?mode=ro", uri=True
        )
        try:
            integrity = verifier.execute("PRAGMA integrity_check").fetchone()
            version = verifier.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()
        finally:
            verifier.close()
        if integrity is None or integrity[0] != "ok":
            raise sqlite3.DatabaseError("backup integrity verification failed")
        if version is None or version[0] != from_version:
            raise sqlite3.DatabaseError("backup schema version verification failed")
        return backup_path
    except BaseException:
        candidate = locals().get("backup_path")
        if backup_created and isinstance(candidate, Path):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _validate_migration_target(connection: sqlite3.Connection) -> None:
    from .repository import (
        hydrate_contradiction,
        hydrate_evidence_item,
        hydrate_experience_fact,
        hydrate_gap_question,
        hydrate_raw_log,
    )

    for row in connection.execute("SELECT * FROM raw_logs"):
        hydrate_raw_log(row)
    for row in connection.execute("SELECT * FROM evidence_items"):
        hydrate_evidence_item(row)
    for row in connection.execute("SELECT * FROM experience_facts"):
        source_rows = connection.execute(
            """
            SELECT fs.fact_id, fs.evidence_item_id, fs.support_type,
                   ei.raw_log_id
            FROM fact_sources AS fs
            JOIN evidence_items AS ei ON ei.id = fs.evidence_item_id
            WHERE fs.fact_id = ?
            """,
            (row["id"],),
        ).fetchall()
        hydrate_experience_fact(row, source_rows)
    for row in connection.execute("SELECT * FROM gap_questions"):
        hydrate_gap_question(row)
    for row in connection.execute("SELECT * FROM contradictions"):
        hydrate_contradiction(row)
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise sqlite3.IntegrityError("foreign key validation failed")
    status = inspect_schema(connection)
    if not status.compatible:
        raise sqlite3.DatabaseError("migration target is incompatible")

    # Compare every table, index, and trigger entry, including implicit
    # sqlite_autoindex rows whose SQL is NULL. The owning CREATE TABLE SQL also
    # pins every constraint that caused those implicit indexes to exist.
    def schema_entries(database: sqlite3.Connection) -> list[tuple[object, ...]]:
        return sorted(
            (row[0], row[1], row[2])
            for row in database.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger')
                """
            )
        )

    scratch = sqlite3.connect(":memory:")
    try:
        scratch.create_function("exp2res_owner_delete", 0, lambda: 0)
        scratch.executescript(SCHEMA_V4_SQL)
        expected_entries = schema_entries(scratch)
    finally:
        scratch.close()

    if not expected_entries or schema_entries(connection) != expected_entries:
        raise sqlite3.DatabaseError("migration target full schema shape mismatch")


def migrate_workspace(
    workspace: Path,
    *,
    clock: Callable[[], datetime] | None = None,
    failure_injector: Callable[[str], None] | None = None,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> SchemaStatus:
    """Apply every pending registered migration after one verified backup."""

    initial = inspect_workspace(workspace)
    if not initial.recognized:
        raise SchemaCompatibilityError()
    if initial.compatible:
        return initial
    if initial.stored_version is None or not migration_available(
        initial.stored_version
    ):
        raise SchemaCompatibilityError()

    backup_path: Path | None = None
    failure_code = "backup_verification"
    try:
        with writer_lock(workspace, timeout_ms=timeout_ms):
            connection = connect_database(
                workspace / ".exp2res" / "exp2res.sqlite",
                readonly=False,
                busy_timeout_ms=timeout_ms,
            )
            try:
                locked_status = inspect_schema(connection)
                if (
                    locked_status.stored_version != initial.stored_version
                    or not migration_available(locked_status.stored_version)
                ):
                    raise SchemaCompatibilityError()
                path = _migration_path(locked_status.stored_version)
                if not path:
                    raise SchemaCompatibilityError()
                now = _migration_time(clock)
                backup_path = _create_verified_backup(
                    connection,
                    workspace=workspace,
                    from_version=locked_status.stored_version,
                    now=now,
                )
                try:
                    foreign_keys_disabled = any(
                        step.requires_foreign_keys_off for step in path
                    )
                    if foreign_keys_disabled:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
                            raise sqlite3.DatabaseError(
                                "migration foreign key suspension failed"
                            )
                    connection.execute("BEGIN IMMEDIATE")
                    for step in path:
                        failure_code = (
                            f"migration_{step.from_version}_to_{step.to_version}"
                        )
                        if failure_injector is not None:
                            failure_injector(
                                f"before_migration_{step.from_version}_to_{step.to_version}"
                            )
                        step.apply(connection)
                        connection.execute(
                            """
                            INSERT INTO schema_meta(version, applied_at, app_version)
                            VALUES (?, ?, ?)
                            """,
                            (step.to_version, now.isoformat(), __version__),
                        )
                        if failure_injector is not None:
                            failure_injector(
                                f"after_migration_{step.from_version}_to_{step.to_version}"
                            )
                    if failure_injector is not None:
                        failure_injector("after_ddl")
                    failure_code = "final_validation"
                    if failure_injector is not None:
                        failure_injector("before_validation")
                    _validate_migration_target(connection)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    if connection.in_transaction:
                        connection.rollback()
                    connection.execute("PRAGMA foreign_keys = ON")
                    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                        raise sqlite3.DatabaseError(
                            "migration foreign key restoration failed"
                        )
                failure_code = "post_commit"
                if failure_injector is not None:
                    failure_injector("after_commit")
            except KeyboardInterrupt:
                raise
            except (SchemaCompatibilityError, WorkspaceBusyError):
                raise
            except BaseException as error:
                safe_detail = (
                    str(error)
                    if isinstance(error, ValueError)
                    and str(error)
                    in {
                        "raw_log_project_label_type",
                        "raw_log_project_label_blank",
                    }
                    else None
                )
                raise MigrationFailedError(
                    managed_backup_path=(
                        None if backup_path is None else str(backup_path)
                    ),
                    failure_code=(
                        failure_code
                        if safe_detail is None
                        else f"{failure_code}:{safe_detail}"
                    ),
                ) from error
            finally:
                connection.close()

        result = inspect_workspace(workspace)
    except KeyboardInterrupt:
        # An interrupt anywhere in the migrate flow — transaction, backup,
        # connection close, lock release, or the final status inspection —
        # keeps §14.14 rule 4's code-9 precedence while still reporting the
        # committed effects: the retained verified backup and, after commit,
        # the durable migrated schema surface through the cancelled envelope.
        raise MigrationInterrupted(
            managed_backup_path=(
                None if backup_path is None else str(backup_path)
            )
        ) from None

    if not result.compatible:
        raise MigrationFailedError(managed_backup_path=str(backup_path))
    return SchemaStatus(
        stored_version=result.stored_version,
        supported_version=result.supported_version,
        recognized=result.recognized,
        compatible=result.compatible,
        migration_path_available=result.migration_path_available,
        managed_backup_path=str(backup_path),
    )


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
    reconcile: bool = True,
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
            if reconcile:
                # §15.10 rule 8: the next compatible writer marks abandoned
                # LLM telemetry before its business operation. Telemetry
                # transactions inside a live invocation pass reconcile=False
                # so a running run cannot cancel itself.
                from .telemetry import reconcile_abandoned_telemetry

                try:
                    connection.execute("BEGIN IMMEDIATE")
                    reconcile_abandoned_telemetry(
                        connection, finished_at=datetime.now(timezone.utc)
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
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
