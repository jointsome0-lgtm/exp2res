"""Raw-layer authority, owner deletion, and backup/restore tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from exp2res.cli import app
import exp2res.services.lifecycle as lifecycle_service
import exp2res.services.logs as logs_service
from exp2res.services.capture import capture_daily
from exp2res.services.logs import delete_log, list_logs, show_log
from exp2res.domain.models import EvidenceItem, ExperienceFact, RawLog, SelfSignal
from exp2res.storage.repository import (
    insert_evidence_item,
    insert_experience_fact,
    insert_raw_log,
    insert_self_signal,
    mark_facts_superseded,
)
from exp2res.storage.telemetry import (
    create_llm_call,
    create_processing_run,
    finish_llm_call,
    finish_processing_run,
)
from exp2res.storage.workspace import initialize_workspace, writer_database

from conftest import FIXED_NOW, configure_timezone
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets, fact_response


pytestmark = pytest.mark.lifecycle
runner = CliRunner()


def _persist_facts_and_completed_calls(workspace: Path):
    selected = capture_daily(
        workspace,
        raw_text="Vera Example selected deletion source",
        clock=lambda: FIXED_NOW,
    )
    retained = capture_daily(
        workspace,
        raw_text="Vera Example unrelated deletion source",
        clock=lambda: FIXED_NOW.replace(hour=13),
    )
    facts = (
        ExperienceFact(
            id="fact_" + "a" * 32,
            created_at=FIXED_NOW,
            claim="Vera Example selected derived fact.",
            context="independent_project",
            ownership_level="built",
            occurred=selected.raw_log.occurred,
            source_log_ids=[selected.raw_log.id],
            evidence_item_ids=[selected.evidence_items[0].id],
            confidence="high",
        ),
        ExperienceFact(
            id="fact_" + "b" * 32,
            created_at=FIXED_NOW.replace(hour=13),
            claim="Vera Example unrelated derived fact.",
            context="independent_project",
            ownership_level="built",
            occurred=retained.raw_log.occurred,
            source_log_ids=[retained.raw_log.id],
            evidence_item_ids=[retained.evidence_items[0].id],
            confidence="high",
        ),
    )
    run_id = "run_" + "d" * 32
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        create_processing_run(
            connection,
            run_id=run_id,
            stage="13.3",
            started_at=FIXED_NOW,
            provider="fake",
            model="fake-model",
            prompt_policy_hash="a" * 64,
            input_ids=[selected.raw_log.id, retained.raw_log.id],
        )
        for call_index in (1, 2):
            create_llm_call(
                connection,
                run_id=run_id,
                call_index=call_index,
                started_at=FIXED_NOW,
                input_hash=str(call_index) * 64,
                provider_request_id=f"req_vera_{call_index}",
            )
            finish_llm_call(
                connection,
                run_id=run_id,
                call_index=call_index,
                finished_at=FIXED_NOW,
                status="completed",
                output_hash=hex(call_index + 10)[2:] * 64,
            )
        for fact in facts:
            insert_experience_fact(
                connection,
                fact,
                produced_by_run_id=run_id,
                generation_id="gen_" + fact.id[-32:],
            )
        mark_facts_superseded(
            connection,
            [facts[1].id],
            FIXED_NOW.replace(hour=14),
        )
        finish_processing_run(
            connection,
            run_id=run_id,
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=[fact.id for fact in facts],
        )
        connection.commit()
    return selected, retained, facts


def _persist_signal(workspace: Path, fact_id: str) -> SelfSignal:
    signal = SelfSignal(
        id="signal_" + "f" * 32,
        created_at=FIXED_NOW,
        signal_type="execution_pattern",
        statement="Vera Example repeats a provenance-aware workflow.",
        supporting_fact_ids=[fact_id],
        confidence="medium",
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        create_processing_run(
            connection,
            run_id="run_" + "f" * 32,
            stage="13.5",
            started_at=FIXED_NOW,
            provider="fake",
            model="fake-model",
            prompt_policy_hash="f" * 64,
            input_ids=[fact_id],
        )
        insert_self_signal(
            connection,
            signal,
            produced_by_run_id="run_" + "f" * 32,
            generation_id="gen_" + "f" * 32,
        )
        finish_processing_run(
            connection,
            run_id="run_" + "f" * 32,
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=[signal.id],
        )
        connection.commit()
    return signal


def test_automation_cannot_rewrite_or_delete_but_owner_deletion_cascades(
    workspace: Path,
) -> None:
    """§21.11 / §21.14; §24.3 / §24.17: actor-scoped raw authority."""
    marker = "Vera Example immutable raw sentinel"
    bundle = capture_daily(workspace, raw_text=marker, clock=lambda: FIXED_NOW)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "UPDATE raw_logs SET raw_text = ? WHERE id = ?",
                ("Vera Example forbidden rewrite", bundle.raw_log.id),
            )
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM raw_logs WHERE id = ?", (bundle.raw_log.id,))
    assert show_log(workspace, log_id=bundle.raw_log.id).raw_log.raw_text == marker

    outcome = delete_log(workspace, log_id=bundle.raw_log.id)
    assert outcome.residual_paths == ()
    assert list_logs(workspace) == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 0
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
    for path in (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    ):
        if path.exists():
            assert marker.encode("utf-8") not in path.read_bytes()


def test_delete_preserves_external_source_and_does_not_follow_backup_symlink(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.14; §24.17: database deletion commits despite safe cleanup residual."""
    external = tmp_path / "Vera Example source.md"
    external.write_text("Vera Example external source remains owner-controlled\n", encoding="utf-8")
    from exp2res.services.capture import capture_daily_file

    bundle = capture_daily_file(
        workspace, source_path=str(external), clock=lambda: FIXED_NOW
    )
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700)
    outside = tmp_path / "Vera Example outside backup.sqlite"
    outside.write_text("Vera Example outside target\n", encoding="utf-8")
    planted = backup_root / "exp2res-v1-Vera-Example.sqlite"
    planted.symlink_to(outside)

    outcome = delete_log(workspace, log_id=bundle.raw_log.id)
    assert list_logs(workspace) == ()
    assert external.exists()
    assert outside.read_text(encoding="utf-8") == "Vera Example outside target\n"
    assert planted.exists()
    assert outcome.residual_paths == (str(planted.absolute()),)


