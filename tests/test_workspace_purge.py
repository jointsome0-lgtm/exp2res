"""§14.16 whole-workspace purge acceptance tests."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from exp2res import __version__
from exp2res.cli import app
import exp2res.exports.managed as managed_outputs
import exp2res.services.workspace as workspace_service
from exp2res.services.capture import capture_daily
from exp2res.storage.schema import PURGE_ENTITY_TABLES, PURGE_TABLE_ORDER
from exp2res.storage.workspace import CURRENT_SCHEMA_VERSION

from conftest import FIXED_NOW
from test_cli_correction import _prepare_full_graph


runner = CliRunner()


def _invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
    )
    return result, json.loads(result.stdout)


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _id_table_names(connection: sqlite3.Connection) -> set[str]:
    tables = set()
    for table in _user_tables(connection):
        primary_key = [
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})")
            if row[5]
        ]
        if primary_key == ["id"]:
            tables.add(table)
    return tables


def _captured_ids(
    connection: sqlite3.Connection,
) -> dict[str, list[str]]:
    return {
        entity_type: [
            row[0]
            for row in connection.execute(
                f"SELECT id FROM {table} ORDER BY CAST(id AS BLOB)"
            )
        ]
        for table, entity_type in PURGE_ENTITY_TABLES
        if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    }


def test_purge_inventory_covers_the_current_schema(workspace: Path) -> None:
    """§14.16: every current table and single-column entity ID is inventoried."""

    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        tables = _user_tables(connection)
        assert set(PURGE_TABLE_ORDER) == tables - {"schema_meta"}
        assert len(PURGE_TABLE_ORDER) == len(set(PURGE_TABLE_ORDER))
        assert {table for table, _entity in PURGE_ENTITY_TABLES} == (
            _id_table_names(connection) - {"schema_meta"}
        )
        assert len(PURGE_ENTITY_TABLES) == len(
            {entity for _table, entity in PURGE_ENTITY_TABLES}
        )


def test_workspace_purge_erases_live_content_and_retains_empty_workspace(
    workspace: Path,
) -> None:
    """§21.44 / §24.47: complete purge, fresh history, and erasure proof."""

    database = workspace / ".exp2res" / "exp2res.sqlite"
    config = workspace / ".exp2res" / "config.toml"
    config_before = config.read_bytes()
    main_sentinel = b"Vera Example PURGE MAIN SENTINEL 7219"
    wal_sentinel = b"Vera Example PURGE WAL SENTINEL 8461"

    _prepare_full_graph(workspace)
    capture_daily(
        workspace,
        raw_text=main_sentinel.decode("utf-8"),
        clock=lambda: FIXED_NOW.replace(hour=14),
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    assert main_sentinel in database.read_bytes()

    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700)
    backup = backup_root / "exp2res-v6-Vera-Example.sqlite"
    backup.write_bytes(b"Vera Example managed backup sentinel")

    branch_parent = workspace / "out" / "branch"
    branch_parent.mkdir(mode=0o700, exist_ok=True)
    branch_set = branch_parent / "branch_vera_purge"
    branch_set.mkdir(mode=0o700)
    (branch_set / "member.md").write_bytes(b"Vera Example managed branch sentinel")

    assessment_parent = workspace / "out" / "assessment"
    candidate = (
        assessment_parent
        / ".exp2res-candidate-snapshot_vera_purge-0123456789abcdef0123456789abcdef"
    )
    candidate.mkdir(mode=0o700)
    (candidate / "partial.md").write_bytes(b"Vera Example candidate sentinel")
    rollback = (
        branch_parent
        / ".exp2res-rollback-branch_vera_purge-fedcba9876543210fedcba9876543210"
    )
    rollback.mkdir(mode=0o700)
    (rollback / "old.md").write_bytes(b"Vera Example rollback sentinel")

    # Keep one idle handle open so closing the snapshot reader does not perform
    # a last-connection checkpoint before the purge command sees the WAL bytes.
    keeper = sqlite3.connect(database)
    reader = sqlite3.connect(database)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM raw_logs").fetchone()
        capture_daily(
            workspace,
            raw_text=wal_sentinel.decode("utf-8"),
            clock=lambda: FIXED_NOW.replace(hour=15),
        )
        wal = database.with_name(database.name + "-wal")
        assert wal.exists()
        assert wal_sentinel in wal.read_bytes()
        reader.rollback()
        reader.close()

        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                INSERT INTO schema_meta(version, applied_at, app_version)
                VALUES (?, ?, ?)
                """,
                (6, FIXED_NOW.isoformat(), "0.0.6"),
            )
            expected_ids = _captured_ids(connection)
            expected_generations = sorted(
                {
                    row[0]
                    for table in PURGE_TABLE_ORDER
                    if "generation_id"
                    in {
                        column[1]
                        for column in connection.execute(
                            f"PRAGMA table_info({table})"
                        )
                    }
                    for row in connection.execute(
                        f"SELECT DISTINCT generation_id FROM {table}"
                    )
                    if row[0] is not None
                },
                key=lambda value: value.encode("utf-8"),
            )
            connection.commit()

        result, envelope = _invoke_json(workspace, ["workspace", "purge", "--yes"])
        assert result.exit_code == 0, result.stderr
        assert envelope["command"] == "workspace purge"
        assert envelope["status"] == "ok"
        assert envelope["diagnostic_class"] is None
        assert envelope["result"] is None
        assert envelope["run_ids"] == []
        assert envelope["invalidated_views"] == []
        assert envelope["invalidated_branches"] == []
        assert envelope["retry"] is None
        assert envelope["residual_paths"] == []
        assert envelope["generation_ids"] == expected_generations
        assert {
            group["entity_type"]: group["ids"]
            for group in envelope["affected_ids"]["deleted"]
        } == expected_ids
        assert envelope["affected_ids"]["created"] == []
        assert envelope["affected_ids"]["superseded"] == []

        assert config.read_bytes() == config_before
        assert (workspace / ".exp2res").is_dir()
        assert database.is_file()
        assert sorted((workspace / "out" / "assessment").iterdir()) == []
        assert sorted((workspace / "out" / "branch").iterdir()) == []
        assert sorted(backup_root.iterdir()) == []

        with sqlite3.connect(database) as connection:
            for table in PURGE_TABLE_ORDER:
                assert connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0] == 0
            schema_rows = connection.execute(
                "SELECT version, applied_at, app_version FROM schema_meta"
            ).fetchall()
            assert len(schema_rows) == 1
            assert schema_rows[0][0] == CURRENT_SCHEMA_VERSION
            assert schema_rows[0][1] != FIXED_NOW.isoformat()
            assert schema_rows[0][2] == __version__
            assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
            assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0

        for path in (
            database,
            database.with_name(database.name + "-wal"),
            database.with_name(database.name + "-shm"),
        ):
            if path.exists():
                content = path.read_bytes()
                assert main_sentinel not in content
                assert wal_sentinel not in content
    finally:
        if reader:
            reader.close()
        keeper.close()


