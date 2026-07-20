"""Workspace bootstrap, compatibility, concurrency, and recovery tests."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from exp2res.cli import app
from exp2res.errors import (
    PublicCheckoutError,
    SchemaCompatibilityError,
    WorkspaceBusyError,
)
from exp2res.services.capture import capture_daily
from exp2res.services.logs import list_logs
from exp2res.storage.workspace import (
    CURRENT_SCHEMA_VERSION,
    discover_workspace,
    initialize_workspace,
)

from conftest import FIXED_NOW, configure_timezone


runner = CliRunner()
pytestmark = pytest.mark.lifecycle


def test_fresh_init_and_idempotent_reopen_are_private_and_versioned(
    tmp_path: Path,
) -> None:
    """§21.36 / §21.41; §24.39 / §24.44: fresh init and idempotent reopen."""
    root = tmp_path / "workspace"
    root.mkdir()
    _, first_status, created = initialize_workspace(root, clock=lambda: FIXED_NOW)
    assert created is True
    assert CURRENT_SCHEMA_VERSION == 6
    assert first_status.stored_version == CURRENT_SCHEMA_VERSION
    assert first_status.supported_version == 6
    assert first_status.compatible is True

    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        rows_before = connection.execute("SELECT * FROM schema_meta").fetchall()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert len(rows_before) == 1
    assert rows_before[0][0] == CURRENT_SCHEMA_VERSION
    assert rows_before[0][1] == FIXED_NOW.isoformat()
    assert rows_before[0][2]
    assert journal_mode.lower() == "wal"

    status_result = runner.invoke(
        app,
        ["--json", "--workspace", str(root), "db", "status"],
    )
    status_envelope = json.loads(status_result.stdout)
    assert status_result.exit_code == 0
    assert status_envelope["result"]["schema"] == {
        "compatible": True,
        "managed_backup_path": None,
        "migration_path_available": None,
        "recognized": True,
        "stored_version": 6,
        "supported_version": 6,
    }

    sentinel = root / "out" / "Vera Example - inert.txt"
    sentinel.write_text("Vera Example\n", encoding="utf-8")
    os.chmod(root / "out", 0o755)
    _, second_status, created_again = initialize_workspace(root)
    with sqlite3.connect(database) as connection:
        rows_after = connection.execute("SELECT * FROM schema_meta").fetchall()
    assert created_again is False
    assert second_status.compatible is True
    assert rows_after == rows_before
    assert sentinel.read_text(encoding="utf-8") == "Vera Example\n"

    for path, expected_mode in {
        root / ".exp2res": 0o700,
        root / "out": 0o700,
        root / ".exp2res" / "config.toml": 0o600,
        root / ".exp2res" / "lock": 0o600,
        database: 0o600,
    }.items():
        assert path.stat().st_mode & 0o777 == expected_mode


def test_init_refuses_public_checkout_and_partial_state(tmp_path: Path) -> None:
    """§21.41; §24.30 / §24.44: public checkout and partial init fail closed."""
    public = tmp_path / "public"
    public.mkdir()
    (public / ".git").mkdir()
    (public / "spec").mkdir()
    (public / "SDD.md").write_text("Vera Example synthetic marker\n", encoding="utf-8")
    with pytest.raises(PublicCheckoutError):
        initialize_workspace(public)
    assert not (public / ".exp2res").exists()

    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / ".exp2res").mkdir()
    with pytest.raises(SchemaCompatibilityError):
        initialize_workspace(partial)
    assert not (partial / ".exp2res" / "exp2res.sqlite").exists()


def test_incompatible_schema_blocks_business_io_but_status_reports(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§21.36; §24.39: newer schema fails before raw-table reads or writes."""
    original = capture_daily(
        workspace,
        raw_text="Vera Example compatible record",
        clock=lambda: FIXED_NOW,
    )
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO schema_meta VALUES (?, ?, ?)",
            (7, FIXED_NOW.isoformat(), "future-build"),
        )

    with pytest.raises(SchemaCompatibilityError):
        list_logs(workspace)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 1

    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "db", "status"])
    envelope = json.loads(result.stdout)
    assert result.exit_code == 4
    assert envelope["diagnostic_class"] == "schema_incompatible"
    assert envelope["result"]["schema"]["stored_version"] == 7
    assert original.raw_log.raw_text not in result.stdout
    assert original.raw_log.raw_text not in result.stderr


