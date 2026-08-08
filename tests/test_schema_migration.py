"""Schema v1→v2→…→v9→v10 migration and rollback tests."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from exp2res.cli import app
from exp2res.errors import MigrationFailedError, MigrationInterrupted
from exp2res.services.logs import show_log
from exp2res.storage.schema import (
    JOB_DESCRIPTIONS_SQL,
    LLM_CALLS_SQL,
    PROCESSING_RUNS_SQL,
    SCHEMA_V1_SQL,
    SCHEMA_V2_SQL,
    SCHEMA_V3_SQL,
    SCHEMA_V4_SQL,
    SCHEMA_V5_SQL,
    SCHEMA_V6_SQL,
    SCHEMA_V7_SQL,
    SCHEMA_V8_SQL,
    SCHEMA_V9_SQL,
    SCHEMA_V10_SQL,
    SCHEMA_V11_SQL,
    RESUME_BRANCHES_SQL,
    RESUME_BULLETS_SQL,
)
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
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    raw_text = "Vera Example v1 record preserved byte for byte"
    log_id = "log_vera_v1"
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V1_SQL)
        connection.execute(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (1, ?, ?)",
            (FIXED_NOW.isoformat(), "0.1.0-v1-fixture"),
        )
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, external_ref, corrects_log_id, metadata_json
            ) VALUES (?, ?, 'manual_daily', 'manual_entry', ?, NULL,
                      'exact_day', 'high', ?, NULL, NULL, NULL, '{}')
            """,
            (log_id, FIXED_NOW.isoformat(), FIXED_NOW.isoformat(), raw_text),
        )
        connection.execute(
            """
            INSERT INTO evidence_items(
                id, created_at, raw_log_id, title, summary, uri, path,
                strength, metadata_json
            ) VALUES ('evi_vera_v1', ?, ?, NULL, 'Owner-authored manual claim.',
                      NULL, NULL, 'manual_claim', '{}')
            """,
            (FIXED_NOW.isoformat(), log_id),
        )
    database.chmod(0o600)
    return root, log_id, raw_text


def v2_workspace(
    tmp_path: Path,
    *,
    projects: tuple[tuple[str, str | None], ...] = (),
    name: str = "v2-workspace",
) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V2_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            (
                (1, FIXED_NOW.isoformat(), "0.1.0-v1-fixture"),
                (2, FIXED_NOW.isoformat(), "0.1.0-v2-fixture"),
            ),
        )
        for log_id, project in projects:
            connection.execute(
                """
                INSERT INTO raw_logs(
                    id, recorded_at, entry_type, source_type, occurred_start,
                    occurred_end, temporal_precision, temporal_confidence,
                    raw_text, project, external_ref, corrects_log_id,
                    metadata_json
                ) VALUES (?, ?, 'manual_daily', 'manual_entry', ?, NULL,
                          'exact_day', 'high', ?, ?, NULL, NULL, '{}')
                """,
                (
                    log_id,
                    FIXED_NOW.isoformat(),
                    FIXED_NOW.isoformat(),
                    f"Vera Example migration record {log_id}",
                    project,
                ),
            )
    database.chmod(0o600)
    return root


def v3_workspace(tmp_path: Path, *, name: str = "v3-workspace") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V3_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            (
                (1, FIXED_NOW.isoformat(), "0.1.0-v1-fixture"),
                (2, FIXED_NOW.isoformat(), "0.1.0-v2-fixture"),
                (3, FIXED_NOW.isoformat(), "0.1.0-v3-fixture"),
            ),
        )
    database.chmod(0o600)
    return root


def v4_workspace(tmp_path: Path, *, name: str = "v4-workspace") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V4_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, FIXED_NOW.isoformat(), f"0.1.0-v{version}-fixture")
                for version in range(1, 5)
            ),
        )
    database.chmod(0o600)
    return root


def v5_workspace(tmp_path: Path, *, name: str = "v5-workspace") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V5_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, FIXED_NOW.isoformat(), f"0.1.0-v{version}-fixture")
                for version in range(1, 6)
            ),
        )
    database.chmod(0o600)
    return root


def v6_workspace(tmp_path: Path, *, name: str = "v6-workspace") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V6_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, FIXED_NOW.isoformat(), f"0.1.0-v{version}-fixture")
                for version in range(1, 7)
            ),
        )
    database.chmod(0o600)
    return root


def v7_workspace(tmp_path: Path, *, name: str = "v7-workspace") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V7_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, FIXED_NOW.isoformat(), f"0.1.0-v{version}-fixture")
                for version in range(1, 8)
            ),
        )
    database.chmod(0o600)
    return root