def test_delete_reports_undecodable_backup_entry_without_blocking_purge(
    workspace: Path, tmp_path: Path
) -> None:
    bundle = capture_daily(
        workspace,
        raw_text="Vera Example undecodable backup-entry source",
        clock=lambda: FIXED_NOW,
    )
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700)
    outside = tmp_path / "Vera Example undecodable backup target"
    outside.write_text("Vera Example outside target\n", encoding="utf-8")
    planted = backup_root / os.fsdecode(b"exp2res-v1-Vera-Example-\xfe.sqlite")
    planted.symlink_to(outside)

    outcome = delete_log(workspace, log_id=bundle.raw_log.id)

    assert list_logs(workspace) == ()
    assert planted.is_symlink()
    assert outside.read_text(encoding="utf-8") == "Vera Example outside target\n"
    assert len(outcome.residual_paths) == 1
    assert os.fsencode(outcome.residual_paths[0]) == os.fsencode(
        str(planted.absolute())
    )


def test_logs_delete_removes_every_managed_set_and_reports_cleanup_residual(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = capture_daily(
        workspace,
        raw_text="Vera Example managed-output deletion source",
        clock=lambda: FIXED_NOW,
    )
    managed_entries = []
    for parent_name, entry_name in (
        ("assessment", "snapshot_vera_delete"),
        ("branch", "branch_vera_delete"),
    ):
        parent = workspace / "out" / parent_name
        parent.mkdir(mode=0o700, exist_ok=True)
        entry = parent / entry_name
        entry.mkdir(mode=0o700)
        (entry / "Vera Example member").write_text(
            "Vera Example managed member\n", encoding="utf-8"
        )
        managed_entries.append(entry)

    removed = delete_log(workspace, log_id=bundle.raw_log.id)
    assert removed.residual_paths == ()
    assert all(not path.exists() for path in managed_entries)

    second = capture_daily(
        workspace,
        raw_text="Vera Example residual deletion source",
        clock=lambda: FIXED_NOW.replace(hour=13),
    )
    residual = str(workspace / "out" / "branch" / "branch_vera_residual")
    monkeypatch.setattr(
        logs_service,
        "remove_all_managed_output_entries",
        lambda _workspace: (residual,),
    )
    incomplete = delete_log(workspace, log_id=second.raw_log.id)
    assert incomplete.residual_paths == (residual,)
    assert list_logs(workspace) == ()


def test_owner_delete_re_roots_retained_correction_without_fk_block(
    workspace: Path,
) -> None:
    """§21.11; §24.3: ON DELETE SET NULL cannot block selected raw deletion."""
    target = capture_daily(
        workspace,
        raw_text="Vera Example original record",
        clock=lambda: FIXED_NOW,
    )
    correction = RawLog(
        id="log_vera_correction",
        recorded_at=FIXED_NOW.replace(hour=13),
        entry_type="correction",
        source_type="manual_entry",
        occurred=target.raw_log.occurred,
        raw_text="Vera Example self-contained corrected record",
        project=None,
        external_ref=None,
        corrects_log_id=target.raw_log.id,
        metadata={},
    )
    evidence = EvidenceItem(
        id="evi_vera_correction",
        created_at=correction.recorded_at,
        raw_log_id=correction.id,
        title=None,
        summary="Owner-authored manual claim.",
        uri=None,
        path=None,
        strength="manual_claim",
        metadata={},
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        insert_raw_log(connection, correction)
        insert_evidence_item(connection, evidence)
        connection.commit()

    delete_log(workspace, log_id=target.raw_log.id)
    retained = show_log(workspace, log_id=correction.id)
    assert retained.raw_log.corrects_log_id is None
    assert retained.raw_log.raw_text == correction.raw_text
    assert retained.evidence_items == (evidence,)


def test_sqlite_backup_restore_preserves_phase0_records(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.36; §24.39: WAL-aware SQLite backup/restore preserves Stage 1 rows."""
    bundle = capture_daily(
        workspace,
        raw_text="Vera Example backup and restore record",
        project="K8s Playbook",
        clock=lambda: FIXED_NOW,
    )
    backup = tmp_path / "Vera Example external backup.sqlite"
    source_db = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(source_db) as source, sqlite3.connect(backup) as target:
        source.backup(target)

    restored = tmp_path / "restored-private-workspace"
    restored.mkdir()
    initialize_workspace(restored, clock=lambda: FIXED_NOW)
    configure_timezone(restored)
    restored_db = restored / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(backup) as source, sqlite3.connect(restored_db) as target:
        source.backup(target)

    restored_bundle = show_log(restored, log_id=bundle.raw_log.id)
    assert restored_bundle.raw_log == bundle.raw_log
    assert restored_bundle.evidence_items == bundle.evidence_items


def test_owner_delete_globally_purges_facts_and_redacts_all_call_hashes(
    workspace: Path,
) -> None:
    """§13.13 rule 5: one raw deletion purges all facts and all content hashes."""

    selected, retained, facts = _persist_facts_and_completed_calls(workspace)
    outcome = delete_log(workspace, log_id=selected.raw_log.id)
    assert outcome.purged_fact_ids == tuple(fact.id for fact in facts)
    assert [item.id for item in list_logs(workspace)] == [retained.raw_log.id]
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM experience_facts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM fact_sources").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 1
        assert connection.execute(
            "SELECT input_hash, output_hash FROM llm_calls ORDER BY call_index"
        ).fetchall() == [(None, None), (None, None)]
        assert connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0] == 1


def test_cli_owner_delete_reports_global_experience_fact_group(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14: logs delete reports purged fact IDs in deterministic type order."""

    selected, retained, facts = _persist_facts_and_completed_calls(workspace)
    signal = _persist_signal(workspace, facts[0].id)
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [
                    fact_response([retained.evidence_items[0].id]),
                    b'{"gap_questions":[],"contradictions":[],"warnings":[]}',
                    b'{"signals":[],"warnings":[]}',
                ]
            ),
        ),
    )
    monkeypatch.chdir(workspace)
    result = runner.invoke(
        app,
        ["--json", "--yes", "logs", "delete", "--log-id", selected.raw_log.id],
    )
    assert result.exit_code == 0, result.stderr
    envelope = json.loads(result.stdout)
    deleted = envelope["affected_ids"]["deleted"]
    assert [group["entity_type"] for group in deleted] == [
        "evidence_item",
        "experience_fact",
        "self_signal",
        "raw_log",
    ]
    assert deleted[1]["ids"] == [fact.id for fact in facts]
    assert deleted[2]["ids"] == [signal.id]


