"""§14.9 assessment generation and inspection CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.assessment as assessment_service
import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
from exp2res.cli import app

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets, fact_response
from test_stage4_detection import detector_response
from test_stage5_signals import prepare_facts, run_stage5, signal_response, SignalIds
from test_stage6_assessment import assessment_response


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(app, ["--json", "--workspace", str(workspace), *arguments])
    return result, json.loads(result.stdout)


def prepare_graph(workspace: Path):
    ids = SignalIds()
    facts = prepare_facts(workspace, ids)
    signals = run_stage5(
        workspace, FakeContractRunner([signal_response(list(facts))]), ids
    ).current_signals
    return facts, tuple(item.id for item in signals)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--yes", "assess", "generate", "--scope", "unknown"],
        ["--yes", "assess", "generate", "--scope", "project"],
        ["--yes", "assess", "generate", "--scope", "project", "--project", "  "],
        ["--yes", "assess", "generate", "--project", "Vera Example"],
    ],
)
def test_scope_project_validation_matrix_is_class_2(
    workspace: Path, arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    result, envelope = invoke_json(workspace, arguments)
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "invalid_usage"


def test_consent_decline_precedes_adapter_construction(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    missing, envelope = invoke_json(workspace, ["assess", "generate"])
    assert missing.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"

    monkeypatch.setattr("exp2res.cli._noninteractive", lambda _controls: False)
    declined = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), "assess", "generate"],
        input="n\n",
    )
    assert declined.exit_code == 9
    assert json.loads(declined.stdout.splitlines()[-1])["diagnostic_class"] == "cancelled"


def test_generate_list_show_and_current_only_replacement(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    response = assessment_response(fact_ids=list(facts), signal_ids=list(signals))
    fake = FakeContractRunner([response, response])
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )

    first_result, first = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert first_result.exit_code == 0, (first_result.stderr, first)
    assert first["result"] is None
    assert [item["entity_type"] for item in first["affected_ids"]["created"]] == [
        "assessment_snapshot",
        "self_claim",
    ]
    assert first["invalidated_views"] == []
    snapshot_id = first["affected_ids"]["created"][0]["ids"][0]

    listed_result, listed = invoke_json(workspace, ["assess", "list"])
    assert listed_result.exit_code == 0
    assert listed["result"]["snapshots"][0]["id"] == snapshot_id
    assert set(listed["result"]["snapshots"][0]) == {
        "id",
        "scope",
        "scope_target",
        "verification_status",
        "created_at",
    }

    shown_result, shown = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert shown_result.exit_code == 0
    assert shown["result"]["snapshot"]["id"] == snapshot_id
    assert [item["id"] for item in shown["result"]["claims"]] == sorted(
        (item["id"] for item in shown["result"]["claims"]), key=str.encode
    )

    second_result, second = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert second_result.exit_code == 0
    assert [item["entity_type"] for item in second["affected_ids"]["superseded"]] == [
        "self_claim",
        "assessment_snapshot",
    ]
    assert second["invalidated_views"] == []

    missing_result, missing = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert missing_result.exit_code == 2
    assert missing["diagnostic_class"] == "selector_not_found"


def test_project_selector_persists_canonical_prefold_value(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    fake = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    generated, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "assess",
            "generate",
            "--scope",
            "project",
            "--project",
            "  Vera Example Project  ",
        ],
    )
    assert generated.exit_code == 0, (generated.stderr, envelope)
    snapshot_id = envelope["affected_ids"]["created"][0]["ids"][0]
    _shown_result, shown = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert shown["result"]["snapshot"]["scope_target"] == "Vera Example Project"


def test_logs_delete_reports_purged_assessment_groups_and_view(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    fake = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    generated_result, generated = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert generated_result.exit_code == 0
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    deleted_result, deleted = invoke_json(
        workspace, ["--yes", "logs", "delete", "--log-id", "log_vera_signal_0"]
    )
    assert deleted_result.exit_code == 0
    groups = {item["entity_type"]: item["ids"] for item in deleted["affected_ids"]["deleted"]}
    assert snapshot_id in groups["assessment_snapshot"]
    assert groups["self_claim"]
    assert deleted["invalidated_views"][0] == {
        "scope": "global",
        "scope_target": None,
        "snapshot_id": snapshot_id,
        "regeneration_command": "exp2res assess generate",
    }


def test_extract_envelope_reports_invalidated_assessment_view(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    assessment_runner = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), assessment_runner),
    )
    generated_result, generated = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert generated_result.exit_code == 0
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    extractor = FakeContractRunner([fact_response(["evi_vera_signal_0"])])
    monkeypatch.setattr(
        extraction_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), extractor),
    )
    extracted_result, extracted = invoke_json(
        workspace, ["--yes", "extract", "--log-id", "log_vera_signal_0"]
    )
    assert extracted_result.exit_code == 0
    superseded_types = {
        item["entity_type"] for item in extracted["affected_ids"]["superseded"]
    }
    assert {"self_claim", "assessment_snapshot"}.issubset(superseded_types)
    assert extracted["invalidated_views"][0]["snapshot_id"] == snapshot_id


def test_detection_replacement_envelope_reports_invalidated_view(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    assessment_runner = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), assessment_runner),
    )
    generated_result, generated = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert generated_result.exit_code == 0
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    detector = FakeContractRunner(
        [
            detector_response(
                target_id=facts[0],
                left=("experience_fact", facts[0]),
                right=("raw_log", "log_vera_signal_0"),
            )
        ]
    )
    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), detector),
    )
    detected_result, detected = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert detected_result.exit_code == 0
    assert detected["invalidated_views"][0]["snapshot_id"] == snapshot_id
    superseded_types = {
        item["entity_type"] for item in detected["affected_ids"]["superseded"]
    }
    assert {"self_claim", "assessment_snapshot"}.issubset(superseded_types)
