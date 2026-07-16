"""Schema v1→v2 backup, migration, and rollback acceptance tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from exp2res.cli import app
from exp2res.errors import MigrationFailedError
from exp2res.services.capture import capture_daily
from exp2res.services.logs import show_log
from exp2res.storage.schema import LLM_CALLS_SQL, PROCESSING_RUNS_SQL
from exp2res.storage.workspace import (
    inspect_workspace,
    initialize_workspace,
    migrate_workspace,
)

from conftest import FIXED_NOW, configure_timezone


runner = CliRunner()
pytestmark = pytest.mark.lifecycle


def v1_workspace(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "v1-workspace"
    root.mkdir()
    initialize_workspace(root, clock=lambda: FIXED_NOW)
    configure_timezone(root)
    raw_text = "Vera Example v1 record preserved byte for byte"
    bundle = capture_daily(root, raw_text=raw_text, clock=lambda: FIXED_NOW)
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE llm_calls")
        connection.execute("DROP TABLE processing_runs")
        connection.execute("DELETE FROM schema_meta")
        connection.execute(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (1, ?, ?)",
            (FIXED_NOW.isoformat(), "0.1.0-v1-fixture"),
        )
    return root, bundle.raw_log.id, raw_text


def table_shape(database: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return [
            tuple(row[index] for index in range(1, 6))
            for row in connection.execute(f"PRAGMA table_info({table})")
        ]


def test_cli_migrates_v1_to_v2_with_verified_backup_and_preserved_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§12.14/issue #69: explicit migration is backed up and all-or-nothing."""

    workspace, log_id, raw_text = v1_workspace(tmp_path)
    before = inspect_workspace(workspace)
    assert before.stored_version == 1
    assert before.compatible is False
    assert before.migration_path_available is True

    monkeypatch.chdir(workspace)
    # §14.14 rule 3: db migrate is in the confirmation set — a non-interactive
    # invocation without --yes fails closed before any mutation.
    refused = runner.invoke(app, ["--json", "db", "migrate"])
    assert refused.exit_code == 2
    refused_envelope = json.loads(refused.stdout)
    assert refused_envelope["diagnostic_class"] == "input_required"
    assert inspect_workspace(workspace).stored_version == 1
    assert not (workspace / ".exp2res" / "backup").exists()

    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    assert result.exit_code == 0, result.stderr
    envelope = json.loads(result.stdout)
    schema = envelope["result"]["schema"]
    assert schema["stored_version"] == 2
    assert schema["compatible"] is True
    backup = Path(schema["managed_backup_path"])
    assert backup.is_file()
    assert backup.stat().st_mode & 0o777 == 0o600
    assert backup.parent.stat().st_mode & 0o777 == 0o700
    assert sorted(path.name for path in backup.parent.iterdir()) == [backup.name]

    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0] == 1
        assert connection.execute(
            "SELECT raw_text FROM raw_logs WHERE id = ?", (log_id,)
        ).fetchone()[0] == raw_text
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        assert [
            row[0] for row in connection.execute("SELECT version FROM schema_meta ORDER BY version")
        ] == [1, 2]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"processing_runs", "llm_calls"}.issubset(tables)
    assert show_log(workspace, log_id=log_id).raw_log.raw_text == raw_text


def test_cli_reports_a_rolled_back_migration_as_integrity_class_7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14: a failed migration is class 7 migration_failed, never class 4."""

    import exp2res.cli as cli_module

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)

    def failing_migration(_workspace: Path):
        raise MigrationFailedError(
            managed_backup_path="/tmp/Vera Example backup.sqlite"
        )

    monkeypatch.setattr(cli_module, "migrate_workspace", failing_migration)
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    assert result.exit_code == 7
    envelope = json.loads(result.stdout)
    assert envelope["diagnostic_class"] == "migration_failed"
    assert envelope["result"]["schema"]["managed_backup_path"] == (
        "/tmp/Vera Example backup.sqlite"
    )
    assert envelope["result"]["schema"]["stored_version"] == 1


def test_cli_reports_migration_interrupt_as_cancelled_class_9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rules 4/6: a service interrupt keeps cancellation precedence."""

    import exp2res.cli as cli_module

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)

    def interrupted_migration(_workspace: Path):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "migrate_workspace", interrupted_migration)
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    assert result.exit_code == 9
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "cancelled"
    assert envelope["diagnostic_class"] == "cancelled"
    assert envelope["result"] is None