def test_workspace_purge_without_yes_refuses_before_destructive_work(
    workspace: Path,
) -> None:
    """§14.14 rule 3: non-interactive purge fails closed without consent."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example purge refusal sentinel",
        clock=lambda: FIXED_NOW,
    )
    managed = workspace / "out" / "assessment" / "snapshot_vera_refusal"
    managed.mkdir(parents=True, mode=0o700)
    (managed / "member.md").write_text("Vera Example retained\n", encoding="utf-8")

    result, envelope = _invoke_json(workspace, ["workspace", "purge"])

    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"
    assert envelope["affected_ids"]["deleted"] == []
    assert managed.is_dir()
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute(
            "SELECT raw_text FROM raw_logs WHERE id = ?", (bundle.raw_log.id,)
        ).fetchone()[0] == "Vera Example purge refusal sentinel"


def test_workspace_purge_human_mode_prints_primary_result_to_stdout(
    workspace: Path,
) -> None:
    """§14.14 rule 5: human result is stdout and diagnostics stay stderr."""

    capture_daily(
        workspace,
        raw_text="Vera Example human purge result",
        clock=lambda: FIXED_NOW,
    )

    result = runner.invoke(
        app,
        ["--workspace", str(workspace), "workspace", "purge", "--yes"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Purged the workspace database; the initialized workspace remains.\n"
    )
    assert result.stderr == ""


def test_workspace_purge_reports_symlink_residual_but_commits_database(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.44 / §24.47: symlinks are residual, untraversed, and exit 8."""

    capture_daily(
        workspace,
        raw_text="Vera Example purge residual source",
        clock=lambda: FIXED_NOW,
    )
    outside = tmp_path / "Vera Example purge symlink target"
    outside.write_bytes(b"Vera Example outside target remains unchanged")
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, exist_ok=True)
    planted = assessment / "snapshot_vera_symlink"
    planted.symlink_to(outside)

    result, envelope = _invoke_json(workspace, ["--yes", "workspace", "purge"])

    assert result.exit_code == 8
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(planted)]
    assert envelope["result"] is None
    assert planted.is_symlink()
    assert outside.read_bytes() == b"Vera Example outside target remains unchanged"
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert all(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            for table in PURGE_TABLE_ORDER
        )
        assert connection.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("failed_step", "expected_residual_name"),
    [
        ("initial_checkpoint", "exp2res.sqlite-wal"),
        ("vacuum", "exp2res.sqlite"),
        ("final_checkpoint", "exp2res.sqlite-wal"),
    ],
)
def test_workspace_purge_erasure_failure_is_exit_8_after_committed_deletion(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_step: str,
    expected_residual_name: str,
) -> None:
    """§8.1: every erasure step is attempted and failure cannot undo purge."""

    capture_daily(
        workspace,
        raw_text="Vera Example erasure-step failure source",
        clock=lambda: FIXED_NOW,
    )
    events: list[str] = []

    def checkpoint(_connection, database: Path) -> tuple[str, ...]:
        step = "initial_checkpoint" if not events else "final_checkpoint"
        events.append(step)
        return (
            (str(database.with_name(database.name + "-wal")),)
            if failed_step == step
            else ()
        )

    def vacuum(_connection, database: Path) -> tuple[str, ...]:
        events.append("vacuum")
        return (str(database),) if failed_step == "vacuum" else ()

    monkeypatch.setattr(workspace_service, "checkpoint_residuals", checkpoint)
    monkeypatch.setattr(workspace_service, "vacuum_residuals", vacuum)

    result, envelope = _invoke_json(workspace, ["workspace", "purge", "--yes"])

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert events == ["initial_checkpoint", "vacuum", "final_checkpoint"]
    assert [Path(path).name for path in envelope["residual_paths"]] == [
        expected_residual_name
    ]
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert all(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            for table in PURGE_TABLE_ORDER
        )
        assert connection.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0] == 1