def test_owner_delete_purges_detections_and_answer_log_fk_cannot_block(
    workspace: Path,
) -> None:
    """§13.13 rule 5: detections purge with the reset, and the answered gap's
    answer_log_id ON DELETE SET NULL action cannot fire into the answered-iff
    CHECK and block owner deletion of the answer record."""

    from exp2res.domain.models import Contradiction, GapQuestion
    from exp2res.storage.repository import insert_contradiction, insert_gap_question

    selected, retained, facts = _persist_facts_and_completed_calls(workspace)
    signal = _persist_signal(workspace, facts[0].id)
    answer = capture_daily(
        workspace,
        raw_text="Vera Example answer record",
        clock=lambda: FIXED_NOW.replace(hour=15),
    )
    gap = GapQuestion(
        id="gap_" + "a" * 32,
        created_at=FIXED_NOW,
        target_type="experience_fact",
        target_id=facts[0].id,
        question="Vera Example open question?",
        reason="weak_evidence",
        priority="medium",
    )
    contradiction = Contradiction(
        id="contradiction_" + "a" * 32,
        created_at=FIXED_NOW,
        title="Vera Example conflict",
        description="Vera Example conflicting statements.",
        left_ref_type="experience_fact",
        left_ref_id=facts[0].id,
        right_ref_type="raw_log",
        right_ref_id=selected.raw_log.id,
    )
    detection_run = "run_" + "e" * 32
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        create_processing_run(
            connection,
            run_id=detection_run,
            stage="13.4",
            started_at=FIXED_NOW,
            provider="fake",
            model="fake-model",
            prompt_policy_hash="b" * 64,
            input_ids=[facts[0].id],
        )
        insert_gap_question(
            connection,
            gap,
            produced_by_run_id=detection_run,
            generation_id="gen_" + "e" * 32,
        )
        insert_contradiction(
            connection,
            contradiction,
            produced_by_run_id=detection_run,
            generation_id="gen_" + "e" * 32,
        )
        finish_processing_run(
            connection,
            run_id=detection_run,
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=[gap.id, contradiction.id],
        )
        connection.execute(
            "UPDATE gap_questions SET answered = 1, answer_log_id = ? WHERE id = ?",
            (answer.raw_log.id, gap.id),
        )
        connection.commit()

    outcome = delete_log(workspace, log_id=answer.raw_log.id)
    assert outcome.purged_gap_ids == (gap.id,)
    assert outcome.purged_contradiction_ids == (contradiction.id,)
    assert outcome.purged_signal_ids == (signal.id,)
    assert outcome.residual_paths == ()
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM gap_questions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM contradictions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM self_signals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM experience_facts").fetchone()[0] == 0
