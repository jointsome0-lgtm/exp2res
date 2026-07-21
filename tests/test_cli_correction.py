"""Offline §14.4/§14.12 correction and recompute lifecycle acceptance."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.services.lifecycle as lifecycle_service
from exp2res.cli import app
from exp2res.domain.models import OccurredAt
from exp2res.llm.runner import AttemptTelemetry, PreparedCall, RawResult
from exp2res.services.capture import new_id
from exp2res.services.export import export_assessment
from exp2res.storage.repository import (
    get_raw_log,
    list_assessment_snapshots,
    list_contradictions,
    list_experience_facts,
    list_gap_questions,
    list_self_signals,
)
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, add_log, budgets, exact_day, fact_response, run_stage3
from test_stage4_detection import detector_response, run_stage4
from test_stage5_signals import SignalIds, run_stage5, signal_response
from test_stage6_assessment import assessment_response, run_stage6
from test_stage7_verification import run_stage7, verifier_response


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]
runner = CliRunner()


def _raw(payload: bytes) -> RawResult:
    return RawResult(
        final_message_bytes=payload,
        exit_code=0,
        duration_seconds=0.01,
        attempts=(AttemptTelemetry(1, 0, 0.01),),
    )


def _lifecycle_response(call: PreparedCall) -> RawResult:
    payload = json.loads(call.serialized_input)
    if call.contract_id == "fact-extractor":
        evidence_ids = [item["id"] for item in payload["evidence_items"]]
        return _raw(fact_response(evidence_ids))
    if call.contract_id == "gap-contradiction-detector":
        return _raw(b'{"gap_questions":[],"contradictions":[],"warnings":[]}')
    assert call.contract_id == "self-signal-extractor"
    fact_ids = [item["id"] for item in payload["facts"]]
    return _raw(signal_response(fact_ids))


def _install_lifecycle_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([_lifecycle_response] * 12),
        ),
    )


def _invoke_json(workspace: Path, arguments: list[str], *, input: str | None = None):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
        input=input,
    )
    return result, json.loads(result.stdout.splitlines()[-1])


def _prepare_full_graph(
    workspace: Path,
    *,
    assessment_scope: str = "global",
    assessment_target: str | None = None,
):
    ids = SignalIds()
    target, target_items = add_log(
        workspace,
        log_id="log_vera_correction_target",
        recorded_at=FIXED_NOW - timedelta(hours=2),
        raw_text="Vera Example originally described a provenance workflow.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_correction_target", "manual_claim"),),
        project="Vera Example Project",
    )
    other, other_items = add_log(
        workspace,
        log_id="log_vera_correction_other",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example independently documented another workflow.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_correction_other", "manual_claim"),),
        project="Vera Example Other",
    )
    extracted = run_stage3(
        workspace,
        FakeContractRunner(
            [fact_response([target_items[0].id]), fact_response([other_items[0].id])]
        ),
        ids,  # type: ignore[arg-type]
    )
    assert len(extracted.created) == 2
    facts = {
        fact.source_log_ids[0]: fact
        for fact in list_experience_facts_for(workspace)
    }
    target_fact = facts[target.id]
    detected = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=target_fact.id,
                    left=("experience_fact", target_fact.id),
                    right=("raw_log", target.id),
                )
            ]
        ),
        ids,  # type: ignore[arg-type]
    )
    signaled = run_stage5(
        workspace,
        FakeContractRunner([signal_response([target_fact.id])]),
        ids,
    )
    fact_ids = (
        [target_fact.id]
        if assessment_scope == "project"
        else [item.id for item in list_experience_facts_for(workspace)]
    )
    signal_ids = [item.id for item in signaled.current_signals]
    assessed = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=fact_ids, signal_ids=signal_ids)]
        ),
        ids,
        scope=assessment_scope,
        target=assessment_target,
    )
    assert assessed.snapshot_id is not None
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response()] * len(assessed.claims)),
        ids,
        assessed.snapshot_id,
    )
    exported = export_assessment(
        workspace, snapshot_id=assessed.snapshot_id, clock=lambda: FIXED_NOW
    )
    return target, other, target_fact, detected, signaled, assessed, Path(exported.manifest_path).parent


def list_experience_facts_for(workspace: Path):
    with read_database(workspace) as connection:
        return list_experience_facts(connection)


def test_correction_rebuilds_through_artifacts_and_preserves_history(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, other, old_fact, detected, signaled, assessed, export_dir = _prepare_full_graph(
        workspace
    )
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)

    result, envelope = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id],
        input="Vera Example corrected and fully restated the workflow.\n\n\n",
    )
    assert result.exit_code == 0, result.stderr
    assert len(envelope["run_ids"]) == 4
    correction_id = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    assert envelope["invalidated_views"][0]["snapshot_id"] == assessed.snapshot_id
    assert not export_dir.exists()

    with read_database(workspace) as connection:
        correction = get_raw_log(connection, correction_id)
        assert correction is not None
        assert correction.entry_type == "correction"
        assert correction.corrects_log_id == target.id
        assert correction.occurred == target.occurred
        assert correction.project == target.project
        evidence = connection.execute(
            "SELECT strength FROM evidence_items WHERE raw_log_id = ?", (correction_id,)
        ).fetchone()
        assert evidence[0] == "manual_claim"
        assert connection.execute(
            "SELECT superseded_at FROM experience_facts WHERE id = ?", (old_fact.id,)
        ).fetchone()[0] is not None
        other_current = [
            item for item in list_experience_facts(connection) if other.id in item.source_log_ids
        ]
        corrected_current = [
            item
            for item in list_experience_facts(connection)
            if correction_id in item.source_log_ids
        ]
        assert len(other_current) == len(corrected_current) == 1
        assert all(
            connection.execute(
                f"SELECT superseded_at FROM {table} WHERE id = ?", (entity_id,)
            ).fetchone()[0]
            is not None
            for table, entity_id in (
                ("gap_questions", detected.created_gap_ids[0]),
                ("contradictions", detected.created_contradiction_ids[0]),
                ("self_signals", signaled.created_signal_ids[0]),
                ("assessment_snapshots", assessed.snapshot_id),
            )
        )
        assert list_assessment_snapshots(connection) == ()
        runs = connection.execute(
            "SELECT id, stage, parent_run_id, status FROM processing_runs "
            f"WHERE id IN ({','.join('?' for _ in envelope['run_ids'])})",
            envelope["run_ids"],
        ).fetchall()
        by_id = {row[0]: row for row in runs}
        assert by_id[envelope["run_ids"][0]][1:] == ("13.13", None, "completed")
        assert [by_id[item][2] for item in envelope["run_ids"][1:]] == [
            envelope["run_ids"][0]
        ] * 3

    current_facts = list_experience_facts_for(workspace)
    current_signals = list_self_signals_for(workspace)
    regenerated = run_stage6(
        workspace,
        FakeContractRunner(
            [
                assessment_response(
                    fact_ids=[item.id for item in current_facts],
                    signal_ids=[item.id for item in current_signals],
                )
            ]
        ),
        new_id,
    )
    assert regenerated.snapshot_id is not None
    with read_database(workspace) as connection:
        snapshots = list_assessment_snapshots(connection)
        assert len(snapshots) == 1 and snapshots[0].scope == "global"


def test_correction_human_output_includes_captured_project_view_command(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _other, _fact, _detected, _signaled, assessed, _export_dir = (
        _prepare_full_graph(
            workspace,
            assessment_scope="project",
            assessment_target="Vera Example Project",
        )
    )
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
        ],
        input="Vera Example corrected and fully restated the workflow.\n\n\n",
    )

    assert result.exit_code == 0, result.output
    assert (
        f"Invalidated {assessed.snapshot_id}: exp2res assess generate "
        "--scope project --project 'Vera Example Project'"
    ) in result.stdout


def list_self_signals_for(workspace: Path):
    with read_database(workspace) as connection:
        return list_self_signals(connection)


def test_correction_copy_and_explicit_temporal_project_replacement(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _items = add_log(
        workspace,
        log_id="log_vera_copy_target",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example original record.",
        occurred=exact_day(12, confidence="low"),
        item_specs=(("evi_vera_copy_target", "manual_claim"),),
        project="Vera Example Original",
    )
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    first_result, first = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id],
        input="Vera Example first complete restatement.\n\n\n",
    )
    assert first_result.exit_code == 0
    first_id = next(
        group["ids"][0]
        for group in first["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    replacement = OccurredAt(
        start=FIXED_NOW + timedelta(days=2),
        end=None,
        precision="exact_datetime",
        confidence="medium",
    )
    second_result, second = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", first_id],
        input=(
            "Vera Example second complete restatement.\n"
            + replacement.model_dump_json()
            + "\nn\nVera Example Replacement\n"
        ),
    )
    assert second_result.exit_code == 0
    second_id = next(
        group["ids"][0]
        for group in second["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    with read_database(workspace) as connection:
        first_log = get_raw_log(connection, first_id)
        second_log = get_raw_log(connection, second_id)
    assert first_log is not None and second_log is not None
    assert first_log.occurred == target.occurred
    assert first_log.project == target.project
    assert second_log.occurred == replacement
    assert second_log.project == "Vera Example Replacement"


@pytest.mark.parametrize(
    ("stored_project", "replacement_input", "expected_project"),
    [
        ("<clear>", "\n", None),
        (None, "<none>\n", "<none>"),
    ],
)
def test_correction_project_choice_has_no_sentinel_collisions(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored_project: str | None,
    replacement_input: str,
    expected_project: str | None,
) -> None:
    target, _items = add_log(
        workspace,
        log_id="log_vera_project_sentinel",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example project-sentinel record.",
        occurred=exact_day(12),
        item_specs=(("evi_vera_project_sentinel", "manual_claim"),),
        project=stored_project,
    )
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)

    result, envelope = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id],
        input=(
            "Vera Example complete project-sentinel restatement.\n"
            "\n"
            "n\n"
            + replacement_input
        ),
    )

    assert result.exit_code == 0, result.output
    correction_id = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    with read_database(workspace) as connection:
        corrected = get_raw_log(connection, correction_id)
    assert corrected is not None and corrected.project == expected_project


def test_unknown_correction_selector_precedes_prompt_and_adapter(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    monkeypatch.setattr(
        typer,
        "prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prompted")),
    )
    result, envelope = _invoke_json(
        workspace,
        ["correction", "add", "--log-id", "log_vera_missing"],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "selector_not_found"


def test_failed_correction_stays_committed_and_selected_recompute_repairs(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, items = add_log(
        workspace,
        log_id="log_vera_failure_target",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example original failure record.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_failure_target", "manual_claim"),),
    )
    old = run_stage3(
        workspace,
        FakeContractRunner([fact_response([items[0].id])]),
        SignalIds(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([b"{}", b"{}"]),
        ),
    )
    failed_result, failed = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id],
        input="Vera Example committed correction before failed recompute.\n\n\n",
    )
    assert failed_result.exit_code == 7
    correction_id = next(
        group["ids"][0]
        for group in failed["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    assert failed["retry"]["command"].endswith(correction_id)
    assert len(failed["run_ids"]) == 2
    with read_database(workspace) as connection:
        assert get_raw_log(connection, correction_id) is not None
        assert list_experience_facts(connection) == ()
        assert connection.execute(
            "SELECT superseded_at FROM experience_facts WHERE id = ?", (old.created[0],)
        ).fetchone()[0] is not None
        orchestration = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (failed["run_ids"][0],),
        ).fetchone()
        assert tuple(orchestration) == ("failed", "response_validation_failed")

    _install_lifecycle_runner(monkeypatch)
    repaired_result, repaired = _invoke_json(
        workspace,
        ["--yes", "recompute", "--log-id", correction_id],
    )
    assert repaired_result.exit_code == 0
    assert len(repaired["run_ids"]) == 4
    with read_database(workspace) as connection:
        assert len(list_experience_facts(connection)) == 1
        assert len(list_self_signals(connection)) == 1


def test_lifecycle_failure_prints_retry_in_human_mode(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _items = add_log(
        workspace,
        log_id="log_vera_human_retry",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example human-retry record.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_human_retry", "manual_claim"),),
    )
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([b"{}", b"{}"]),
        ),
    )

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
        ],
        input="Vera Example committed correction needing retry.\n\n\n",
    )

    assert result.exit_code == 7
    assert "Retry: exp2res recompute --log-id log_" in result.stderr


def test_delete_rebuild_success_failure_zero_survivor_and_bare_recompute(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected, selected_items = add_log(
        workspace,
        log_id="log_vera_delete_selected",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example selected deletion record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_delete_selected", "manual_claim"),),
    )
    survivor, survivor_items = add_log(
        workspace,
        log_id="log_vera_delete_survivor",
        recorded_at=FIXED_NOW + timedelta(hours=1),
        raw_text="Vera Example surviving deletion record.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_delete_survivor", "manual_claim"),),
    )
    run_stage3(
        workspace,
        FakeContractRunner(
            [
                fact_response([selected_items[0].id]),
                fact_response([survivor_items[0].id]),
            ]
        ),
        SignalIds(),  # type: ignore[arg-type]
    )
    _install_lifecycle_runner(monkeypatch)
    deleted_result, deleted = _invoke_json(
        workspace,
        ["--yes", "logs", "delete", "--log-id", selected.id],
    )
    assert deleted_result.exit_code == 0, deleted_result.stderr
    assert len(deleted["run_ids"]) == 4
    with read_database(workspace) as connection:
        assert get_raw_log(connection, selected.id) is None
        assert get_raw_log(connection, survivor.id) is not None
        facts = list_experience_facts(connection)
        assert len(facts) == 1 and survivor.id in facts[0].source_log_ids
        assert len(list_self_signals(connection)) == 1
        assert list_assessment_snapshots(connection) == ()

    _install_lifecycle_runner(monkeypatch)
    bare_result, bare = _invoke_json(workspace, ["--yes", "recompute"])
    assert bare_result.exit_code == 0
    assert len(bare["run_ids"]) == 4
    assert bare["warnings"] == [
        {
            "type": "assessment_view_regeneration_required",
            "message": (
                "No current assessment view exists; run exp2res assess generate "
                "after recompute."
            ),
        }
    ]

    only_survivor = survivor.id
    # §29.2 selection stays eagerly resolved like a direct `extract`, but the
    # zero-survivor rebuild plans no call: a runner whose every invocation
    # fails proves the rebuild stayed offline through real empty stage runs.
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), FakeContractRunner([])),
    )
    zero_result, zero = _invoke_json(
        workspace,
        ["--yes", "logs", "delete", "--log-id", only_survivor],
    )
    assert zero_result.exit_code == 0
    assert len(zero["run_ids"]) == 4
    assert zero["warnings"][0]["type"] == "assessment_view_regeneration_required"


def test_delete_rebuild_failure_never_restores_deleted_record(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected, _ = add_log(
        workspace,
        log_id="log_vera_delete_failure",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example deletion failure selected record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_delete_failure", "manual_claim"),),
    )
    survivor, _ = add_log(
        workspace,
        log_id="log_vera_delete_failure_survivor",
        recorded_at=FIXED_NOW + timedelta(hours=1),
        raw_text="Vera Example deletion failure survivor.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_delete_failure_survivor", "manual_claim"),),
    )
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([b"{}", b"{}"]),
        ),
    )
    result, envelope = _invoke_json(
        workspace,
        ["--yes", "logs", "delete", "--log-id", selected.id],
    )
    assert result.exit_code == 7
    assert envelope["result"]["selected_log"]["id"] == selected.id
    assert envelope["retry"] == {"command": "exp2res recompute"}
    with read_database(workspace) as connection:
        assert get_raw_log(connection, selected.id) is None
        assert get_raw_log(connection, survivor.id) is not None
        assert list_experience_facts(connection) == ()


def test_interrupted_delete_checkpoint_reports_committed_purge(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import exp2res.services.logs as logs_service

    target, _other, _fact, _detected, _signaled, assessed, _export_dir = (
        _prepare_full_graph(workspace)
    )

    def interrupt_checkpoint(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        logs_service, "_delete_checkpoint_residuals", interrupt_checkpoint
    )
    result, envelope = _invoke_json(
        workspace,
        ["--yes", "logs", "delete", "--log-id", target.id],
    )

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["result"]["selected_log"]["id"] == target.id
    assert envelope["retry"] == {"command": "exp2res recompute"}
    assert envelope["generation_ids"]
    assert envelope["invalidated_views"][0]["snapshot_id"] == assessed.snapshot_id
    assert envelope["residual_paths"] == [
        str(workspace / ".exp2res" / "exp2res.sqlite-wal")
    ]
    deleted = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["deleted"]
    }
    assert deleted["raw_log"] == [target.id]
    assert deleted["evidence_item"]
    with read_database(workspace) as connection:
        assert get_raw_log(connection, target.id) is None
        assert list_experience_facts(connection) == ()


def test_recompute_holds_one_writer_authority_across_stages(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §8.1: the §13.13 lifecycle passes its one held writer connection into
    # every stage runner; a stage acquiring its own writer authority would
    # let another business writer interleave between the stage swaps.
    import exp2res.pipeline.stage3 as stage3_module
    import exp2res.pipeline.stage4 as stage4_module
    import exp2res.pipeline.stage5 as stage5_module

    add_log(
        workspace,
        log_id="log_vera_lock_scope",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example lock-scope record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_lock_scope", "manual_claim"),),
    )
    _install_lifecycle_runner(monkeypatch)

    def refuse_stage_lock(*_args, **_kwargs):
        raise AssertionError("a stage acquired its own writer authority")

    for module in (stage3_module, stage4_module, stage5_module):
        monkeypatch.setattr(module, "writer_database", refuse_stage_lock)
    result, envelope = _invoke_json(workspace, ["--yes", "recompute"])
    assert result.exit_code == 0, result.output
    assert len(envelope["run_ids"]) == 4


def test_interrupt_between_stages_reports_committed_progress(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §14.14 rule 6: Ctrl-C after the Stage 3 swap committed still reports
    # the committed runs, created facts, and the §14.12 retry in the
    # cancelled envelope instead of an empty class-9 result.
    add_log(
        workspace,
        log_id="log_vera_interrupt",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example interrupt record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_interrupt", "manual_claim"),),
    )
    _install_lifecycle_runner(monkeypatch)

    def interrupt_stage4(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        lifecycle_service, "run_detection_generation", interrupt_stage4
    )
    result, envelope = _invoke_json(workspace, ["--yes", "recompute"])
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    # Orchestration row plus the committed Stage 3 run stay addressable.
    assert len(envelope["run_ids"]) == 2
    created = {
        group["entity_type"] for group in envelope["affected_ids"]["created"]
    }
    assert "experience_fact" in created
    assert envelope["retry"] == {"command": "exp2res recompute"}
    with read_database(workspace) as connection:
        row = connection.execute(
            "SELECT status, failure_code FROM processing_runs "
            "WHERE stage = '13.13'"
        ).fetchone()
    assert (row["status"], row["failure_code"]) == ("failed", "cancelled")


@pytest.mark.parametrize(
    ("error", "exit_code", "status", "diagnostic_class", "failure_code"),
    [
        (KeyboardInterrupt(), 9, "cancelled", "cancelled", "cancelled"),
        (
            RuntimeError("late view read failed"),
            1,
            "failed",
            "internal_error",
            "internal_error",
        ),
    ],
)
def test_final_view_check_failure_reports_committed_progress(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    exit_code: int,
    status: str,
    diagnostic_class: str,
    failure_code: str,
) -> None:
    add_log(
        workspace,
        log_id="log_vera_final_view_check",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example final-view-check record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_final_view_check", "manual_claim"),),
    )
    _install_lifecycle_runner(monkeypatch)

    def fail_view_check(_connection):
        raise error

    monkeypatch.setattr(
        lifecycle_service, "_has_current_assessment_view", fail_view_check
    )
    result, envelope = _invoke_json(workspace, ["--yes", "recompute"])

    assert result.exit_code == exit_code
    assert envelope["status"] == status
    assert envelope["diagnostic_class"] == diagnostic_class
    assert len(envelope["run_ids"]) == 4
    assert "experience_fact" in {
        group["entity_type"] for group in envelope["affected_ids"]["created"]
    }
    assert envelope["generation_ids"]
    assert envelope["retry"] == {"command": "exp2res recompute"}
    with read_database(workspace) as connection:
        row = connection.execute(
            "SELECT status, failure_code FROM processing_runs "
            "WHERE stage = '13.13'"
        ).fetchone()
    assert (row["status"], row["failure_code"]) == ("failed", failure_code)


def test_interactive_delete_confirmation_names_the_rebuild(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §14.14 rule 3: the one TTY confirmation covers both the destructive
    # purge and the cost-bearing rebuild's provider call.
    selected, _items = add_log(
        workspace,
        log_id="log_vera_consent",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example consent record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_consent", "manual_claim"),),
    )
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    result, envelope = _invoke_json(
        workspace, ["logs", "delete", "--log-id", selected.id], input="n\n"
    )
    assert result.exit_code == 9
    assert envelope["diagnostic_class"] == "cancelled"
    assert "rebuild derived state" in result.output
    assert "model provider" in result.output
    with read_database(workspace) as connection:
        assert get_raw_log(connection, selected.id) is not None


def test_correction_and_delete_hold_one_writer_acquisition(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §8.1: the CLI command acquires the writer authority exactly once and
    # both the committed lifecycle boundary and the rebuild share it.
    import exp2res.services.correction as correction_service
    import exp2res.services.logs as logs_service

    target, _items = add_log(
        workspace,
        log_id="log_vera_one_lock",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example one-lock record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_one_lock", "manual_claim"),),
    )
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a service acquired its own writer authority")

    for module in (correction_service, logs_service, lifecycle_service):
        monkeypatch.setattr(module, "writer_database", refuse)

    acquisitions = []
    real_writer = cli_module.writer_database

    def counting_writer(*args, **kwargs):
        acquisitions.append(kwargs)
        return real_writer(*args, **kwargs)

    monkeypatch.setattr(cli_module, "writer_database", counting_writer)

    corrected_result, _ = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id],
        input="Vera Example one-lock restatement.\n\n\n",
    )
    assert corrected_result.exit_code == 0, corrected_result.output
    assert len(acquisitions) == 1

    acquisitions.clear()
    deleted_result, _ = _invoke_json(
        workspace, ["--yes", "logs", "delete", "--log-id", target.id]
    )
    assert deleted_result.exit_code == 0, deleted_result.output
    assert len(acquisitions) == 1
    assert acquisitions[0]["owner_delete"] is True


def test_interrupted_correction_cleanup_reports_committed_capture(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §14.14 rule 6: Ctrl-C during post-commit managed cleanup still reports
    # the durable correction, its invalidations, and the §14.12 retry.
    import exp2res.services.correction as correction_service

    target, _items = add_log(
        workspace,
        log_id="log_vera_cleanup_interrupt",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example cleanup-interrupt record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_cleanup_interrupt", "manual_claim"),),
    )
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)

    def interrupt_cleanup(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        correction_service, "remove_assessment_sets", interrupt_cleanup
    )
    result, envelope = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id],
        input="Vera Example interrupted restatement.\n\n\n",
    )
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    correction_id = created["raw_log"][0]
    assert created["evidence_item"]
    assert envelope["retry"] == {
        "command": f"exp2res recompute --log-id {correction_id}"
    }
    with read_database(workspace) as connection:
        stored = get_raw_log(connection, correction_id)
    assert stored is not None and stored.corrects_log_id == target.id


def test_interrupted_stage_cleanup_keeps_committed_swap_in_envelope(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §14.14 rule 6: Ctrl-C during Stage 3's post-commit cleanup still
    # reports the committed fact generation through the carried result.
    import exp2res.pipeline.stage3 as stage3_module

    add_log(
        workspace,
        log_id="log_vera_stage_cleanup",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example stage-cleanup record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_stage_cleanup", "manual_claim"),),
    )
    _install_lifecycle_runner(monkeypatch)

    def interrupt_cleanup(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        stage3_module, "remove_assessment_sets", interrupt_cleanup
    )
    result, envelope = _invoke_json(workspace, ["--yes", "recompute"])
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = {
        group["entity_type"] for group in envelope["affected_ids"]["created"]
    }
    assert "experience_fact" in created
    assert envelope["generation_ids"]
    assert len(envelope["run_ids"]) == 2
    assert envelope["retry"] == {"command": "exp2res recompute"}