def v8_workspace(tmp_path: Path, *, name: str = "v8-workspace") -> Path:
    """A v8 workspace holding one raw lineage and a full derived layer."""

    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    stamp = FIXED_NOW.isoformat()
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V8_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, stamp, f"0.1.0-v{version}-fixture")
                for version in range(1, 9)
            ),
        )
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, project_key, external_ref, corrects_log_id, metadata_json
            ) VALUES (
                'log_vera_v8', ?, 'manual_daily', 'manual_entry', ?, NULL,
                'exact_day', 'high', 'Vera Example retained raw record.',
                NULL, NULL, NULL, NULL, '{}'
            )
            """,
            (stamp, stamp),
        )
        connection.execute(
            """
            INSERT INTO evidence_items(
                id, created_at, raw_log_id, title, summary, uri, path,
                strength, metadata_json
            ) VALUES (
                'evi_vera_v8', ?, 'log_vera_v8', NULL,
                'Vera Example retained support.', NULL, NULL,
                'manual_claim', '{}'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO processing_runs(
                id, stage, started_at, status, input_ids_json, output_ids_json,
                metadata_json
            ) VALUES (
                'run_vera_v8', '13.6', ?, 'completed', '[]', '[]', '{}'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO experience_facts(
                id, created_at, superseded_at, claim, claim_kind, project,
                project_key, role, company, context, ownership_level, action,
                object, outcome, skills_json, technologies_json, themes_json,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, confidence, metadata_json,
                produced_by_run_id, generation_id
            ) VALUES (
                'fact_vera_v8', ?, NULL, 'Vera Example retained fact.',
                'observed_fact', NULL, NULL, NULL, NULL,
                'independent_project', 'built', 'built', 'a fixture', NULL,
                '[]', '[]', '[]', ?, NULL, 'exact_day', 'high', 'medium', '{}',
                'run_vera_v8', 'gen_vera_v8'
            )
            """,
            (stamp, stamp),
        )
        connection.execute(
            """
            INSERT INTO fact_sources(fact_id, evidence_item_id, support_type)
            VALUES ('fact_vera_v8', 'evi_vera_v8', 'direct')
            """
        )
        connection.execute(
            """
            INSERT INTO self_signals(
                id, created_at, superseded_at, signal_type, statement,
                supporting_fact_ids_json, counter_fact_ids_json, confidence,
                metadata_json, produced_by_run_id, generation_id
            ) VALUES (
                'signal_vera_v8', ?, NULL, 'execution_pattern',
                'Vera Example repeats a provenance-aware workflow.',
                '["fact_vera_v8"]', '[]', 'medium', '{}',
                'run_vera_v8', 'gen_vera_v8'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO assessment_snapshots(
                id, created_at, superseded_at, scope, scope_target, title,
                summary, gap_question_ids_json, contradiction_ids_json,
                verification_status, metadata_json, produced_by_run_id,
                generation_id
            ) VALUES (
                'snapshot_vera_v8', ?, NULL, 'global', NULL,
                'Self-Assessment — Global',
                'Current evidence suggests a provenance-aware workflow.',
                '[]', '[]', 'supported', '{}', 'run_vera_v8', 'gen_vera_v8'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO self_claims(
                id, created_at, superseded_at, snapshot_id, claim, claim_kind,
                dimension, source_signal_ids_json, source_fact_ids_json,
                confidence, verification_status, counterevidence_json,
                uncertainty, metadata_json, produced_by_run_id, generation_id
            ) VALUES (
                'claim_vera_v8', ?, NULL, 'snapshot_vera_v8',
                'Current evidence suggests a provenance-aware workflow.',
                'narrative_summary', 'trajectory', '["signal_vera_v8"]',
                '["fact_vera_v8"]', 'medium', 'supported', '[]', NULL, '{}',
                'run_vera_v8', 'gen_vera_v8'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO verification_findings(
                id, created_at, produced_by_run_id, target_type, target_id,
                status, reason, unsupported_phrases_json, suggested_rewrite,
                counterevidence_json
            ) VALUES (
                'finding_vera_v8', ?, 'run_vera_v8', 'self_claim',
                'claim_vera_v8', 'supported',
                'The supplied evidence supports the claim.', '[]', NULL, '[]'
            )
            """,
            (stamp,),
        )
    database.chmod(0o600)
    return root


def v9_workspace(tmp_path: Path, *, name: str = "v9-workspace") -> Path:
    """A v9 workspace holding one global and one project-scoped view.

    Both views are current and complete — snapshot, claim, finding — and both
    have a published set, so the 9→10 step has something to delete, something
    to retain, and managed output on both sides of that split.
    """

    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    stamp = FIXED_NOW.isoformat()
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V9_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, stamp, f"0.1.0-v{version}-fixture")
                for version in range(1, 10)
            ),
        )
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, project_key, external_ref, corrects_log_id, metadata_json
            ) VALUES (
                'log_vera_v9', ?, 'manual_daily', 'manual_entry', ?, NULL,
                'exact_day', 'high', 'Vera Example retained raw record.',
                'Vera Example Project', 'vera example project', NULL, NULL, '{}'
            )
            """,
            (stamp, stamp),
        )
        connection.execute(
            """
            INSERT INTO evidence_items(
                id, created_at, raw_log_id, title, summary, uri, path,
                strength, metadata_json
            ) VALUES (
                'evi_vera_v9', ?, 'log_vera_v9', NULL,
                'Vera Example retained support.', NULL, NULL,
                'manual_claim', '{}'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO processing_runs(
                id, stage, started_at, status, input_ids_json, output_ids_json,
                metadata_json
            ) VALUES (
                'run_vera_v9', '13.6', ?, 'completed', '[]', '[]', '{}'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO experience_facts(
                id, created_at, superseded_at, claim, claim_kind, project,
                project_key, role, company, context, ownership_level, action,
                object, outcome, skills_json, technologies_json, themes_json,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, confidence, metadata_json,
                produced_by_run_id, generation_id
            ) VALUES (
                'fact_vera_v9', ?, NULL, 'Vera Example retained fact.',
                'observed_fact', 'Vera Example Project', 'vera example project',
                NULL, NULL, 'independent_project', 'built', 'built',
                'a fixture', NULL, '[]', '[]', '[]', ?, NULL, 'exact_day',
                'high', 'medium', '{}', 'run_vera_v9', 'gen_vera_v9'
            )
            """,
            (stamp, stamp),
        )
        connection.execute(
            """
            INSERT INTO fact_sources(fact_id, evidence_item_id, support_type)
            VALUES ('fact_vera_v9', 'evi_vera_v9', 'direct')
            """
        )
        for identity, scope, target, title in (
            ("global", "global", None, "Self-Assessment — Global"),
            (
                "project",
                "project",
                "Vera Example Project",
                "Self-Assessment — Vera Example Project",
            ),
        ):
            connection.execute(
                """
                INSERT INTO assessment_snapshots(
                    id, created_at, superseded_at, scope, scope_target, title,
                    summary, gap_question_ids_json, contradiction_ids_json,
                    verification_status, metadata_json, produced_by_run_id,
                    generation_id
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, '[]', '[]', 'supported',
                          '{}', 'run_vera_v9', 'gen_vera_v9')
                """,
                (
                    f"snapshot_vera_v9_{identity}",
                    stamp,
                    scope,
                    target,
                    title,
                    "Current evidence suggests a provenance-aware workflow.",
                ),
            )
            connection.execute(
                """
                INSERT INTO self_claims(
                    id, created_at, superseded_at, snapshot_id, claim,
                    claim_kind, dimension, source_fact_ids_json,
                    counter_fact_ids_json, confidence, verification_status,
                    counterevidence_json, uncertainty, metadata_json,
                    produced_by_run_id, generation_id
                ) VALUES (?, ?, NULL, ?,
                    'Current evidence suggests a provenance-aware workflow.',
                    'narrative_summary', 'trajectory', '["fact_vera_v9"]',
                    '[]', 'medium', 'supported', '[]', NULL, '{}',
                    'run_vera_v9', 'gen_vera_v9')
                """,
                (
                    f"claim_vera_v9_{identity}",
                    stamp,
                    f"snapshot_vera_v9_{identity}",
                ),
            )
            connection.execute(
                """
                INSERT INTO verification_findings(
                    id, created_at, produced_by_run_id, target_type, target_id,
                    status, reason, unsupported_phrases_json,
                    suggested_rewrite, counterevidence_json
                ) VALUES (?, ?, 'run_vera_v9', 'self_claim', ?, 'supported',
                    'The supplied evidence supports the claim.', '[]', NULL,
                    '[]')
                """,
                (
                    f"finding_vera_v9_{identity}",
                    stamp,
                    f"claim_vera_v9_{identity}",
                ),
            )
    database.chmod(0o600)
    return root


def published_set(workspace: Path, snapshot_id: str) -> Path:
    path = workspace / "out" / "assessment" / snapshot_id
    path.mkdir(mode=0o700, parents=True)
    (path / "report.md").write_text("stale", encoding="utf-8")
    (path / "manifest.json").write_text("{}", encoding="utf-8")
    return path


def sqlite_master_shape(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return sorted(
            connection.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger')
                """
            ).fetchall()
        )


def _shape_rows(
    connection: sqlite3.Connection, table: str
) -> list[tuple[object, ...]]:
    return [
        tuple(row[index] for index in range(1, 6))
        for row in connection.execute(f"PRAGMA table_info({table})")
    ]


def table_shape(database: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return _shape_rows(connection, table)


def table_rows(database: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def normative_shape(ddl: str, table: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(ddl)
        return _shape_rows(connection, table)
    finally:
        connection.close()


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
    assert schema["stored_version"] == 12
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
        ] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "processing_runs",
        "llm_calls",
        "experience_facts",
        "fact_sources",
        "gap_questions",
        "contradictions",
        "self_claims",
    }.issubset(tables)
    assert show_log(workspace, log_id=log_id).raw_log.raw_text == raw_text


def test_v2_to_v3_backfills_canonical_project_keys_and_keeps_one_backup(
    tmp_path: Path,
) -> None:
    """§12 rule 14/§12.14: v2 labels receive deterministic stored keys."""

    workspace = v2_workspace(
        tmp_path,
        projects=(
            ("log_ascii", " Exp2Res "),
            ("log_unicode", "Exp2Re\u0301s"),
            ("log_none", None),
        ),
    )
    migrated = migrate_workspace(
        workspace, clock=lambda: FIXED_NOW.replace(day=16)
    )
    assert migrated.stored_version == 12
    backup = Path(migrated.managed_backup_path or "")
    assert backup.is_file()
    assert "exp2res-v2-" in backup.name
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0] == 2
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT id, project, project_key FROM raw_logs ORDER BY id"
        ).fetchall() == [
            ("log_ascii", " Exp2Res ", "exp2res"),
            ("log_none", None, None),
            ("log_unicode", "Exp2Re\u0301s", "exp2rés"),
        ]
        assert connection.execute(
            "SELECT version FROM schema_meta ORDER BY version"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,)
        ]


def test_fresh_and_migrated_workspaces_have_identical_sqlite_master_shape(
    tmp_path: Path,
) -> None:
    """§12.14: fresh and migrated table/trigger SQL has exact parity."""

    migrated = v7_workspace(tmp_path)
    migrate_workspace(migrated, clock=lambda: FIXED_NOW.replace(day=16))
    fresh = tmp_path / "fresh-current"
    fresh.mkdir()
    initialize_workspace(fresh, clock=lambda: FIXED_NOW.replace(day=16))
    assert sqlite_master_shape(
        migrated / ".exp2res" / "exp2res.sqlite"
    ) == sqlite_master_shape(fresh / ".exp2res" / "exp2res.sqlite")


def test_v8_to_v9_deletes_the_derived_layer_and_its_published_sets(
    tmp_path: Path,
) -> None:
    """§12.14/issue #76: the registered whole-layer deletion, out/ included."""

    workspace = v8_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    published = workspace / "out" / "assessment" / "snapshot_vera_v8"
    published.mkdir(mode=0o700, parents=True)
    (published / "report.md").write_text("stale", encoding="utf-8")
    (published / "manifest.json").write_text("{}", encoding="utf-8")

    migrated = migrate_workspace(
        workspace, clock=lambda: FIXED_NOW.replace(day=16)
    )
    assert migrated.stored_version == 12
    assert migrated.compatible is True
    # The published set is regenerable output over rows the step deletes, so
    # it goes with them rather than outliving its own database provenance.
    assert not published.exists()

    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "self_signals" not in tables
        assert not any("self_signals" in row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
        ))
        for table in ("assessment_snapshots", "self_claims", "verification_findings"):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0
        columns = {row[1] for row in connection.execute("PRAGMA table_info(self_claims)")}
        assert "counter_fact_ids_json" in columns
        assert "source_signal_ids_json" not in columns
        # The raw and fact layers are untouched: only the derived layer the
        # signal removal invalidates is rebuilt from empty.
        assert connection.execute(
            "SELECT raw_text FROM raw_logs WHERE id = 'log_vera_v8'"
        ).fetchone()[0] == "Vera Example retained raw record."
        assert connection.execute(
            "SELECT COUNT(*) FROM experience_facts"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM fact_sources"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT version FROM schema_meta ORDER BY version"
        ).fetchall() == [(index,) for index in range(1, 13)]
        # The rebuilt tables keep their §11 lifecycle guards, so the empty
        # derived layer is protected exactly like a populated one.
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        } >= {
            "self_claims_lifecycle_update_guard",
            "self_claims_owner_delete_guard",
            "assessment_snapshots_lifecycle_update_guard",
            "verification_findings_update_guard",
        }


def test_v9_to_v10_deletes_the_project_views_and_keeps_the_global_one(
    tmp_path: Path,
) -> None:
    """§12.14/issue #247: `AssessmentScope` loses `project`, so its rows go."""

    workspace = v9_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    project_set = published_set(workspace, "snapshot_vera_v9_project")
    global_set = published_set(workspace, "snapshot_vera_v9_global")

    migrated = migrate_workspace(
        workspace, clock=lambda: FIXED_NOW.replace(day=16)
    )
    assert migrated.stored_version == 12
    assert migrated.compatible is True
    # The deleted view's set has no provenance left at all, and the retained
    # view's set carries the superseded `manifest_version` §13.14 rule 5
    # refuses to overwrite in place, so both are cleared for regeneration.
    assert not project_set.exists()
    assert not global_set.exists()

    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        assert connection.execute(
            "SELECT id, scope, title, verification_status, produced_by_run_id "
            "FROM assessment_snapshots"
        ).fetchall() == [
            (
                "snapshot_vera_v9_global",
                "global",
                "Self-Assessment — Global",
                "supported",
                "run_vera_v9",
            )
        ]
        assert [
            row[0] for row in connection.execute("SELECT id FROM self_claims")
        ] == ["claim_vera_v9_global"]
        assert [
            row[0]
            for row in connection.execute("SELECT id FROM verification_findings")
        ] == ["finding_vera_v9_global"]
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(assessment_snapshots)")
        }
        assert "scope_target" not in columns
        assert "scope" in columns
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assessment_snapshots SET scope = 'project' WHERE id = ?",
                ("snapshot_vera_v9_global",),
            )
        assert connection.execute(
            "SELECT raw_text FROM raw_logs WHERE id = 'log_vera_v9'"
        ).fetchone()[0] == "Vera Example retained raw record."
        assert connection.execute(
            "SELECT COUNT(*) FROM experience_facts"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM fact_sources"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT version FROM schema_meta ORDER BY version"
        ).fetchall() == [(index,) for index in range(1, 13)]
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        } >= {
            "assessment_snapshots_lifecycle_update_guard",
            "assessment_snapshots_owner_delete_guard",
            "self_claims_owner_delete_guard",
            "verification_findings_owner_delete_guard",
        }


def test_v9_to_v10_reports_a_managed_set_it_cannot_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§12.14: stranded managed output is reported, never silently kept."""

    workspace = v9_workspace(tmp_path)
    published_set(workspace, "snapshot_vera_v9_global")
    # A symlink is never followed and never removed, so its path is exactly
    # the residual the cleanup has to surface.
    link = workspace / "out" / "assessment" / "snapshot_vera_v9_project"
    link.symlink_to(tmp_path)

    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    envelope = json.loads(result.stdout)

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "managed_output_incomplete"
    assert envelope["residual_paths"] == [str(link)]
    assert link.is_symlink()
    assert envelope["result"]["schema"]["stored_version"] == 12
    assert inspect_workspace(workspace).stored_version == 12


def test_v7_to_v8_rebuild_preserves_rows_guards_indexes_and_admits_open_ranges(
    tmp_path: Path,
) -> None:
    """§12 rule 5/§21.53: v8 is a byte-preserving two-table rebuild."""

    workspace = v7_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    closed_start = "2026-04-01T00:00:00+02:00"
    closed_end = "2026-07-01T00:00:00+02:00"
    recorded_at = "2026-07-15T14:30:00+02:00"
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, project_key, external_ref, corrects_log_id, metadata_json
            ) VALUES (
                'log_vera_v7', ?, 'manual_retro', 'user_memory', ?, ?,
                'approximate_range', 'medium',
                'Vera Example retained closed period.', NULL, NULL, NULL, NULL,
                '{"fixture":"Vera Example"}'
            )
            """,
            (recorded_at, closed_start, closed_end),
        )
        connection.execute(
            """
            INSERT INTO evidence_items(
                id, created_at, raw_log_id, title, summary, uri, path,
                strength, metadata_json
            ) VALUES (
                'evi_vera_v7', ?, 'log_vera_v7', NULL,
                'Vera Example retained manual support.', NULL, NULL,
                'manual_claim', '{}'
            )
            """,
            (recorded_at,),
        )
        connection.execute(
            """
            INSERT INTO processing_runs(
                id, stage, started_at, status, input_ids_json, output_ids_json,
                metadata_json
            ) VALUES (
                'run_vera_v7', '13.3', ?, 'completed',
                '["log_vera_v7"]', '["fact_vera_v7"]', '{}'
            )
            """,
            (recorded_at,),
        )
        connection.execute(
            """
            INSERT INTO experience_facts(
                id, created_at, superseded_at, claim, claim_kind, project,
                project_key, role, company, context, ownership_level, action,
                object, outcome, skills_json, technologies_json, themes_json,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, confidence, metadata_json,
                produced_by_run_id, generation_id
            ) VALUES (
                'fact_vera_v7', ?, NULL,
                'Vera Example retained migration fact.', 'observed_fact',
                NULL, NULL, NULL, NULL, 'independent_project', 'built',
                'built', 'a migration fixture', NULL, '[]', '["SQLite"]', '[]',
                ?, ?, 'approximate_range', 'medium', 'medium', '{}',
                'run_vera_v7', 'gen_vera_v7'
            )
            """,
            (recorded_at, closed_start, closed_end),
        )
        connection.execute(
            """
            INSERT INTO fact_sources(fact_id, evidence_item_id, support_type)
            VALUES ('fact_vera_v7', 'evi_vera_v7', 'direct')
            """
        )
        for statement in (
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, project_key, external_ref, corrects_log_id, metadata_json
            ) VALUES (
                'log_vera_open', ?, 'manual_retro', 'user_memory', ?, NULL,
                'date_range', 'medium', 'Vera Example open period.',
                NULL, NULL, NULL, NULL, '{}'
            )
            """,
            """
            INSERT INTO experience_facts(
                id, created_at, superseded_at, claim, claim_kind, project,
                project_key, role, company, context, ownership_level, action,
                object, outcome, skills_json, technologies_json, themes_json,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, confidence, metadata_json,
                produced_by_run_id, generation_id
            ) VALUES (
                'fact_vera_open', ?, NULL, 'Vera Example open fact.',
                'observed_fact', NULL, NULL, NULL, NULL,
                'independent_project', 'built', NULL, NULL, NULL,
                '[]', '[]', '[]', ?, NULL, 'date_range', 'medium', 'medium',
                '{}', 'run_vera_v7', 'gen_vera_open'
            )
            """,
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, (recorded_at, closed_start))
        retained_before = {
            table: connection.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
            for table in (
                "raw_logs",
                "evidence_items",
                "processing_runs",
                "experience_facts",
                "fact_sources",
            )
        }
        # The v9 step legitimately drops the signal layer's own objects, the
        # v11 step adds the job-description layer's, and the v12 step adds the
        # branch/bullet layer's, so the parity this test owns is over
        # everything else (issue #76).
        added_or_removed = (
            "self_signals",
            "job_descriptions",
            "resume_branches",
            "resume_bullets",
        )
        indexes_before = [
            row for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name"
            ).fetchall()
            if not any(name in row[0] for name in added_or_removed)
        ]
        triggers_before = [
            row for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            ).fetchall()
            if not any(name in row[0] for name in added_or_removed)
        ]

    migrated = migrate_workspace(
        workspace, clock=lambda: FIXED_NOW.replace(day=16)
    )
    assert migrated.stored_version == 12
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        retained_after = {
            table: connection.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
            for table in retained_before
        }
        assert retained_after == retained_before
        assert [
            row for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name"
            ).fetchall()
            if not any(name in row[0] for name in added_or_removed)
        ] == indexes_before
        assert [
            row for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            ).fetchall()
            if not any(name in row[0] for name in added_or_removed)
        ] == triggers_before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="raw_log_immutable"):
            connection.execute(
                "UPDATE raw_logs SET raw_text = 'Vera Example changed' "
                "WHERE id = 'log_vera_v7'"
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="experience_fact_lifecycle_only"
        ):
            connection.execute(
                "UPDATE experience_facts SET claim = 'Vera Example changed' "
                "WHERE id = 'fact_vera_v7'"
            )
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, project_key, external_ref, corrects_log_id, metadata_json
            ) VALUES (
                'log_vera_open', ?, 'manual_retro', 'user_memory', ?, NULL,
                'date_range', 'medium', 'Vera Example open period.',
                NULL, NULL, NULL, NULL, '{}'
            )
            """,
            (recorded_at, closed_start),
        )
        connection.execute(
            """
            INSERT INTO experience_facts(
                id, created_at, superseded_at, claim, claim_kind, project,
                project_key, role, company, context, ownership_level, action,
                object, outcome, skills_json, technologies_json, themes_json,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, confidence, metadata_json,
                produced_by_run_id, generation_id
            ) VALUES (
                'fact_vera_open', ?, NULL, 'Vera Example open fact.',
                'observed_fact', NULL, NULL, NULL, NULL,
                'independent_project', 'built', NULL, NULL, NULL,
                '[]', '[]', '[]', ?, NULL, 'date_range', 'medium', 'medium',
                '{}', 'run_vera_v7', 'gen_vera_open'
            )
            """,
            (recorded_at, closed_start),
        )
        assert connection.execute(
            "SELECT occurred_end FROM raw_logs WHERE id = 'log_vera_open'"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT occurred_end FROM experience_facts "
            "WHERE id = 'fact_vera_open'"
        ).fetchone() == (None,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    ("start_version", "failure_point"),
    [
        (1, "after_migration_1_to_2"),
        (1, "after_migration_2_to_3"),
        (1, "after_migration_3_to_4"),
        (1, "after_migration_4_to_5"),
        (1, "after_migration_5_to_6"),
        (1, "after_migration_6_to_7"),
        (1, "after_migration_7_to_8"),
        (2, "after_migration_2_to_3"),
        (2, "after_migration_3_to_4"),
        (2, "after_migration_4_to_5"),
        (2, "after_migration_5_to_6"),
        (2, "after_migration_6_to_7"),
        (2, "after_migration_7_to_8"),
        (3, "after_migration_3_to_4"),
        (3, "after_migration_4_to_5"),
        (3, "after_migration_5_to_6"),
        (3, "after_migration_6_to_7"),
        (3, "after_migration_7_to_8"),
        (4, "after_migration_4_to_5"),
        (4, "after_migration_5_to_6"),
        (4, "after_migration_6_to_7"),
        (4, "after_migration_7_to_8"),
        (5, "after_migration_5_to_6"),
        (5, "after_migration_6_to_7"),
        (5, "after_migration_7_to_8"),
        (6, "after_migration_6_to_7"),
        (6, "after_migration_7_to_8"),
        (7, "after_migration_7_to_8"),
    ],
)
def test_each_registered_step_failure_rolls_back_to_the_original_version(
    tmp_path: Path, start_version: int, failure_point: str
) -> None:
    """§12.14: every pending step shares one rollback boundary."""

    if start_version == 1:
        workspace, _log_id, _raw_text = v1_workspace(tmp_path)
    elif start_version == 2:
        workspace = v2_workspace(tmp_path)
    elif start_version == 3:
        workspace = v3_workspace(tmp_path)
    elif start_version == 4:
        workspace = v4_workspace(tmp_path)
    elif start_version == 5:
        workspace = v5_workspace(tmp_path)
    elif start_version == 6:
        workspace = v6_workspace(tmp_path)
    else:
        workspace = v7_workspace(tmp_path)

    def inject(point: str) -> None:
        if point == failure_point:
            raise RuntimeError("Vera Example registered-step failure")

    with pytest.raises(MigrationFailedError) as caught:
        migrate_workspace(
            workspace,
            clock=lambda: FIXED_NOW.replace(day=16),
            failure_injector=inject,
        )
    backup = Path(caught.value.managed_backup_path or "")
    assert backup.is_file()
    assert f"exp2res-v{start_version}-" in backup.name
    assert inspect_workspace(workspace).stored_version == start_version
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT MAX(version) FROM schema_meta").fetchone()[0] == start_version