def test_workspace_purge_interrupt_after_commit_reports_committed_effects(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§14.14 rule 6: post-commit interruption cancels without restoration."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example post-commit purge interruption",
        clock=lambda: FIXED_NOW,
    )

    def interrupt_checkpoint(_connection, _database):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        workspace_service,
        "checkpoint_residuals",
        interrupt_checkpoint,
    )

    result, envelope = _invoke_json(workspace, ["workspace", "purge", "--yes"])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["diagnostic_class"] == "cancelled"
    assert {Path(path).name for path in envelope["residual_paths"]} == {
        "exp2res.sqlite",
        "exp2res.sqlite-wal",
    }
    deleted = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["deleted"]
    }
    assert deleted["raw_log"] == [bundle.raw_log.id]
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert all(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            for table in PURGE_TABLE_ORDER
        )
        assert connection.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0] == 1


def test_workspace_purge_preamble_residual_uses_deletion_diagnostic(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§14.16: even preamble cleanup residuals use deletion_incomplete."""

    capture_daily(
        workspace,
        raw_text="Vera Example purge preamble residual",
        clock=lambda: FIXED_NOW,
    )
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setattr(
        managed_outputs,
        "reconcile_managed_outputs",
        lambda _workspace: (str(assessment),),
    )

    result, envelope = _invoke_json(workspace, ["workspace", "purge", "--yes"])

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(assessment)]
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 0


def test_preamble_residual_makes_the_human_result_report_incompleteness(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.16: a residual the purge service never sees still makes the human
    primary result incomplete, not a standing success sentence."""

    capture_daily(
        workspace,
        raw_text="Vera Example preamble residual purge",
        clock=lambda: FIXED_NOW,
    )
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, exist_ok=True)
    target = workspace.parent / "Vera Example purge preamble target"
    target.mkdir(exist_ok=True)
    candidate = assessment / (
        ".exp2res-candidate-snapshot_vera_purge_preamble-" + "b" * 32
    )
    candidate.symlink_to(target, target_is_directory=True)
    # The preamble reports the ambiguous sibling; this purge run reports no
    # residual of its own, which is exactly the divergence under test.
    monkeypatch.setattr(
        workspace_service, "remove_all_managed_output_entries", lambda _workspace: ()
    )

    result = runner.invoke(
        app,
        ["--workspace", str(workspace), "workspace", "purge", "--yes"],
    )

    assert result.exit_code == 8
    assert result.stdout == (
        "Purged the workspace database; the initialized workspace remains.\n"
        "Cleanup is incomplete; the paths reported above are unresolved.\n"
    )
    assert candidate.is_symlink()
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 0


