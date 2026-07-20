"""§14.8 self-signal generation and inspection CLI behavior."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.signals as signals_service
import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
from exp2res.cli import app
from exp2res.storage.repository import list_self_signals
from exp2res.storage.workspace import read_database

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage3_extraction import fact_response
from test_stage4_detection import detector_response
from test_stage5_signals import (
    SignalIds,
    prepare_facts,
    run_stage5,
    signal_response,
)


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app, ["--json", "--workspace", str(workspace), *arguments]
    )
    return result, json.loads(result.stdout)


def install_fake_execution(
    monkeypatch: pytest.MonkeyPatch, fake: FakeContractRunner
) -> None:
    monkeypatch.setattr(
        signals_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )


def two_signal_response(fact_id: str, *, suffix: str) -> bytes:
    first = json.loads(signal_response([fact_id]).decode("utf-8"))["signals"][0]
    second = dict(first)
    second["signal_type"] = "interest_signal"
    second["statement"] = f"Vera Example interest signal {suffix}."
    first["statement"] = f"Vera Example execution signal {suffix}."
    return json.dumps(
        {"signals": [second, first], "warnings": []}, separators=(",", ":")
    ).encode("utf-8")


def test_generate_envelope_replaces_and_keeps_unlisted_result_null(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact_id = prepare_facts(workspace, SignalIds())[0]
    payload = signal_response(
        [fact_id],
        warnings=[{"type": "vera_note", "message": "Vera Example note."}],
    )
    fake = FakeContractRunner([payload, payload, payload])
    install_fake_execution(monkeypatch, fake)

    first_result, first = invoke_json(workspace, ["--yes", "signals", "generate"])
    assert first_result.exit_code == 0
    assert first["result"] is None
    assert [group["entity_type"] for group in first["affected_ids"]["created"]] == [
        "self_signal"
    ]
    assert first["affected_ids"]["superseded"] == []
    assert len(first["generation_ids"]) == len(first["run_ids"]) == 1
    assert first["warnings"][0]["type"] == "vera_note"
    assert first["invalidated_views"] == first["invalidated_branches"] == []

    second_result, second = invoke_json(
        workspace, ["--yes", "signals", "generate"]
    )
    assert second_result.exit_code == 0
    assert [group["entity_type"] for group in second["affected_ids"]["superseded"]] == [
        "self_signal"
    ]
    assert len(second["generation_ids"]) == 2
    assert second["result"] is None

    human = runner.invoke(
        app,
        ["--workspace", str(workspace), "--yes", "signals", "generate"],
    )
    assert human.exit_code == 0
    assert "Created 1 signals; superseded 1." in human.stdout
    assert "Invalidated artifact classes: none" in human.stdout
    assert "No assessment view exists yet to regenerate." in human.stdout


def test_generate_consent_decline_and_noninteractive_failure_precede_adapter(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_build(_workspace: Path):
        raise AssertionError("adapter construction ran before cost consent")

    monkeypatch.setattr(signals_service, "build_llm_execution", refuse_build)
    missing, envelope = invoke_json(workspace, ["signals", "generate"])
    assert missing.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"

    monkeypatch.setattr("exp2res.cli._noninteractive", lambda _controls: False)
    declined = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "signals",
            "generate",
        ],
        input="n\n",
    )
    declined_envelope = json.loads(declined.stdout.splitlines()[-1])
    assert declined.exit_code == 9
    assert declined_envelope["diagnostic_class"] == "cancelled"


def test_signals_list_is_read_only_current_only_and_id_byte_ordered(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact_id = prepare_facts(workspace, SignalIds())[0]
    fake = FakeContractRunner(
        [
            two_signal_response(fact_id, suffix="old"),
            two_signal_response(fact_id, suffix="current"),
        ]
    )
    install_fake_execution(monkeypatch, fake)
    first, _first_envelope = invoke_json(
        workspace, ["--yes", "signals", "generate"]
    )
    second, second_envelope = invoke_json(
        workspace, ["--yes", "signals", "generate"]
    )
    assert first.exit_code == second.exit_code == 0

    real_read = signals_service.read_database
    read_calls: list[Path] = []

    @contextmanager
    def tracked_read(selected: Path, **kwargs):
        read_calls.append(selected)
        with real_read(selected, **kwargs) as connection:
            yield connection

    monkeypatch.setattr(signals_service, "read_database", tracked_read)
    listed_result, listed = invoke_json(workspace, ["signals", "list"])
    assert listed_result.exit_code == 0
    signals = listed["result"]["signals"]
    ids = [signal["id"] for signal in signals]
    assert ids == sorted(ids, key=str.encode)
    assert len(ids) == 2
    assert all(signal["superseded_at"] is None for signal in signals)
    assert set(ids) == set(second_envelope["affected_ids"]["created"][0]["ids"])
    assert read_calls == [workspace]
    with read_database(workspace) as connection:
        assert len(list_self_signals(connection, current_only=False)) == 4

    human = runner.invoke(
        app, ["--workspace", str(workspace), "signals", "list"]
    )
    assert human.exit_code == 0
    assert all(len(line.split("\t")) == 3 for line in human.stdout.splitlines())


def test_extract_and_detection_replacement_append_signal_supersession_group(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = SignalIds()
    fact_id = prepare_facts(workspace, ids)[0]
    first_signal = run_stage5(
        workspace, FakeContractRunner([signal_response([fact_id])]), ids
    )
    monkeypatch.setattr(
        extraction_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([fact_response(["evi_vera_signal_0"])]),
        ),
    )
    extracted, extraction_envelope = invoke_json(
        workspace, ["--yes", "extract", "--log-id", "log_vera_signal_0"]
    )
    assert extracted.exit_code == 0
    assert [
        group["entity_type"]
        for group in extraction_envelope["affected_ids"]["superseded"]
    ] == ["experience_fact", "self_signal"]
    assert extraction_envelope["affected_ids"]["superseded"][1]["ids"] == list(
        first_signal.created_signal_ids
    )

    current_fact_id = extraction_envelope["affected_ids"]["created"][0]["ids"][0]
    second_signal = run_stage5(
        workspace,
        FakeContractRunner([signal_response([current_fact_id])]),
        ids,
    )
    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [
                    detector_response(
                        target_id=current_fact_id,
                        left=("experience_fact", current_fact_id),
                        right=("raw_log", "log_vera_signal_0"),
                    )
                ]
            ),
        ),
    )
    detected, detection_envelope = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert detected.exit_code == 0
    assert [
        group["entity_type"]
        for group in detection_envelope["affected_ids"]["superseded"]
    ] == ["self_signal"]
    assert detection_envelope["affected_ids"]["superseded"][0]["ids"] == list(
        second_signal.created_signal_ids
    )