def test_blank_project_backfill_fails_closed_and_leaves_v2_usable(
    tmp_path: Path,
) -> None:
    """§11 policy/§12 rule 14: non-transformable retained labels abort v3."""

    workspace = v2_workspace(
        tmp_path, projects=(("log_blank_project", "   "),)
    )
    with pytest.raises(MigrationFailedError) as caught:
        migrate_workspace(workspace, clock=lambda: FIXED_NOW.replace(day=16))
    assert Path(caught.value.managed_backup_path or "").is_file()
    assert caught.value.failure_code == (
        "migration_2_to_3:raw_log_project_label_blank"
    )
    assert inspect_workspace(workspace).stored_version == 2
    assert isinstance(caught.value.__cause__, ValueError)
    assert str(caught.value.__cause__) == "raw_log_project_label_blank"
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT project FROM raw_logs WHERE id = 'log_blank_project'"
        ).fetchone()[0] == "   "
        assert connection.execute(
            "SELECT 1 FROM pragma_table_info('raw_logs') WHERE name = 'project_key'"
        ).fetchone() is None


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
    """§12.14: an injected target-validation failure exposes no partial v3."""

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

    with pytest.raises(KeyboardInterrupt) as interrupt_info:
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
    # §14.14 rule 4: the committed effect rides along for the cancelled
    # envelope instead of being dropped with a bare re-raise.
    assert isinstance(interrupt_info.value, MigrationInterrupted)
    assert interrupt_info.value.managed_backup_path == str(backups[0])


