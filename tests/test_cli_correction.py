"""Offline §14.4/§14.12 correction and recompute lifecycle acceptance."""

from __future__ import annotations

from contextlib import contextmanager

from datetime import timedelta
import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.exports.managed as managed_module
import exp2res.services.lifecycle as lifecycle_service
from exp2res.cli import app
from exp2res.domain.models import OccurredAt
from exp2res.domain.temporal import governing_contains
from exp2res.llm.runner import AttemptTelemetry, PreparedCall, RawResult
from exp2res.services.capture import new_id
from exp2res.services.correction import capture_correction
from exp2res.services.export import export_assessment
from exp2res.storage.repository import (
    get_raw_log,
    list_assessment_snapshots,
    list_contradictions,
    list_experience_facts,
    list_gap_questions,
)
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import (
    SELECTION,
    TestIds,
    add_log,
    budgets,
    exact_day,
    fact_response,
    run_stage3,
)
from assessment_helpers import VeraIds
from test_stage4_detection import detector_response, run_stage4
from test_stage6_assessment import assessment_response, run_stage6
from test_stage7_verification import run_stage7, verifier_response
from test_branch_substrate import plant_branch, plant_job_description


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
    assert call.contract_id == "gap-contradiction-detector"
    return _raw(b'{"gap_questions":[],"contradictions":[],"warnings":[]}')


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


def _prepare_full_graph(workspace: Path):
    ids = VeraIds()
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
    fact_ids = [item.id for item in list_experience_facts_for(workspace)]
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=fact_ids)]),
        ids,
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
    return target, other, target_fact, detected, assessed, Path(exported.manifest_path).parent


def list_experience_facts_for(workspace: Path):
    with read_database(workspace) as connection:
        return list_experience_facts(connection)


def test_correction_rebuilds_through_artifacts_and_preserves_history(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, other, old_fact, detected, assessed, export_dir = _prepare_full_graph(
        workspace
    )
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)

    result, envelope = _invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--artifact",
            "https://example.invalid/Vera-Example-correction-artifact",
        ],
        input="Vera Example corrected and fully restated the workflow.\n\n\n",
    )
    assert result.exit_code == 0, result.stderr
    assert len(envelope["run_ids"]) == 3
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
            "SELECT strength, uri FROM evidence_items WHERE raw_log_id = ? "
            "ORDER BY rowid",
            (correction_id,),
        ).fetchall()
        assert [tuple(row) for row in evidence] == [
            ("manual_claim", None),
            (
                "artifact_reference",
                "https://example.invalid/Vera-Example-correction-artifact",
            ),
        ]
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
        ] * 2

    current_facts = list_experience_facts_for(workspace)
    regenerated = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[item.id for item in current_facts])]
        ),
        new_id,
    )
    assert regenerated.snapshot_id is not None
    with read_database(workspace) as connection:
        snapshots = list_assessment_snapshots(connection)
        assert len(snapshots) == 1 and snapshots[0].scope == "global"


