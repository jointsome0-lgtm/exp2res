"""Vera E4 replays E1→E3 state into global assessment synthesis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.assessment as assessment_service
import exp2res.services.detection as detection_service
from exp2res.cli import app
from exp2res.storage.repository import list_assessment_snapshots
from exp2res.storage.workspace import read_database

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage4_detection import detector_response
from assessment_helpers import VeraIds, prepare_facts
from test_stage6_assessment import assessment_response


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(app, ["--json", "--workspace", str(workspace), *arguments])
    return result, json.loads(result.stdout)


def test_vera_e4_cli_assessment_is_navigable_then_regeneration_invalidates(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = VeraIds()
    fact_ids = prepare_facts(workspace, ids, count=2)
    assessment_runner = FakeContractRunner(
        [assessment_response(fact_ids=list(fact_ids), confidence="low")]
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
    assert generated["status"] == "ok"
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    listed_result, listed = invoke_json(workspace, ["assess", "list"])
    shown_result, shown = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert listed_result.exit_code == shown_result.exit_code == 0
    assert [item["id"] for item in listed["result"]["snapshots"]] == [snapshot_id]
    assert shown["result"]["snapshot"]["id"] == snapshot_id
    assert {item["claim"] for item in shown["result"]["claims"]} == {
        "You currently show a provenance-aware working pattern.",
        "Current evidence suggests provenance-aware pattern 1.",
    }
    with read_database(workspace) as connection:
        assert len(list_assessment_snapshots(connection)) == 1

    # §13.4: with the signal layer gone, the detection set is the upstream
    # replacement that reaches the synthesis — a new generation supersedes
    # the current snapshot and its claims, and names the stale view.
    replacement_runner = FakeContractRunner(
        [
            detector_response(
                target_id=fact_ids[0],
                left=("experience_fact", fact_ids[0]),
                right=("experience_fact", fact_ids[1]),
            )
        ]
    )
    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), replacement_runner),
    )
    replaced_result, replaced = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert replaced_result.exit_code == 0
    assert replaced["invalidated_views"] == [
        {
            "scope": "global",
            "scope_target": None,
            "snapshot_id": snapshot_id,
            "regeneration_command": "exp2res assess generate",
        }
    ]
    assert {item["entity_type"] for item in replaced["affected_ids"]["superseded"]} >= {
        "self_claim",
        "assessment_snapshot",
    }