def test_migration_failure_rolls_back_ddl_and_version_but_retains_backup(
    tmp_path: Path,
) -> None:
    """§12.14: an injected target-validation failure exposes no partial v2."""

    workspace, log_id, raw_text = v1_workspace(tmp_path)

    def fail_after_ddl(point: str) -> None:
        if point == "after_ddl":
            raise RuntimeError("Vera Example injected migration failure")

    with pytest.raises(MigrationFailedError) as caught:
        migrate_workspace(
            workspace,
            clock=lambda: FIXED_NOW.replace(day=16),
            failure_injector=fail_after_ddl,
        )
    assert caught.value.managed_backup_path is not None
    backup = Path(caught.value.managed_backup_path)
    assert backup.is_file()

    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_meta ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        stored_text = connection.execute(
            "SELECT raw_text FROM raw_logs WHERE id = ?", (log_id,)
        ).fetchone()[0]
    assert versions == [(1,)]
    assert "processing_runs" not in tables
    assert "llm_calls" not in tables
    assert stored_text == raw_text
    assert inspect_workspace(workspace).stored_version == 1


def test_migration_interrupt_rolls_back_and_propagates_with_backup_retained(
    tmp_path: Path,
) -> None:
    """§12.14/§14.14: Ctrl-C rolls back unchanged and is not class 7."""

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE processing_runs (id TEXT PRIMARY KEY)")
    stale_shape = table_shape(database, "processing_runs")

    def interrupt_after_ddl(point: str) -> None:
        if point == "after_ddl":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        migrate_workspace(
            workspace,
            clock=lambda: FIXED_NOW.replace(day=16),
            failure_injector=interrupt_after_ddl,
        )

    assert inspect_workspace(workspace).stored_version == 1
    assert table_shape(database, "processing_runs") == stale_shape
    assert table_shape(database, "llm_calls") == []
    backups = list((workspace / ".exp2res" / "backup").iterdir())
    assert len(backups) == 1
    assert backups[0].is_file()


@pytest.mark.parametrize("table", ["processing_runs", "llm_calls"])
def test_cli_rejects_wrong_shaped_preexisting_telemetry_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str
) -> None:
    """§12.14: stale telemetry DDL fails validation and rolls back v1."""

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        if table == "processing_runs":
            connection.execute("CREATE TABLE processing_runs (id TEXT PRIMARY KEY)")
        else:
            connection.execute(
                """
                CREATE TABLE llm_calls (
                    run_id TEXT NOT NULL,
                    call_index INTEGER NOT NULL,
                    PRIMARY KEY (run_id, call_index)
                )
                """
            )
    stale_shape = table_shape(database, table)

    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    assert result.exit_code == 7
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "migration_failed"
    backup = Path(envelope["result"]["schema"]["managed_backup_path"])
    assert backup.is_file()
    assert envelope["result"]["schema"]["stored_version"] == 1
    assert table_shape(database, table) == stale_shape


@pytest.mark.parametrize(
    ("table", "ddl"),
    [("processing_runs", PROCESSING_RUNS_SQL), ("llm_calls", LLM_CALLS_SQL)],
)
def test_exact_shape_preexisting_telemetry_table_migrates(
    tmp_path: Path, table: str, ddl: str
) -> None:
    """§12.13/§12.15: an exact normative pre-existing shape is accepted."""

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(ddl)
    expected_shape = table_shape(database, table)

    migrated = migrate_workspace(
        workspace, clock=lambda: FIXED_NOW.replace(day=16)
    )

    assert migrated.stored_version == 2
    assert migrated.compatible is True
    assert table_shape(database, table) == expected_shape
