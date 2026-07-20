"""Vera E3 replays capture, extraction, detection, and signal generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
import exp2res.services.signals as signals_service
from exp2res.cli import app
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.pipeline.stage5 import run_signal_generation
from exp2res.storage.repository import list_experience_facts, list_self_signals
from exp2res.storage.workspace import read_database

from conftest import VERA_CORPUS, configure_timezone
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_vera_replay_detect import detection_response
from test_vera_replay_extract import ReplayIds, parse_clock, replay_manual_capture


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app, ["--json", "--workspace", str(workspace), *arguments]
    )
    return result, json.loads(result.stdout)


def test_vera_e3_cli_replay_persists_one_current_ordered_signal_generation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_timezone(workspace, "Europe/Berlin")
    ids = ReplayIds()
    captured = replay_manual_capture(workspace, ids)
    contract = json.loads(
        (VERA_CORPUS / "replay.json").read_text(encoding="utf-8")
    )
    by_step = {step["step"]: step for step in contract["derived_steps"]}

    extractor = FakeContractRunner(
        [
            path.read_bytes()
            for path in sorted((VERA_CORPUS / "llm").glob("extract-call-*.json"))
        ]
    )
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
            clock=lambda: parse_clock(by_step["E1"]["clock"]),
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
    assert extracted_envelope["status"] == by_step["E1"]["expect"]["status"]

    expected_logs = {
        captured[Path(relative).stem].id
        for relative in by_step["E2"]["expect"]["contradiction_between"]
    }
    with read_database(workspace) as connection:
        facts = list_experience_facts(connection)
    facts_by_log = {
        source_log_id: fact
        for fact in facts
        for source_log_id in fact.source_log_ids
        if source_log_id in expected_logs
    }
    left_fact, right_fact = (
        facts_by_log[log_id] for log_id in sorted(expected_logs)
    )

    detector = FakeContractRunner(
        [
            detection_response(
                gap_fact_id=left_fact.id,
                left_fact_id=left_fact.id,
                right_fact_id=right_fact.id,
            )
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
            clock=lambda: parse_clock(by_step["E2"]["clock"]),
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )

    monkeypatch.setattr(
        detection_service, "run_detection_generation", deterministic_stage4
    )
    detected, detected_envelope = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert detected.exit_code == 0
    assert detected_envelope["status"] == by_step["E2"]["expect"]["status"]

    response = json.dumps(
        {
            "signals": [
                {
                    "signal_type": "direction_signal",
                    "statement": (
                        "Vera Example repeatedly chooses provenance-aware "
                        "local systems."
                    ),
                    "supporting_fact_ids": [right_fact.id, left_fact.id],
                    "counter_fact_ids": [],
                    "confidence": "low",
                },
                {
                    "signal_type": "constraint_signal",
                    "statement": (
                        "Vera Example records environment constraints explicitly."
                    ),
                    "supporting_fact_ids": [left_fact.id],
                    "counter_fact_ids": [right_fact.id],
                    "confidence": "low",
                },
            ],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signal_runner = FakeContractRunner([response])
    monkeypatch.setattr(
        signals_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), signal_runner),
    )
    monkeypatch.setattr(signals_service, "new_id", ids)
    real_stage5 = run_signal_generation

    def deterministic_stage5(selected_workspace: Path, **kwargs):
        kwargs.pop("clock", None)
        return real_stage5(
            selected_workspace,
            **kwargs,
            clock=lambda: parse_clock(by_step["E3"]["clock"]),
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )

    monkeypatch.setattr(
        signals_service, "run_signal_generation", deterministic_stage5
    )
    generated, generated_envelope = invoke_json(
        workspace, ["--yes", "signals", "generate"]
    )
    assert generated.exit_code == 0
    assert generated_envelope["status"] == by_step["E3"]["expect"]["status"]
    assert generated_envelope["result"] is None
    assert generated_envelope["affected_ids"]["created"][0]["entity_type"] == (
        "self_signal"
    )

    listed_result, listed = invoke_json(workspace, ["signals", "list"])
    assert listed_result.exit_code == 0
    listed_signals = listed["result"]["signals"]
    assert [item["id"] for item in listed_signals] == sorted(
        (item["id"] for item in listed_signals), key=str.encode
    )
    assert all("Vera Example" in item["statement"] for item in listed_signals)
    with read_database(workspace) as connection:
        assert len(list_self_signals(connection)) == 2
        generations = connection.execute(
            "SELECT DISTINCT generation_id FROM self_signals "
            "WHERE superseded_at IS NULL"
        ).fetchall()
    assert len(generations) == 1