def test_cli_pre_backup_interrupt_keeps_the_generic_null_result_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: no committed effect means the generic cancel shape."""

    import exp2res.cli as cli_module

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)

    def interrupted_before_backup(_target: Path):
        raise MigrationInterrupted(managed_backup_path=None)

    monkeypatch.setattr(cli_module, "migrate_workspace", interrupted_before_backup)
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    assert result.exit_code == 9
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "cancelled"
    assert envelope["diagnostic_class"] == "cancelled"
    assert envelope["result"] is None


def test_post_commit_interrupt_reports_backup_and_leaves_durable_v8(
    tmp_path: Path,
) -> None:
    """§14.14 rule 4: a post-commit interrupt still reports both effects."""

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)

    def interrupt_after_commit(point: str) -> None:
        if point == "after_commit":
            raise KeyboardInterrupt

    with pytest.raises(MigrationInterrupted) as interrupt_info:
        migrate_workspace(
            workspace,
            clock=lambda: FIXED_NOW.replace(day=16),
            failure_injector=interrupt_after_commit,
        )

    backups = list((workspace / ".exp2res" / "backup").iterdir())
    assert len(backups) == 1
    assert interrupt_info.value.managed_backup_path == str(backups[0])
    after = inspect_workspace(workspace)
    assert after.stored_version == 12
    assert after.compatible is True


def test_cli_post_commit_interrupt_envelope_reports_durable_v8_and_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: the cancelled envelope shows the committed migration."""

    import exp2res.cli as cli_module

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)

    def interrupt_after_commit(point: str) -> None:
        if point == "after_commit":
            raise KeyboardInterrupt

    def interrupted_migration(target: Path):
        return migrate_workspace(
            target,
            clock=lambda: FIXED_NOW.replace(day=16),
            failure_injector=interrupt_after_commit,
        )

    monkeypatch.setattr(cli_module, "migrate_workspace", interrupted_migration)
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    assert result.exit_code == 9
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "cancelled"
    assert envelope["result"]["schema"]["stored_version"] == 12
    assert envelope["result"]["schema"]["compatible"] is True
    backup = Path(envelope["result"]["schema"]["managed_backup_path"])
    assert backup.is_file()


