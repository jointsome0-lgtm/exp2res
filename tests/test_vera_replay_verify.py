"""Vera E5 replays the E4 assessment through the Stage 7 CLI gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import exp2res.services.assessment as assessment_service
from exp2res.storage.repository import (
    list_self_claims_for_snapshot,
    list_verification_findings,
)
from exp2res.storage.workspace import read_database

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage5_signals import SignalIds, prepare_facts, run_stage5, signal_response
from test_stage6_assessment import assessment_response
from test_stage7_verification import verifier_response
from test_vera_replay_assess import invoke_json


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_vera_e5_cli_reverification_preserves_complete_finding_history(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = SignalIds()
    fact_ids = prepare_facts(workspace, ids, count=2)
    signal_result = run_stage5(
        workspace,
        FakeContractRunner([signal_response(list(fact_ids), confidence="low")]),
        ids,
    )
    signal_ids = [item.id for item in signal_result.current_signals]
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [
                    assessment_response(
                        fact_ids=list(fact_ids),
                        signal_ids=signal_ids,
                        confidence="low",
                    )
                ]
            ),
        ),
    )
    generated_result, generated = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert generated_result.exit_code == 0
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    with read_database(workspace) as connection:
        claim_count = len(list_self_claims_for_snapshot(connection, snapshot_id))
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([verifier_response()] * claim_count),
        ),
    )
    first_result, first = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert first_result.exit_code == 0
    assert first["status"] == "ok"

    listed_result, listed = invoke_json(workspace, ["assess", "list"])
    shown_result, shown = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert listed_result.exit_code == shown_result.exit_code == 0
    assert listed["result"]["snapshots"][0]["verification_status"] == "supported"
    assert all(
        item["verification_status"] == "supported"
        for item in shown["result"]["claims"]
    )

    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("unsupported")]
                + [verifier_response()] * (claim_count - 1)
            ),
        ),
    )
    second_result, second = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert second_result.exit_code == 10
    assert second["status"] == "blocked"

    with read_database(workspace) as connection:
        history = list_verification_findings(connection)
    assert len(history) == claim_count * 2
    assert {item.produced_by_run_id for item in history} == {
        first["run_ids"][0],
        second["run_ids"][0],
    }
    stored_by_id = {item.id: item for item in history}
    for finding in [*first["findings"], *second["findings"]]:
        stored = stored_by_id[finding["id"]].model_dump(mode="json")
        assert _canonical_bytes(finding) == _canonical_bytes(stored)
