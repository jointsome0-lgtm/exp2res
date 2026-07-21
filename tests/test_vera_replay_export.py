"""Vera E6 replays the implemented CLI pipeline through assessment export."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import exp2res.services.assessment as assessment_service
import exp2res.services.capture as capture_service
import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
import exp2res.services.signals as signals_service
from exp2res.pipeline.stage3 import run_fact_extraction
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.pipeline.stage5 import run_signal_generation
from exp2res.pipeline.stage6 import run_assessment_generation
from exp2res.pipeline.stage7 import run_assessment_verification
from exp2res.services.capture import capture_daily

from conftest import FIXED_NOW, REPOSITORY_ROOT, VERA_CORPUS
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets, fact_response
from test_stage5_signals import SignalIds, signal_response
from test_stage6_assessment import assessment_response
from test_stage7_verification import verifier_response
from test_vera_replay_assess import invoke_json


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]
GOLDENS = REPOSITORY_ROOT / "tests" / "goldens" / "assessment"
MEMBERS = ("report.md", "self_claims.json", "evidence_map.json")


def _fixed_stage(real_stage, ids):
    def deterministic(selected_workspace: Path, **kwargs):
        kwargs.pop("id_factory", None)
        kwargs.pop("clock", None)
        return real_stage(
            selected_workspace,
            **kwargs,
            id_factory=ids,
            clock=lambda: FIXED_NOW,
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )

    return deterministic


def _run_cli_stage(
    monkeypatch: pytest.MonkeyPatch,
    service,
    real_stage,
    ids,
    response_bytes: list[bytes],
    workspace: Path,
    command: list[str],
):
    monkeypatch.setattr(service, "new_id", ids)
    monkeypatch.setattr(
        service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(response_bytes),
        ),
    )
    stage_name = {
        extraction_service: "run_fact_extraction",
        detection_service: "run_detection_generation",
        signals_service: "run_signal_generation",
        assessment_service: (
            "run_assessment_verification"
            if real_stage is run_assessment_verification
            else "run_assessment_generation"
        ),
    }[service]
    monkeypatch.setattr(service, stage_name, _fixed_stage(real_stage, ids))
    result, envelope = invoke_json(workspace, ["--yes", *command])
    assert result.exit_code == 0, (result.stderr, envelope)
    return envelope


def test_vera_e6_cli_export_goldens_and_artifact_lifecycle(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = SignalIds()
    monkeypatch.setattr(capture_service, "new_id", ids)
    captured = capture_daily(
        workspace,
        raw_text=(
            "Vera Example built and validated a provenance-aware local workflow."
        ),
        project="Exp2Res",
        clock=lambda: FIXED_NOW,
        id_factory=ids,
    )

    extracted = _run_cli_stage(
        monkeypatch,
        extraction_service,
        run_fact_extraction,
        ids,
        [fact_response([captured.evidence_items[0].id])],
        workspace,
        ["extract"],
    )
    fact_id = extracted["affected_ids"]["created"][0]["ids"][0]

    detector_payload = json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "experience_fact",
                    "target_id": fact_id,
                    "question": "Which exact scale did Vera Example validate?",
                    "reason": "missing_scale",
                    "priority": "medium",
                }
            ],
            "contradictions": [],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    detected = _run_cli_stage(
        monkeypatch,
        detection_service,
        run_detection_generation,
        ids,
        [detector_payload],
        workspace,
        ["detections", "generate"],
    )
    gap_id = detected["result"]["gaps"][0]["id"]

    generated_signals = _run_cli_stage(
        monkeypatch,
        signals_service,
        run_signal_generation,
        ids,
        [signal_response([fact_id])],
        workspace,
        ["signals", "generate"],
    )
    signal_id = generated_signals["affected_ids"]["created"][0]["ids"][0]

    generated = _run_cli_stage(
        monkeypatch,
        assessment_service,
        run_assessment_generation,
        ids,
        [assessment_response(fact_ids=[fact_id], signal_ids=[signal_id])],
        workspace,
        ["assess", "generate"],
    )
    snapshot_id = next(
        group["ids"][0]
        for group in generated["affected_ids"]["created"]
        if group["entity_type"] == "assessment_snapshot"
    )
    claim_count = next(
        len(group["ids"])
        for group in generated["affected_ids"]["created"]
        if group["entity_type"] == "self_claim"
    )

    _run_cli_stage(
        monkeypatch,
        assessment_service,
        run_assessment_verification,
        ids,
        [verifier_response()] * claim_count,
        workspace,
        ["assess", "verify", "--snapshot", snapshot_id],
    )

    first_result, first = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert first_result.exit_code == 0
    assert first["result"].keys() == {"manifest_path", "managed_paths"}
    final_set = workspace / "out" / "assessment" / snapshot_id
    first_bytes = {name: (final_set / name).read_bytes() for name in MEMBERS}
    first_manifest = json.loads((final_set / "manifest.json").read_text())

    second_result, second = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert second_result.exit_code == 0
    assert second["result"] == first["result"]
    second_bytes = {name: (final_set / name).read_bytes() for name in MEMBERS}
    second_manifest = json.loads((final_set / "manifest.json").read_text())
    assert second_bytes == first_bytes
    assert second_manifest["members"] == first_manifest["members"]
    assert {
        row["name"]: row["sha256"] for row in second_manifest["members"]
    } == {
        name: hashlib.sha256(member).hexdigest()
        for name, member in second_bytes.items()
    }
    for name, member in first_bytes.items():
        assert member == (GOLDENS / name).read_bytes()

    answer_source = VERA_CORPUS / "logs" / "daily-2026-06-20.md"
    answered_result, answered = invoke_json(
        workspace,
        ["gaps", "answer", "--gap-id", gap_id, "--file", str(answer_source)],
    )
    assert answered_result.exit_code == 0, (answered_result.stderr, answered)
    assert not final_set.exists()
    reexported_result, _reexported = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert reexported_result.exit_code == 0
    assert b"**Answered since synthesis:** yes" in (final_set / "report.md").read_bytes()
    companion = json.loads((final_set / "self_claims.json").read_text())
    assert companion["unknowns"] == [
        {
            "answered": True,
            "id": gap_id,
            "priority": "medium",
            "question": "Which exact scale did Vera Example validate?",
            "reason": "missing_scale",
            "target_id": fact_id,
            "target_type": "experience_fact",
        }
    ]

    replaced = _run_cli_stage(
        monkeypatch,
        signals_service,
        run_signal_generation,
        ids,
        [
            signal_response(
                [fact_id],
                statement="Vera Example changed the provenance-aware direction.",
            )
        ],
        workspace,
        ["signals", "generate"],
    )
    assert replaced["invalidated_views"][0]["snapshot_id"] == snapshot_id
    assert not final_set.exists()
    stale_result, stale = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert stale_result.exit_code == 2
    assert stale["diagnostic_class"] == "snapshot_not_current"