def test_interrupt_between_erasure_steps_still_reports_committed_purge(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: cancellation anywhere after commit carries the committed
    deletion and its unproven paths, never an empty cancelled envelope."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example purge interrupt sentinel",
        clock=lambda: FIXED_NOW,
    )

    def interrupt(*_arguments, **_keywords):
        raise KeyboardInterrupt()

    monkeypatch.setattr(workspace_service, "vacuum_residuals", interrupt)

    result, envelope = _invoke_json(workspace, ["--yes", "workspace", "purge"])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    deleted = {
        group["entity_type"]: group["ids"] for group in envelope["affected_ids"]["deleted"]
    }
    assert deleted["raw_log"] == [bundle.raw_log.id]
    database = workspace / ".exp2res" / "exp2res.sqlite"
    assert str(database) in envelope["residual_paths"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0] == 1


def test_unreadable_managed_root_cannot_block_the_database_purge(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: a filesystem error becomes a residual path; it never
    leaves private rows behind by aborting before the purge transaction."""

    capture_daily(
        workspace,
        raw_text="Vera Example unreadable managed root",
        clock=lambda: FIXED_NOW,
    )

    def denied(_workspace: Path) -> tuple[str, ...]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(
        workspace_service, "remove_all_managed_output_entries", denied
    )

    result, envelope = _invoke_json(workspace, ["--yes", "workspace", "purge"])

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str((workspace / "out").absolute())]
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert all(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            for table in PURGE_TABLE_ORDER
        )


def test_managed_enumeration_is_total_when_an_entry_cannot_be_inspected(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared helper reports, never raises, so any privacy flow that calls
    it keeps its own commit-despite-cleanup-failure contract."""

    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, exist_ok=True)
    (assessment / "snapshot_vera_unreadable").mkdir(mode=0o700, exist_ok=True)

    def denied(*_arguments, **_keywords):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(managed_outputs, "_remove_entry", denied)

    residuals = managed_outputs.remove_all_managed_output_entries(workspace)

    assert residuals == (str(assessment / "snapshot_vera_unreadable"),)