def test_correction_human_output_includes_the_captured_view_command(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _other, _fact, _detected, assessed, _export_dir = _prepare_full_graph(
        workspace
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
        f"Invalidated assessment view {assessed.snapshot_id} "
        "(global); regenerate with: exp2res assess generate"
    ) in result.stdout


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
            + '\n"Vera Example Replacement"\n'
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


def test_open_period_correction_reattests_then_closes_without_rewriting_history(
    workspace: Path,
) -> None:
    """§21.53/§24.57: correction is the only open-period state transition."""

    start = FIXED_NOW - timedelta(days=105)
    open_period = OccurredAt(
        start=start,
        end=None,
        precision="date_range",
        confidence="medium",
    )
    root, root_items = add_log(
        workspace,
        log_id="log_vera_open_correction_root",
        recorded_at=FIXED_NOW - timedelta(days=45),
        raw_text="Vera Example began the synthetic work and recorded no end.",
        occurred=open_period,
        item_specs=(("evi_vera_open_correction_root", "manual_claim"),),
        project=None,
    )
    ids = TestIds()
    run_stage3(
        workspace,
        FakeContractRunner([fact_response([root_items[0].id])]),
        ids,
    )

    restated = capture_correction(
        workspace,
        log_id=root.id,
        raw_text="Vera Example restates the synthetic work with no recorded end.",
        occurred=open_period,
        project=None,
        clock=lambda: FIXED_NOW,
    )
    run_stage3(
        workspace,
        FakeContractRunner(
            [fact_response([restated.evidence_items[0].id])]
        ),
        ids,
    )
    with read_database(workspace) as connection:
        restated_fact = list_experience_facts(connection)[0]
    assert restated_fact.occurred == open_period
    bounded_after_root_attestation = OccurredAt(
        start=FIXED_NOW - timedelta(days=20),
        end=FIXED_NOW - timedelta(days=19),
        precision="date_range",
        confidence="medium",
    )
    assert not governing_contains(
        root.occurred, root.recorded_at, bounded_after_root_attestation
    )
    assert governing_contains(
        restated.raw_log.occurred,
        restated.raw_log.recorded_at,
        bounded_after_root_attestation,
    )

    closed_period = open_period.model_copy(
        update={"end": FIXED_NOW - timedelta(days=1)}
    )
    closed = capture_correction(
        workspace,
        log_id=restated.raw_log.id,
        raw_text="Vera Example restates the synthetic work with its recorded end.",
        occurred=closed_period,
        project=None,
        clock=lambda: FIXED_NOW + timedelta(days=1),
    )
    run_stage3(
        workspace,
        FakeContractRunner([fact_response([closed.evidence_items[0].id])]),
        ids,
    )
    with read_database(workspace) as connection:
        current_fact = list_experience_facts(connection)[0]
        retained = connection.execute(
            """
            SELECT id, occurred_start, occurred_end
            FROM raw_logs
            WHERE id IN (?, ?, ?)
            ORDER BY recorded_at, id
            """,
            (root.id, restated.raw_log.id, closed.raw_log.id),
        ).fetchall()
    assert current_fact.occurred == closed_period
    assert [tuple(row) for row in retained] == [
        (root.id, start.isoformat(), None),
        (restated.raw_log.id, start.isoformat(), None),
        (closed.raw_log.id, start.isoformat(), closed_period.end.isoformat()),
    ]


@pytest.mark.parametrize(
    ("stored_project", "replacement_input", "expected_project"),
    [
        ("<clear>", "null\n", None),
        (None, '"<none>"\n', "<none>"),
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
        VeraIds(),  # type: ignore[arg-type]
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
    assert len(repaired["run_ids"]) == 3
    with read_database(workspace) as connection:
        assert len(list_experience_facts(connection)) == 1


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


@pytest.mark.parametrize("mode", ["human", "json"])
def test_failed_lifecycle_still_reports_the_invalidated_view(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """§14.14 rule 5: the staleness report survives the failure path (#164).

    The capture transaction already superseded the published snapshot when
    the Stage 5 rebuild fails, so an owner who learns nothing here discovers
    it only by trying to export.
    """

    target, _other, _fact, _detected, assessed, export_dir = (
        _prepare_full_graph(workspace)
    )
    assert export_dir.is_dir()
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

    arguments = ["--yes", "correction", "add", "--log-id", target.id]
    text = "Vera Example correction whose rebuild fails.\n\n\n"
    reported = {
        "scope": "global",
        "snapshot_id": assessed.snapshot_id,
        "regeneration_command": "exp2res assess generate",
    }

    if mode == "json":
        # The machine consumer keeps reading the same closed envelope field;
        # human mode gains the rendering, the envelope gains nothing.
        result, envelope = _invoke_json(workspace, arguments, input=text)
        assert result.exit_code == 7
        assert envelope["invalidated_views"] == [reported]
        assert "Invalidated assessment view" not in result.stdout
        return

    result = runner.invoke(
        app, ["--workspace", str(workspace), *arguments], input=text
    )
    assert result.exit_code == 7, result.output
    assert (
        f"Invalidated assessment view {reported['snapshot_id']} (global); "
        f"regenerate with: {reported['regeneration_command']}"
    ) in result.stdout


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
        VeraIds(),  # type: ignore[arg-type]
    )
    _install_lifecycle_runner(monkeypatch)
    deleted_result, deleted = _invoke_json(
        workspace,
        ["--yes", "logs", "delete", "--log-id", selected.id],
    )
    assert deleted_result.exit_code == 0, deleted_result.stderr
    assert len(deleted["run_ids"]) == 3
    with read_database(workspace) as connection:
        assert get_raw_log(connection, selected.id) is None
        assert get_raw_log(connection, survivor.id) is not None
        facts = list_experience_facts(connection)
        assert len(facts) == 1 and survivor.id in facts[0].source_log_ids
        assert list_assessment_snapshots(connection) == ()

    _install_lifecycle_runner(monkeypatch)
    bare_result, bare = _invoke_json(workspace, ["--yes", "recompute"])
    assert bare_result.exit_code == 0
    assert len(bare["run_ids"]) == 3
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
    assert len(zero["run_ids"]) == 3
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

    target, _other, _fact, _detected, assessed, _export_dir = (
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
    assert len(envelope["run_ids"]) == 1
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
        orchestration = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
    assert tuple(orchestration) == ("failed", "cancelled")


def test_recompute_holds_one_writer_authority_across_stages(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §8.1: the §13.13 lifecycle passes its one held writer connection into
    # every stage runner; a stage acquiring its own writer authority would
    # let another business writer interleave between the stage swaps.
    import exp2res.pipeline.stage3 as stage3_module
    import exp2res.pipeline.stage4 as stage4_module

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

    for module in (stage3_module, stage4_module):
        monkeypatch.setattr(module, "writer_database", refuse_stage_lock)
    result, envelope = _invoke_json(workspace, ["--yes", "recompute"])
    assert result.exit_code == 0, result.output
    assert len(envelope["run_ids"]) == 3


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


def test_interrupt_after_orchestration_creation_reports_committed_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_log(
        workspace,
        log_id="log_vera_orchestration_interrupt",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example orchestration-interrupt record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_orchestration_interrupt", "manual_claim"),),
    )
    _install_lifecycle_runner(monkeypatch)
    real_transaction = lifecycle_service.transaction
    transaction_count = 0

    @contextmanager
    def interrupt_after_first_commit(connection):
        nonlocal transaction_count
        with real_transaction(connection) as held:
            yield held
        transaction_count += 1
        if transaction_count == 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr(lifecycle_service, "transaction", interrupt_after_first_commit)
    result, envelope = _invoke_json(workspace, ["--yes", "recompute"])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert len(envelope["run_ids"]) == 1
    assert envelope["retry"] == {"command": "exp2res recompute"}
    with read_database(workspace) as connection:
        orchestration = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
    assert tuple(orchestration) == ("failed", "cancelled")


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
    assert len(envelope["run_ids"]) == 3
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


def test_interactive_correction_runs_without_confirmation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _items = add_log(
        workspace,
        log_id="log_vera_correction_consent",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example correction-consent record.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_correction_consent", "manual_claim"),),
    )
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(
        cli_module.typer,
        "confirm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("routine model work prompted")
        ),
    )

    result, envelope = _invoke_json(
        workspace,
        ["correction", "add", "--log-id", target.id],
        input="Vera Example promptless correction restatement.\n\n\n",
    )

    assert result.exit_code == 0, result.output
    correction_id = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    with read_database(workspace) as connection:
        correction = get_raw_log(connection, correction_id)
    assert correction is not None
    assert correction.corrects_log_id == target.id


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
        managed_module, "remove_assessment_sets", interrupt_cleanup
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
    assert len(envelope["run_ids"]) == 1
    with read_database(workspace) as connection:
        stored = get_raw_log(connection, correction_id)
        orchestration = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
    assert stored is not None and stored.corrects_log_id == target.id
    assert tuple(orchestration) == ("failed", "cancelled")


def test_interrupted_stage_cleanup_keeps_committed_swap_in_envelope(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §14.14 rule 6: Ctrl-C during Stage 3's post-commit cleanup still
    # reports the committed fact generation through the carried result.
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
        managed_module, "remove_assessment_sets", interrupt_cleanup
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


def test_correction_reports_the_superseded_branch_and_bullet(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 4: only the capture composition can report these IDs.

    Correction capture supersedes every current branch and bullet before the
    Stage 3-4 rebuild, and the rebuild never sees them again — so a mapper
    that skipped them would drop the IDs from every correction envelope.
    """

    target, _other, _fact, _detected, assessed, _export_dir = _prepare_full_graph(
        workspace
    )
    plant_job_description(workspace)
    branch_id, bullet_id = plant_branch(
        workspace,
        snapshot_id=assessed.snapshot_id,
        fact_ids=tuple(
            sorted(item.id for item in list_experience_facts_for(workspace))
        ),
    )
    _install_lifecycle_runner(monkeypatch)
    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)

    result, envelope = _invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id],
        input="Vera Example corrected and fully restated the workflow.\n\n\n",
    )

    assert result.exit_code == 0, result.stderr
    superseded = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["superseded"]
    }
    assert superseded["resume_branch"] == [branch_id]
    assert superseded["resume_bullet"] == [bullet_id]
    assert [item["name"] for item in envelope["invalidated_branches"]] == [
        "agent-engineer"
    ]