def test_cli_reports_retained_backup_in_the_cancelled_migration_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: cancellation still reports the retained backup path."""

    import exp2res.cli as cli_module

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)

    def interrupt_before_validation(point: str) -> None:
        if point == "before_validation":
            raise KeyboardInterrupt

    def interrupted_migration(target: Path):
        return migrate_workspace(
            target,
            clock=lambda: FIXED_NOW.replace(day=16),
            failure_injector=interrupt_before_validation,
        )

    monkeypatch.setattr(cli_module, "migrate_workspace", interrupted_migration)
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["--json", "--yes", "db", "migrate"])
    assert result.exit_code == 9
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "cancelled"
    assert envelope["diagnostic_class"] == "cancelled"
    assert envelope["result"]["schema"]["stored_version"] == 1
    backup = Path(envelope["result"]["schema"]["managed_backup_path"])
    assert backup.is_file()


@pytest.mark.parametrize("table", ["processing_runs", "llm_calls"])
def test_extra_trigger_on_exact_telemetry_table_fails_migration(
    tmp_path: Path, table: str
) -> None:
    """§12.13/§12.15: a rider trigger on an exact table fails validation."""

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    ddl = PROCESSING_RUNS_SQL if table == "processing_runs" else LLM_CALLS_SQL
    with sqlite3.connect(database) as connection:
        connection.execute(ddl)
        connection.execute(
            f"""
            CREATE TRIGGER stale_rider_guard
            BEFORE INSERT ON {table}
            BEGIN
                SELECT RAISE(ABORT, 'stale rider');
            END
            """
        )

    with pytest.raises(MigrationFailedError):
        migrate_workspace(workspace, clock=lambda: FIXED_NOW.replace(day=16))

    assert inspect_workspace(workspace).stored_version == 1
    with sqlite3.connect(database) as connection:
        rider = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            " AND name = 'stale_rider_guard'"
        ).fetchone()
    assert rider is not None


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
    ("table", "stale_ddl", "normative_ddl"),
    [
        (
            "processing_runs",
            """
            CREATE TABLE processing_runs (
                id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                parent_run_id TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                prompt_policy_hash TEXT,
                failure_code TEXT,
                input_ids_json TEXT NOT NULL DEFAULT '[]',
                output_ids_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            PROCESSING_RUNS_SQL,
        ),
        (
            "llm_calls",
            """
            CREATE TABLE llm_calls (
                run_id TEXT NOT NULL,
                call_index INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                input_hash TEXT,
                output_hash TEXT,
                provider_request_id TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                reported_cost TEXT,
                transport_retries INTEGER,
                schema_retries INTEGER,
                failure_code TEXT,

                PRIMARY KEY (run_id, call_index)
            )
            """,
            LLM_CALLS_SQL,
        ),
    ],
)
def test_constraint_free_same_column_telemetry_table_fails_migration(
    tmp_path: Path, table: str, stale_ddl: str, normative_ddl: str
) -> None:
    """§12.13/§12.15: missing REFERENCES/CHECK constraints fail validation."""

    workspace, _log_id, _raw_text = v1_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(stale_ddl)
    # The stale table is indistinguishable from the normative shape by
    # PRAGMA table_info alone; only its constraint clauses differ.
    assert table_shape(database, table) == normative_shape(normative_ddl, table)

    with pytest.raises(MigrationFailedError):
        migrate_workspace(workspace, clock=lambda: FIXED_NOW.replace(day=16))

    assert inspect_workspace(workspace).stored_version == 1
    assert table_shape(database, table) == normative_shape(normative_ddl, table)


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

    assert migrated.stored_version == 12
    assert migrated.compatible is True
    assert table_shape(database, table) == expected_shape