def test_interrupt_on_commit_return_still_reports_the_durable_purge(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: an interrupt delivered as `commit()` returns finds the
    transaction already ended, so the envelope carries the durable purge
    instead of a no-op rollback and an empty cancelled result."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example commit-return interrupt",
        clock=lambda: FIXED_NOW,
    )

    class InterruptOnCommitReturn:
        """Commit for real, then raise as the C call returns to Python."""

        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def commit(self) -> None:
            self._connection.commit()
            raise KeyboardInterrupt()

    real_writer = workspace_service.writer_database

    @contextmanager
    def wrapped(target: Path, **keywords):
        with real_writer(target, **keywords) as connection:
            yield InterruptOnCommitReturn(connection)

    monkeypatch.setattr(workspace_service, "writer_database", wrapped)

    result, envelope = _invoke_json(workspace, ["--yes", "workspace", "purge"])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    deleted = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["deleted"]
    }
    assert deleted["raw_log"] == [bundle.raw_log.id]
    database = workspace / ".exp2res" / "exp2res.sqlite"
    assert str(database) in envelope["residual_paths"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 0
        assert connection.execute(
            "SELECT version FROM schema_meta"
        ).fetchall() == [(CURRENT_SCHEMA_VERSION,)]


def test_erasure_residual_never_tells_the_owner_to_remove_the_database(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.16: the initialized database must remain, so a failed VACUUM or
    checkpoint reports an unresolved path without prescribing its removal."""

    capture_daily(
        workspace,
        raw_text="Vera Example vacuum residual wording",
        clock=lambda: FIXED_NOW,
    )
    database = workspace / ".exp2res" / "exp2res.sqlite"
    monkeypatch.setattr(
        workspace_service,
        "vacuum_residuals",
        lambda _connection, path: (str(path),),
    )

    result = runner.invoke(
        app,
        ["--workspace", str(workspace), "workspace", "purge", "--yes"],
    )

    assert result.exit_code == 8
    assert "removal" not in result.stdout
    assert "remove" not in result.stdout
    assert result.stdout == (
        "Purged the workspace database; the initialized workspace remains.\n"
        "Cleanup is incomplete; the paths reported above are unresolved.\n"
    )
    assert "Cleanup did not complete; unresolved paths:" in result.stderr
    assert str(database) in result.stderr
    assert database.is_file()


def test_interrupt_during_connection_teardown_still_reports_the_purge(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the cancellation boundary reaches past result
    construction into lock and connection teardown."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example teardown interrupt",
        clock=lambda: FIXED_NOW,
    )
    real_writer = workspace_service.writer_database

    @contextmanager
    def interrupting_teardown(target: Path, **keywords):
        with real_writer(target, **keywords) as connection:
            yield connection
        # The purge has returned its result; the interrupt lands while the
        # writer lock and connection are being released.
        raise KeyboardInterrupt()

    monkeypatch.setattr(workspace_service, "writer_database", interrupting_teardown)

    result, envelope = _invoke_json(workspace, ["--yes", "workspace", "purge"])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    deleted = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["deleted"]
    }
    assert deleted["raw_log"] == [bundle.raw_log.id]
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 0


def test_interrupt_in_pre_transaction_cleanup_keeps_its_residuals(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: cleanup that already ran is reported even when the
    interrupt precedes the purge transaction and no row was deleted."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example pre-transaction interrupt",
        clock=lambda: FIXED_NOW,
    )
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, exist_ok=True)
    target = workspace.parent / "Vera Example pre-transaction target"
    target.mkdir(exist_ok=True)
    planted = assessment / "snapshot_vera_pre_transaction"
    planted.symlink_to(target, target_is_directory=True)

    def interrupt_after_managed_removal(_workspace: Path):
        raise KeyboardInterrupt()

    # Backups are enumerated first and report the planted symlink's sibling
    # residual; the managed-output pass is interrupted immediately after.
    monkeypatch.setattr(
        workspace_service, "remove_managed_backups", lambda _w: (str(planted),)
    )
    monkeypatch.setattr(
        workspace_service,
        "remove_all_managed_output_entries",
        interrupt_after_managed_removal,
    )

    result, envelope = _invoke_json(workspace, ["--yes", "workspace", "purge"])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["residual_paths"] == [str(planted)]
    assert envelope["affected_ids"]["deleted"] == []
    assert planted.is_symlink()
    assert target.is_dir()
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM raw_logs WHERE id = ?", (bundle.raw_log.id,)
        ).fetchone()[0] == 1
