"""Vera E2 replays capture and Stage 3 into the Stage 4 CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
from exp2res.cli import app
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.storage.repository import list_contradictions, list_experience_facts
from exp2res.storage.workspace import read_database

from conftest import VERA_CORPUS, configure_timezone
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_vera_replay_extract import (
    ReplayIds,
    parse_clock,
    replay_manual_capture,
)


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app, ["--json", "--workspace", str(workspace), *arguments]
    )
    return result, json.loads(result.stdout)


def detection_response(
    *,
    gap_fact_id: str,
    left_fact_id: str,
    right_fact_id: str,
    paraphrased: bool = False,
) -> bytes:
    return json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "experience_fact",
                    "target_id": gap_fact_id,
                    "question": (
                        "Which exact scale did Vera Example validate?"
                        if not paraphrased
                        else "What validation scale did Vera Example record?"
                    ),
                    "reason": "missing_scale",
                    "priority": "medium",
                }
            ],
            "contradictions": [
                {
                    "title": (
                        "Vera Example environment tension"
                        if not paraphrased
                        else "Vera Example environment mismatch"
                    ),
                    "description": (
                        "The two supplied facts describe incompatible environments."
                        if not paraphrased
                        else "The supplied facts disagree about the environment."
                    ),
                    "left_ref_type": "experience_fact",
                    "left_ref_id": (
                        left_fact_id if not paraphrased else right_fact_id
                    ),
                    "right_ref_type": "experience_fact",
                    "right_ref_id": (
                        right_fact_id if not paraphrased else left_fact_id
                    ),
                }
            ],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode()


def test_vera_e2_cli_detect_replay_is_navigable_and_retains_paraphrases(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_timezone(workspace, "Europe/Berlin")
    ids = ReplayIds()
    captured = replay_manual_capture(workspace, ids)
    contract = json.loads(
        (VERA_CORPUS / "replay.json").read_text(encoding="utf-8")
    )
    e1 = next(step for step in contract["derived_steps"] if step["step"] == "E1")
    e2 = next(step for step in contract["derived_steps"] if step["step"] == "E2")

    extractor_responses = [
        path.read_bytes()
        for path in sorted((VERA_CORPUS / "llm").glob("extract-call-*.json"))
    ]
    extractor = FakeContractRunner(extractor_responses)
    monkeypatch.setattr(
        extraction_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), extractor),
    )
    real_stage3 = extraction_service.run_fact_extraction

    def deterministic_stage3(selected_workspace: Path, **kwargs):
        kwargs.pop("id_factory", None)
        kwargs.pop("clock", None)
        return real_stage3(
            selected_workspace,
            **kwargs,
            id_factory=ids,
            clock=lambda: parse_clock(e1["clock"]),
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )

    monkeypatch.setattr(
        extraction_service, "run_fact_extraction", deterministic_stage3
    )
    extracted, extracted_envelope = invoke_json(
        workspace, ["--yes", "extract"]
    )
    assert extracted.exit_code == 0
    assert extracted_envelope["status"] == e1["expect"]["status"] == "ok"

    expected_paths = e2["expect"]["contradiction_between"]
    expected_logs = {
        captured[Path(relative).stem].id for relative in expected_paths
    }
    with read_database(workspace) as connection:
        facts = list_experience_facts(connection)
    facts_by_log = {
        source_log_id: fact
        for fact in facts
        for source_log_id in fact.source_log_ids
        if source_log_id in expected_logs
    }
    assert set(facts_by_log) == expected_logs
    left_log_id, right_log_id = sorted(expected_logs)
    left_fact = facts_by_log[left_log_id]
    right_fact = facts_by_log[right_log_id]

    detector = FakeContractRunner(
        [
            detection_response(
                gap_fact_id=left_fact.id,
                left_fact_id=left_fact.id,
                right_fact_id=right_fact.id,
            ),
            detection_response(
                gap_fact_id=left_fact.id,
                left_fact_id=left_fact.id,
                right_fact_id=right_fact.id,
                paraphrased=True,
            ),
        ]
    )
    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), detector),
    )
    monkeypatch.setattr(detection_service, "new_id", ids)
    real_stage4 = run_detection_generation

    def deterministic_stage4(selected_workspace: Path, **kwargs):
        kwargs.pop("clock", None)
        return real_stage4(
            selected_workspace,
            **kwargs,
            clock=lambda: parse_clock(e2["clock"]),
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )

    monkeypatch.setattr(
        detection_service, "run_detection_generation", deterministic_stage4
    )

    first_result, first = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert first_result.exit_code == 0
    assert first["status"] == e2["expect"]["status"] == "ok"
    assert [group["entity_type"] for group in first["affected_ids"]["created"]] == [
        "gap_question",
        "contradiction",
    ]
    assert all(len(group["ids"]) == 1 for group in first["affected_ids"]["created"])

    with read_database(workspace) as connection:
        contradiction = list_contradictions(connection)[0]
        current_facts = {fact.id: fact for fact in list_experience_facts(connection)}
    traced_logs = {
        raw_log_id
        for ref_id in (contradiction.left_ref_id, contradiction.right_ref_id)
        for raw_log_id in current_facts[ref_id].source_log_ids
    }
    assert traced_logs == expected_logs

    prior_ids = {
        "gap": first["result"]["gaps"][0]["id"],
        "contradiction": first["result"]["contradictions"][0]["id"],
    }
    retained_result, retained = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert retained_result.exit_code == 0
    assert retained["affected_ids"]["created"] == []
    assert retained["affected_ids"]["superseded"] == []
    assert retained["generation_ids"] == []
    assert retained["result"]["gaps"][0]["id"] == prior_ids["gap"]
    assert (
        retained["result"]["contradictions"][0]["id"]
        == prior_ids["contradiction"]
    )