def v10_workspace(tmp_path: Path, *, name: str = "v10-workspace") -> Path:
    """A v10 workspace holding one raw lineage and its evidence."""

    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    stamp = FIXED_NOW.isoformat()
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V10_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, stamp, f"0.1.0-v{version}-fixture")
                for version in range(1, 11)
            ),
        )
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, project_key, external_ref, corrects_log_id, metadata_json
            ) VALUES (
                'log_vera_v10', ?, 'manual_daily', 'manual_entry', ?, NULL,
                'exact_day', 'high', 'Vera Example retained raw record.',
                NULL, NULL, NULL, NULL, '{}'
            )
            """,
            (stamp, stamp),
        )
        connection.execute(
            """
            INSERT INTO evidence_items(
                id, created_at, raw_log_id, title, summary, uri, path,
                strength, metadata_json
            ) VALUES (
                'evi_vera_v10', ?, 'log_vera_v10', NULL,
                'Vera Example retained support.', NULL, NULL,
                'manual_claim', '{}'
            )
            """,
            (stamp,),
        )
    database.chmod(0o600)
    return root


def test_v10_to_v11_adds_the_job_description_layer_and_keeps_prior_rows(
    tmp_path: Path,
) -> None:
    """§12.14/§13.8: the additive job-description step touches no prior row."""

    workspace = v10_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    retained_before = {
        table: table_rows(database, table)
        for table in ("raw_logs", "evidence_items", "schema_meta")
    }

    migrated = migrate_workspace(
        workspace, clock=lambda: FIXED_NOW.replace(day=16)
    )

    assert migrated.stored_version == 12
    assert migrated.compatible is True
    assert table_shape(database, "job_descriptions") == normative_shape(
        JOB_DESCRIPTIONS_SQL, "job_descriptions"
    )
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        } >= {
            "job_descriptions_update_guard",
            "job_descriptions_owner_delete_guard",
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    retained_after = {
        table: table_rows(database, table)
        for table in ("raw_logs", "evidence_items")
    }
    assert retained_after == {
        table: rows
        for table, rows in retained_before.items()
        if table != "schema_meta"
    }
    # The only schema_meta change is the appended v11 row (§12.14).
    assert table_rows(database, "schema_meta")[:10] == retained_before["schema_meta"]
    assert table_rows(database, "schema_meta")[11][0] == 12


def v11_workspace(tmp_path: Path, *, name: str = "v11-workspace") -> Path:
    """A v11 workspace holding a raw lineage, a view, and a job description.

    Every layer the branch/bullet substrate references already exists here, so
    the 11→12 step has real rows to leave alone and real foreign-key targets.
    """

    root = tmp_path / name
    root.mkdir()
    (root / ".exp2res").mkdir(mode=0o700)
    (root / ".exp2res" / "lock").touch(mode=0o600)
    (root / "out").mkdir(mode=0o700)
    configure_timezone(root)
    database = root / ".exp2res" / "exp2res.sqlite"
    stamp = FIXED_NOW.isoformat()
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_V11_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
            tuple(
                (version, stamp, f"0.1.0-v{version}-fixture")
                for version in range(1, 12)
            ),
        )
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence, raw_text,
                project, project_key, external_ref, corrects_log_id, metadata_json
            ) VALUES (
                'log_vera_v11', ?, 'manual_daily', 'manual_entry', ?, NULL,
                'exact_day', 'high', 'Vera Example retained raw record.',
                NULL, NULL, NULL, NULL, '{}'
            )
            """,
            (stamp, stamp),
        )
        connection.execute(
            """
            INSERT INTO processing_runs(
                id, stage, started_at, status, input_ids_json, output_ids_json,
                metadata_json
            ) VALUES (
                'run_vera_v11', '13.6', ?, 'completed', '[]', '[]', '{}'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO assessment_snapshots(
                id, created_at, superseded_at, scope, title, summary,
                gap_question_ids_json, contradiction_ids_json,
                verification_status, metadata_json, produced_by_run_id,
                generation_id
            ) VALUES (
                'snapshot_vera_v11', ?, NULL, 'global',
                'Self-Assessment — Global',
                'Current evidence suggests a provenance-aware workflow.',
                '[]', '[]', 'supported', '{}', 'run_vera_v11', 'gen_vera_v11'
            )
            """,
            (stamp,),
        )
        connection.execute(
            """
            INSERT INTO job_descriptions(
                id, created_at, title, company, raw_text, parsed_json
            ) VALUES (
                'jd_vera_v11', ?, 'Agent Engineer', 'Example Co',
                'Vera Example vacancy text.', '{}'
            )
            """,
            (stamp,),
        )
    database.chmod(0o600)
    return root


