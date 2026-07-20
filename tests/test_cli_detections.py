"""§14.7 detection generation, inspection, and gap-answer CLI behavior."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.detection as detection_service
from exp2res.cli import app
from exp2res.storage.repository import (
    get_evidence_for_log,
    get_raw_log,
    list_contradictions,
    list_gap_questions,
)
from exp2res.storage.workspace import read_database

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage4_detection import (
    DetectionIds,
    detector_response,
    prepare_fact,
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
        detection_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )


def seed_detection_inputs(workspace: Path):
    ids = DetectionIds()
    return prepare_fact(workspace, ids)


def test_generate_replaces_then_retains_complete_sets_and_standard_fields(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact_id, log_id, item_id = seed_detection_inputs(workspace)
    first_payload = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    changed_payload = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("evidence_item", item_id),
        reason="missing_metric",
        priority="high",
    )
    human_replacement_payload = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("evidence_item", item_id),
        reason="missing_metric",
        priority="low",
    )
    retained_paraphrase = detector_response(
        target_id=fact_id,
        left=("evidence_item", item_id),
        right=("experience_fact", fact_id),
        reason="missing_metric",
        priority="low",
        question="Which exact metric did Vera Example record?",
        title="Paraphrased Vera Example conflict",
        description="The same supplied objects remain in tension.",
    )
    fake = FakeContractRunner(
        [
            first_payload,
            changed_payload,
            human_replacement_payload,
            human_replacement_payload,
            retained_paraphrase,
        ]
    )
    install_fake_execution(monkeypatch, fake)

    first_result, first = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert first_result.exit_code == 0
    assert [group["entity_type"] for group in first["affected_ids"]["created"]] == [
        "gap_question",
        "contradiction",
    ]
    assert first["affected_ids"]["superseded"] == []
    assert len(first["generation_ids"]) == len(first["run_ids"]) == 1
    assert len(first["result"]["gaps"]) == 1
    assert len(first["result"]["contradictions"]) == 1
    assert set(first["result"]["gaps"][0]) == {
        "id",
        "created_at",
        "superseded_at",
        "target_type",
        "target_id",
        "question",
        "reason",
        "priority",
        "answered",
        "answer_log_id",
    }
    assert set(first["result"]["contradictions"][0]) == {
        "id",
        "created_at",
        "superseded_at",
        "title",
        "description",
        "left_ref_type",
        "left_ref_id",
        "right_ref_type",
        "right_ref_id",
        "metadata",
    }

    replaced_result, replaced = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert replaced_result.exit_code == 0
    assert [
        group["entity_type"] for group in replaced["affected_ids"]["superseded"]
    ] == ["gap_question", "contradiction"]
    assert len(replaced["generation_ids"]) == 2
    assert {
        gap["id"] for gap in replaced["result"]["gaps"]
    } == set(replaced["affected_ids"]["created"][0]["ids"])
    assert {
        item["id"] for item in replaced["result"]["contradictions"]
    } == set(replaced["affected_ids"]["created"][1]["ids"])

    human_replaced = runner.invoke(
        app,
        ["--workspace", str(workspace), "--yes", "detections", "generate"],
    )
    assert human_replaced.exit_code == 0
    assert "Replaced both complete detection sets." in human_replaced.stdout
    assert "Current gaps (1): gap_" in human_replaced.stdout
    assert "Current contradictions (1): contradiction_" in human_replaced.stdout
    assert (
        "Invalidated artifact classes: gap_question, contradiction."
        in human_replaced.stdout
    )
    with read_database(workspace) as connection:
        prior_gap_ids = [gap.id for gap in list_gap_questions(connection)]
        prior_contradiction_ids = [
            item.id for item in list_contradictions(connection)
        ]

    retained_result, retained = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert retained_result.exit_code == 0
    assert [gap["id"] for gap in retained["result"]["gaps"]] == prior_gap_ids
    assert [
        item["id"] for item in retained["result"]["contradictions"]
    ] == prior_contradiction_ids
    assert retained["affected_ids"] == {
        "created": [],
        "superseded": [],
        "deleted": [],
    }
    assert retained["generation_ids"] == []
    assert len(retained["run_ids"]) == 1
    assert len(fake.calls) == 4

    human = runner.invoke(
        app,
        ["--workspace", str(workspace), "--yes", "detections", "generate"],
    )
    assert human.exit_code == 0
    assert human.stdout.strip() == (
        "Retained the current detection generation unchanged."
    )


def test_generate_consent_order_decline_and_zero_input_lazy_runner(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_build(_workspace: Path):
        raise AssertionError("adapter construction ran before consent")

    monkeypatch.setattr(detection_service, "build_llm_execution", refuse_build)
    missing, missing_envelope = invoke_json(
        workspace, ["detections", "generate"]
    )
    assert missing.exit_code == 2
    assert missing_envelope["diagnostic_class"] == "input_required"

    monkeypatch.setattr("exp2res.cli._noninteractive", lambda _controls: False)
    declined = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "detections",
            "generate",
        ],
        input="n\n",
    )
    declined_envelope = json.loads(declined.stdout.splitlines()[-1])
    assert declined.exit_code == 9
    assert declined_envelope["diagnostic_class"] == "cancelled"

    materialized = False

    def build_never_runner():
        nonlocal materialized
        materialized = True
        raise AssertionError("zero-input Stage 4 materialized adapter preflight")

    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            detection_service.LazyPreflightRunner(build_never_runner),
        ),
    )
    empty, empty_envelope = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert empty.exit_code == 0
    assert empty_envelope["result"] == {
        "gaps": [],
        "contradictions": [],
    }
    assert materialized is False
    assert len(empty_envelope["run_ids"]) == 1


def test_gap_answer_is_atomic_self_contained_and_fails_closed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact_id, log_id, _item_id = seed_detection_inputs(workspace)
    payload = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    install_fake_execution(monkeypatch, FakeContractRunner([payload]))
    generated, generated_envelope = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert generated.exit_code == 0
    gap = generated_envelope["result"]["gaps"][0]
    answer_file = tmp_path / "Vera Example gap answer.md"
    answer_file.write_text("Vera Example validated three local nodes.", encoding="utf-8")

    answered, envelope = invoke_json(
        workspace,
        ["gaps", "answer", "--gap-id", gap["id"], "--file", str(answer_file)],
    )
    assert answered.exit_code == 0
    assert [group["entity_type"] for group in envelope["affected_ids"]["created"]] == [
        "evidence_item",
        "raw_log",
    ]
    assert envelope["affected_ids"]["superseded"] == []
    assert envelope["result"] is None
    answer_log_id = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    with read_database(workspace) as connection:
        stored = get_raw_log(connection, answer_log_id)
        evidence = get_evidence_for_log(connection, answer_log_id)
        current_gap = next(
            item for item in list_gap_questions(connection) if item.id == gap["id"]
        )
        raw_count = connection.execute(
            "SELECT COUNT(*) FROM raw_logs WHERE entry_type = 'gap_answer'"
        ).fetchone()[0]
    assert stored is not None
    assert stored.entry_type == "gap_answer"
    assert stored.source_type == "manual_entry"
    assert stored.metadata == {
        "question_text": gap["question"],
        "question_reason": gap["reason"],
    }
    assert len(evidence) == 1 and evidence[0].strength == "manual_claim"
    assert current_gap.answered is True
    assert current_gap.answer_log_id == answer_log_id

    second, second_envelope = invoke_json(
        workspace,
        ["gaps", "answer", "--gap-id", gap["id"], "--file", str(answer_file)],
    )
    assert second.exit_code == 2
    assert second_envelope["diagnostic_class"] == "gap_already_answered"
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM raw_logs WHERE entry_type = 'gap_answer'"
        ).fetchone()[0] == raw_count

    unknown, unknown_envelope = invoke_json(
        workspace,
        [
            "gaps",
            "answer",
            "--gap-id",
            "gap_vera_missing",
            "--file",
            str(tmp_path / "does-not-exist.txt"),
        ],
    )
    assert unknown.exit_code == 2
    assert unknown_envelope["diagnostic_class"] == "selector_not_found"


def test_answered_gap_key_equal_cli_rerun_replaces_without_relinking(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact_id, log_id, _item_id = seed_detection_inputs(workspace)
    response = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    install_fake_execution(monkeypatch, FakeContractRunner([response, response]))
    first_result, first = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert first_result.exit_code == 0
    old_gap_id = first["result"]["gaps"][0]["id"]
    answer_file = tmp_path / "Vera Example answer.txt"
    answer_file.write_text("Vera Example supplied the missing scale.", encoding="utf-8")
    answered, _ = invoke_json(
        workspace,
        ["gaps", "answer", "--gap-id", old_gap_id, "--file", str(answer_file)],
    )
    assert answered.exit_code == 0

    rerun_result, rerun = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert rerun_result.exit_code == 0
    new_gap = rerun["result"]["gaps"][0]
    assert new_gap["id"] != old_gap_id
    assert new_gap["answered"] is False
    assert new_gap["answer_log_id"] is None

    stale, stale_envelope = invoke_json(
        workspace, ["gaps", "answer", "--gap-id", old_gap_id]
    )
    assert stale.exit_code == 2
    assert stale_envelope["diagnostic_class"] == "selector_not_found"
    needs_file, needs_file_envelope = invoke_json(
        workspace, ["gaps", "answer", "--gap-id", new_gap["id"]]
    )
    assert needs_file.exit_code == 2
    assert needs_file_envelope["diagnostic_class"] == "input_required"


def test_detection_inspection_is_read_only_ordered_and_current_only(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact_id, log_id, item_id = seed_detection_inputs(workspace)
    responses = [
        detector_response(
            target_id=fact_id,
            left=("experience_fact", fact_id),
            right=("raw_log", log_id),
        ),
        detector_response(
            target_id=fact_id,
            left=("experience_fact", fact_id),
            right=("evidence_item", item_id),
            reason="missing_metric",
        ),
    ]
    install_fake_execution(monkeypatch, FakeContractRunner(responses))
    first_result, first = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert first_result.exit_code == 0
    old_contradiction_id = first["result"]["contradictions"][0]["id"]
    second_result, second = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert second_result.exit_code == 0

    real_read = detection_service.read_database
    read_calls: list[Path] = []

    @contextmanager
    def tracked_read(selected: Path, **kwargs):
        read_calls.append(selected)
        with real_read(selected, **kwargs) as connection:
            yield connection

    monkeypatch.setattr(detection_service, "read_database", tracked_read)
    gaps_result, gaps = invoke_json(workspace, ["gaps", "list"])
    contradictions_result, contradictions = invoke_json(
        workspace, ["contradictions", "list"]
    )
    current_contradiction_id = second["result"]["contradictions"][0]["id"]
    shown_result, shown = invoke_json(
        workspace,
        ["contradictions", "show", "--contradiction-id", current_contradiction_id],
    )
    superseded_result, superseded_envelope = invoke_json(
        workspace,
        ["contradictions", "show", "--contradiction-id", old_contradiction_id],
    )
    assert gaps_result.exit_code == contradictions_result.exit_code == shown_result.exit_code == 0
    assert [item["id"] for item in gaps["result"]["gaps"]] == sorted(
        item["id"] for item in gaps["result"]["gaps"]
    )
    current_ids = [
        item["id"] for item in contradictions["result"]["contradictions"]
    ]
    assert current_ids == sorted(current_ids)
    assert current_ids == [second["result"]["contradictions"][0]["id"]]
    current_row = shown["result"]["contradictions"][0]
    assert current_row["id"] == current_contradiction_id
    assert current_row["superseded_at"] is None
    # §14.14 rule 7: superseded detections are not browsable beyond runs show.
    assert superseded_result.exit_code == 2
    assert superseded_envelope["diagnostic_class"] == "selector_not_found"
    assert read_calls == [workspace, workspace, workspace, workspace]

    missing, missing_envelope = invoke_json(
        workspace,
        ["contradictions", "show", "--contradiction-id", "contradiction_missing"],
    )
    assert missing.exit_code == 2
    assert missing_envelope["diagnostic_class"] == "selector_not_found"


def test_invalid_detector_response_keeps_prior_generation_and_reports_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact_id, log_id, _item_id = seed_detection_inputs(workspace)
    valid = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    invalid = detector_response(
        target_id="fact_vera_missing",
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    install_fake_execution(
        monkeypatch, FakeContractRunner([valid, invalid, invalid])
    )
    first_result, first = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert first_result.exit_code == 0
    prior_gap_ids = [item["id"] for item in first["result"]["gaps"]]

    failed_result, failed = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert failed_result.exit_code == 7
    assert failed["diagnostic_class"] == "response_validation_failed"
    assert len(failed["run_ids"]) == 1
    with read_database(workspace) as connection:
        gaps = list_gap_questions(connection)
        contradictions = list_contradictions(connection)
    assert [gap.id for gap in gaps] == prior_gap_ids
    assert len(contradictions) == 1