def test_nearest_partial_marker_wins_and_override_never_falls_back(
    tmp_path: Path,
) -> None:
    """§21.41; §24.44: physical nearest-parent discovery is fail-closed."""
    outer = tmp_path / "outer"
    outer.mkdir()
    initialize_workspace(outer, clock=lambda: FIXED_NOW)
    configure_timezone(outer)
    nested = outer / "nested"
    deep = nested / "a" / "b"
    deep.mkdir(parents=True)
    (nested / ".exp2res").mkdir()

    assert discover_workspace(cwd=deep) == nested
    with pytest.raises(SchemaCompatibilityError):
        list_logs(nested)
    with pytest.raises(Exception):
        discover_workspace(cwd=deep, override=str(tmp_path / "missing"))


def test_busy_writer_fails_with_stable_class_and_no_partial_pair(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§21.37; §24.40: bounded writer-lock contention yields workspace_busy."""
    lock_path = workspace / ".exp2res" / "lock"
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(WorkspaceBusyError) as captured:
            capture_daily(
                workspace,
                raw_text="Vera Example contended capture",
                clock=lambda: FIXED_NOW,
                timeout_ms=20,
            )
        assert captured.value.diagnostic_class == "workspace_busy"

        from exp2res.services.capture import capture_daily_file as real_capture_file

        def fast_capture(*args, **kwargs):
            return real_capture_file(*args, **kwargs, timeout_ms=20)

        monkeypatch.setattr("exp2res.cli.capture_daily_file", fast_capture)
        source = Path(__file__).resolve().parent.parent / "examples" / "vera" / "corpus" / "logs" / "daily-2026-06-02.md"
        cli_result = runner.invoke(
            app,
            [
                "--json",
                "--workspace",
                str(workspace),
                "log",
                "today",
                "--file",
                str(source),
            ],
        )
        envelope = json.loads(cli_result.stdout)
        assert cli_result.exit_code == 5
        assert envelope["diagnostic_class"] == "workspace_busy"
        assert "Traceback" not in cli_result.stdout + cli_result.stderr
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert list_logs(workspace) == ()


def test_losing_the_marker_creation_race_never_removes_the_foreign_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.1: init never deletes existing workspace data, even under a create race."""
    foreign = tmp_path / ".exp2res"
    original_mkdir = Path.mkdir

    def racing_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == foreign:
            original_mkdir(self, mode=0o700)
            raise FileExistsError(str(self))
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    with pytest.raises(SchemaCompatibilityError):
        initialize_workspace(tmp_path)
    assert foreign.is_dir()


def test_init_refuses_subdirectories_of_a_public_checkout(tmp_path: Path) -> None:
    """PR #95 review: a public-checkout ancestor forbids every nested target."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "SDD.md").write_text("# map\n", encoding="utf-8")
    (tmp_path / "spec").mkdir()
    nested = tmp_path / "examples" / "scratch"
    nested.mkdir(parents=True)
    with pytest.raises(PublicCheckoutError):
        initialize_workspace(nested)
    assert not (nested / ".exp2res").exists()


def test_incompatible_workspace_blocks_file_capture_before_source_read(
    tmp_path: Path,
) -> None:
    """PR #95 review: the §12.14 gate precedes private source acquisition."""
    workspace, _, _ = initialize_workspace(tmp_path)
    configure_timezone(workspace)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (7, ?, ?)",
        (FIXED_NOW.isoformat(), "future"),
    )
    connection.commit()
    connection.close()
    result = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "log",
            "today",
            "--file",
            str(workspace / "definitely-missing.md"),
        ],
    )
    assert result.exit_code == 4
    envelope = json.loads(result.stdout)
    assert envelope["diagnostic_class"] == "schema_incompatible"