def test_v11_to_v12_adds_the_branch_substrate_and_keeps_prior_rows(
    tmp_path: Path,
) -> None:
    """§12.14/§13.10: the additive branch/bullet step touches no prior row."""

    workspace = v11_workspace(tmp_path)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    retained_tables = (
        "raw_logs",
        "processing_runs",
        "assessment_snapshots",
        "job_descriptions",
    )
    retained_before = {
        table: table_rows(database, table)
        for table in (*retained_tables, "schema_meta")
    }

    migrated = migrate_workspace(
        workspace, clock=lambda: FIXED_NOW.replace(day=16)
    )

    assert migrated.stored_version == 12
    assert migrated.compatible is True
    assert table_shape(database, "resume_branches") == normative_shape(
        RESUME_BRANCHES_SQL, "resume_branches"
    )
    assert table_shape(database, "resume_bullets") == normative_shape(
        RESUME_BULLETS_SQL, "resume_bullets"
    )
    with sqlite3.connect(database) as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        for table in ("resume_branches", "resume_bullets"):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        } >= {
            "resume_branches_lifecycle_update_guard",
            "resume_branches_owner_delete_guard",
            "resume_bullets_lifecycle_update_guard",
            "resume_bullets_owner_delete_guard",
        }
        # §12 rule 12's exact-spelling backstop arrives with its table.
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("resume_branches_current_name_unique",),
        ).fetchone() is not None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert {
        table: table_rows(database, table) for table in retained_tables
    } == {
        table: rows
        for table, rows in retained_before.items()
        if table != "schema_meta"
    }
    # The only schema_meta change is the appended v12 row (§12.14).
    assert table_rows(database, "schema_meta")[:11] == retained_before["schema_meta"]
    assert table_rows(database, "schema_meta")[11][0] == 12
