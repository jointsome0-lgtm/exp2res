"""§14.9/§14.14 assessment-export CLI and writer-preamble coverage."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.pipeline.stage5 as stage5_module
import exp2res.pipeline.stage7 as stage7_module
import exp2res.services.assessment as assessment_service
import exp2res.services.capture as capture_service
import exp2res.services.signals as signals_service
from exp2res.cli import app
from exp2res.errors import ManagedOutputIncompleteError
from exp2res.storage.repository import list_raw_logs
from exp2res.storage.workspace import read_database, writer_database

from conftest import VERA_CORPUS
from fakes import FakeContractRunner
from test_stage7_verification import generated_snapshot, run_stage7, verifier_response
from test_stage3_extraction import SELECTION, budgets
from test_stage5_signals import signal_response


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(app, ["--json", "--workspace", str(workspace), *arguments])
    return result, json.loads(result.stdout)


def verified_snapshot(workspace: Path) -> str:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    return generated.snapshot_id


def test_export_assessment_closed_success_envelope_needs_no_yes(
    workspace: Path,
) -> None:
    snapshot_id = verified_snapshot(workspace)
    result, envelope = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )

    assert result.exit_code == 0
    assert envelope["command"] == "export assessment"
    assert envelope["result"].keys() == {"manifest_path", "managed_paths"}
    assert envelope["result"]["manifest_path"].endswith(
        f"/out/assessment/{snapshot_id}/manifest.json"
    )
    assert [Path(path).name for path in envelope["result"]["managed_paths"]] == [
        "evidence_map.json",
        "manifest.json",
        "report.md",
        "self_claims.json",
    ]


def test_export_assessment_selector_blocked_and_residual_classes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing, missing_envelope = invoke_json(
        workspace,
        ["export", "assessment", "--snapshot", "snapshot_vera_missing"],
    )
    assert missing.exit_code == 2
    assert missing_envelope["diagnostic_class"] == "selector_not_found"
    assert missing_envelope["result"] is None

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    blocked, blocked_envelope = invoke_json(
        workspace,
        ["export", "assessment", "--snapshot", generated.snapshot_id],
    )
    assert blocked.exit_code == 10
    assert blocked_envelope["diagnostic_class"] == "assessment_export_blocked"
    assert blocked_envelope["result"] is None

    # The residual class needs an export-eligible snapshot: the read-only
    # §16.11 gate now refuses ineligible ones before the writer path runs.
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    residual = str(workspace / "out" / "assessment" / "snapshot_vera_residual")

    def incomplete(_workspace: Path, *, snapshot_id: str):
        assert snapshot_id == generated.snapshot_id
        raise ManagedOutputIncompleteError((residual,))

    monkeypatch.setattr(cli_module, "export_assessment", incomplete)
    failed, failed_envelope = invoke_json(
        workspace,
        ["export", "assessment", "--snapshot", generated.snapshot_id],
    )
    assert failed.exit_code == 8
    assert failed_envelope["diagnostic_class"] == "managed_output_incomplete"
    assert failed_envelope["residual_paths"] == [residual]
    assert failed_envelope["result"] is None


def test_export_selector_resolves_before_preamble_cleanup_or_class_8(
    workspace: Path,
) -> None:
    # §14.14 rule 3: an unresolvable selector is class 2 even when an
    # unreconciliable residual would stop publication; the read-only resolve
    # runs before the writer preamble, which is neither run nor reported.
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = workspace.parent / "Vera Example selector target"
    target.mkdir()
    candidate = assessment / (
        ".exp2res-candidate-snapshot_vera_selector-" + "b" * 32
    )
    candidate.symlink_to(target, target_is_directory=True)

    result, envelope = invoke_json(
        workspace,
        ["export", "assessment", "--snapshot", "snapshot_vera_missing"],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "selector_not_found"
    assert envelope["residual_paths"] == []
    assert candidate.is_symlink()


def test_logs_delete_clears_a_preamble_residual_it_later_removed(
    workspace: Path,
) -> None:
    # An ambiguous rollback sibling is a preamble residual, but `logs delete`
    # then removes every entry under the managed parents; a reported path
    # that no longer exists at envelope assembly is not residual, so the
    # committed deletion reports success rather than class 8.
    source = VERA_CORPUS / "logs" / "daily-2026-06-09.md"
    captured, captured_envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    assert captured.exit_code == 0
    log_id = next(
        group["ids"][0]
        for group in captured_envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )

    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, parents=True, exist_ok=True)
    ambiguous = assessment / (
        ".exp2res-rollback-snapshot_vera_ambiguous-" + "d" * 32
    )
    ambiguous.mkdir(mode=0o700)
    (ambiguous / "Vera Example junk").write_text(
        "Vera Example not a manifest\n", encoding="utf-8"
    )

    # Non-vacuity: a non-destructive writer leaves the ambiguous sibling in
    # place and reports it, exactly the state the deletion must clear.
    retained, retained_envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    assert retained.exit_code == 8
    assert retained_envelope["residual_paths"] == [str(ambiguous)]
    assert ambiguous.is_dir()

    result, envelope = invoke_json(
        workspace, ["--yes", "logs", "delete", "--log-id", log_id]
    )
    assert result.exit_code == 0
    assert envelope["residual_paths"] == []
    assert not ambiguous.exists()


def test_unverified_snapshot_is_blocked_before_the_writer_path(
    workspace: Path,
) -> None:
    # §16.11 refusal on a current-but-ineligible snapshot is class 10 even
    # when an unreconciliable residual would stop publication with class 8:
    # the gate applies read-only before the writer preamble runs.
    _ids, _facts, _signals, generated = generated_snapshot(workspace)
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = workspace.parent / "Vera Example blocked target"
    target.mkdir()
    candidate = assessment / (
        ".exp2res-candidate-snapshot_vera_blocked-" + "c" * 32
    )
    candidate.symlink_to(target, target_is_directory=True)

    result, envelope = invoke_json(
        workspace,
        ["export", "assessment", "--snapshot", generated.snapshot_id],
    )
    assert result.exit_code == 10
    assert envelope["diagnostic_class"] == "assessment_export_blocked"
    assert envelope["residual_paths"] == []
    assert candidate.is_symlink()


def test_interrupted_invalidation_reports_the_stale_set_in_the_cancelled_envelope(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    exported, _envelope = invoke_json(
        workspace,
        ["export", "assessment", "--snapshot", generated.snapshot_id],
    )
    assert exported.exit_code == 0
    final_set = workspace / "out" / "assessment" / generated.snapshot_id

    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("unsupported") for _ in generated.claims]
            ),
        ),
    )

    def interrupt_removal(_workspace: Path, snapshot_ids):
        raise KeyboardInterrupt()

    monkeypatch.setattr(stage7_module, "remove_assessment_sets", interrupt_removal)
    result, envelope = invoke_json(
        workspace,
        ["--yes", "assess", "verify", "--snapshot", generated.snapshot_id],
    )
    # §14.14: the interrupt keeps class 9 precedence while the committed
    # verification change and the known stale-set path stay reported.
    assert result.exit_code == 9
    assert envelope["diagnostic_class"] == "cancelled"
    assert envelope["residual_paths"] == [str(final_set)]
    assert final_set.is_dir()


def test_undecodable_managed_entry_does_not_block_writers(
    workspace: Path,
) -> None:
    # A stray non-UTF-8 POSIX name under a managed parent must not raise out
    # of the every-writer preamble; it is not a reserved sibling, so ordinary
    # captures proceed and the entry is left in place.
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, parents=True, exist_ok=True)
    stray = assessment / os.fsdecode(b"vera-\xff-stray")
    stray.mkdir(mode=0o700)

    source = VERA_CORPUS / "logs" / "daily-2026-06-09.md"
    result, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    assert result.exit_code == 0
    assert envelope["residual_paths"] == []
    assert stray.is_dir()


def test_interrupted_gap_answer_cleanup_reports_the_stale_set(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = verified_snapshot(workspace)
    exported, _envelope = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert exported.exit_code == 0
    final_set = workspace / "out" / "assessment" / snapshot_id

    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        fact_id = connection.execute(
            "SELECT id FROM experience_facts LIMIT 1"
        ).fetchone()[0]
        run_id = connection.execute(
            "SELECT id FROM processing_runs LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO gap_questions (id, created_at, target_type, "
            "target_id, question, reason, priority, answered, "
            "produced_by_run_id, generation_id) VALUES "
            "('gap_vera_interrupt', '2026-07-15T12:00:00+00:00', "
            "'experience_fact', ?, 'Which Vera Example scale applies?', "
            "'missing_scale', 'medium', 0, ?, 'generation_vera_interrupt')",
            (fact_id, run_id),
        )
        connection.commit()

    def interrupt_removal(_workspace: Path, snapshot_ids):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        capture_service, "remove_assessment_sets", interrupt_removal
    )
    source = VERA_CORPUS / "logs" / "daily-2026-06-20.md"
    result, envelope = invoke_json(
        workspace,
        ["gaps", "answer", "--gap-id", "gap_vera_interrupt", "--file", str(source)],
    )
    # The answer transaction committed before the interrupt; the cancelled
    # envelope keeps class 9 and reports the retained stale set.
    assert result.exit_code == 9
    assert envelope["diagnostic_class"] == "cancelled"
    assert envelope["residual_paths"] == [str(final_set)]
    assert final_set.is_dir()
    with read_database(workspace) as connection:
        answered = connection.execute(
            "SELECT answered FROM gap_questions WHERE id = 'gap_vera_interrupt'"
        ).fetchone()[0]
    assert answered == 1


def test_interrupt_after_committed_stage_replacement_keeps_the_stale_report(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An interrupt can land after the stage transaction durably committed but
    # before the stage returns; the pre-commit pending report must survive
    # into the cancelled envelope because the published set really is stale.
    ids, facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    exported, _envelope = invoke_json(
        workspace, ["export", "assessment", "--snapshot", generated.snapshot_id]
    )
    assert exported.exit_code == 0
    final_set = workspace / "out" / "assessment" / generated.snapshot_id

    fact_id = list(facts)[0]
    monkeypatch.setattr(
        signals_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([signal_response([fact_id])]),
        ),
    )
    real_run = stage5_module.run_complete_stage

    def interrupt_after_commit(*args, **kwargs):
        real_run(*args, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(stage5_module, "run_complete_stage", interrupt_after_commit)
    result, envelope = invoke_json(workspace, ["--yes", "signals", "generate"])
    assert result.exit_code == 9
    assert envelope["diagnostic_class"] == "cancelled"
    assert envelope["residual_paths"] == [str(final_set)]
    assert final_set.is_dir()
    with read_database(workspace) as connection:
        superseded = connection.execute(
            "SELECT superseded_at FROM assessment_snapshots WHERE id = ?",
            (generated.snapshot_id,),
        ).fetchone()[0]
    assert superseded is not None


def test_undecodable_residual_path_is_escaped_in_the_envelope(
    workspace: Path,
) -> None:
    # A non-removable managed entry with a non-UTF-8 name must not crash the
    # envelope: the committed deletion reports the path in backslash-escaped
    # form instead of surrogates that UTF-8 output cannot carry.
    source = VERA_CORPUS / "logs" / "daily-2026-06-09.md"
    captured, captured_envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    assert captured.exit_code == 0
    log_id = next(
        group["ids"][0]
        for group in captured_envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )

    branch = workspace / "out" / "branch"
    branch.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = workspace.parent / "Vera Example undecodable target"
    target.mkdir()
    stray = branch / os.fsdecode(b"vera-\xfe-stray")
    stray.symlink_to(target, target_is_directory=True)

    result, envelope = invoke_json(
        workspace, ["--yes", "logs", "delete", "--log-id", log_id]
    )
    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(branch / "vera-\\xfe-stray")]
    assert stray.is_symlink()


def test_non_export_writer_commits_but_preamble_residual_forces_class_8(
    workspace: Path,
) -> None:
    assessment = workspace / "out" / "assessment"
    assessment.mkdir(mode=0o700, exist_ok=True)
    target = workspace.parent / "Vera Example preamble target"
    target.mkdir()
    candidate = assessment / (
        ".exp2res-candidate-snapshot_vera_preamble-" + "a" * 32
    )
    candidate.symlink_to(target, target_is_directory=True)

    source = VERA_CORPUS / "logs" / "daily-2026-06-09.md"
    result, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "managed_output_incomplete"
    assert envelope["residual_paths"] == [str(candidate)]
    assert candidate.is_symlink()
    assert target.is_dir()
    with read_database(workspace) as connection:
        assert len(list_raw_logs(connection)) == 1


def test_reverification_cleanup_residual_takes_class_8_over_blocked_findings(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    exported, _envelope = invoke_json(
        workspace,
        ["export", "assessment", "--snapshot", generated.snapshot_id],
    )
    assert exported.exit_code == 0
    final_set = workspace / "out" / "assessment" / generated.snapshot_id
    residual = str(final_set)

    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("unsupported") for _ in generated.claims]
            ),
        ),
    )
    def fail_removal(_workspace: Path, snapshot_ids):
        assert tuple(snapshot_ids) == (generated.snapshot_id,)
        return (residual,)

    monkeypatch.setattr(stage7_module, "remove_assessment_sets", fail_removal)
    result, envelope = invoke_json(
        workspace,
        ["--yes", "assess", "verify", "--snapshot", generated.snapshot_id],
    )
    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "managed_output_incomplete"
    assert envelope["residual_paths"] == [residual]
    assert len(envelope["findings"]) == len(generated.claims)
    assert final_set.is_dir()
