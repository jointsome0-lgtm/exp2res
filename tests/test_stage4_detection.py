"""Offline §13.4 detector contract, retention, replacement, and atomicity."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError
import pytest

from exp2res.errors import LLMCancelledError, LLMInvocationError
from exp2res.llm.detector import DetectorOutput, GapCandidate
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.storage.repository import list_contradictions, list_gap_questions
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import (
    SELECTION,
    add_log,
    budgets,
    exact_day,
    fact_response,
    run_stage3,
)


pytestmark = pytest.mark.contract


class DetectionIds:
    __test__ = False

    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        self.counts[kind] += 1
        return f"{kind}_vera_{self.counts[kind]:04d}"


def detector_response(
    *,
    target_id: str,
    left: tuple[str, str],
    right: tuple[str, str],
    question: str = "What scale did Vera Example validate?",
    reason: str = "missing_scale",
    priority: str = "medium",
    title: str = "Vera Example scope conflict",
    description: str = "The supplied objects describe incompatible scopes.",
    warnings: list[dict[str, str]] | None = None,
) -> bytes:
    return json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "experience_fact",
                    "target_id": target_id,
                    "question": question,
                    "reason": reason,
                    "priority": priority,
                }
            ],
            "contradictions": [
                {
                    "title": title,
                    "description": description,
                    "left_ref_type": left[0],
                    "left_ref_id": left[1],
                    "right_ref_type": right[0],
                    "right_ref_id": right[1],
                }
            ],
            "warnings": [] if warnings is None else warnings,
        },
        separators=(",", ":"),
    ).encode()


def prepare_fact(workspace: Path, ids: DetectionIds):
    log, items = add_log(
        workspace,
        log_id="log_vera_stage4",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example tested a local provenance workflow.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_stage4", "manual_claim"),),
    )
    extracted = run_stage3(
        workspace,
        FakeContractRunner([fact_response([items[0].id])]),
        ids,  # type: ignore[arg-type]
    )
    return extracted.created[0], log.id, items[0].id


def run_stage4(
    workspace: Path,
    fake: FakeContractRunner,
    ids: DetectionIds,
):
    return run_detection_generation(
        workspace,
        selection=SELECTION,
        budgets=budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def current_rows(workspace: Path):
    with read_database(workspace) as connection:
        return list_gap_questions(connection), list_contradictions(connection)


@pytest.mark.lifecycle
def test_empty_workspace_completes_zero_call_retained_run(workspace: Path) -> None:
    ids = DetectionIds()
    fake = FakeContractRunner([])
    result = run_stage4(workspace, fake, ids)
    assert result.retained is True
    assert result.generation_id is None
    assert fake.calls == []
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT stage, status FROM processing_runs WHERE id = ?", (result.run_id,)
        ).fetchone()
        assert tuple(run) == ("13.4", "completed")
        assert connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE run_id = ?", (result.run_id,)
        ).fetchone()[0] == 0


@pytest.mark.lifecycle
def test_happy_path_persists_shared_generation_and_complete_telemetry(
    workspace: Path,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, _item_id = prepare_fact(workspace, ids)
    result = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("experience_fact", fact_id),
                    right=("raw_log", log_id),
                    warnings=[{"type": "vera_note", "message": "Vera Example note."}],
                )
            ]
        ),
        ids,
    )
    assert result.retained is False
    assert result.generation_id == "gen_vera_0002"
    assert len(result.created_gap_ids) == len(result.created_contradiction_ids) == 1
    assert result.warnings[0].type == "vera_note"
    with read_database(workspace) as connection:
        generations = connection.execute(
            """
            SELECT generation_id FROM gap_questions
            UNION ALL SELECT generation_id FROM contradictions
            """
        ).fetchall()
        run = connection.execute(
            "SELECT stage, status, output_ids_json FROM processing_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        call = connection.execute(
            "SELECT status, input_hash, output_hash FROM llm_calls WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()
    assert [row[0] for row in generations] == [result.generation_id] * 2
    assert tuple(run[:2]) == ("13.4", "completed")
    assert set(json.loads(run[2])) == {
        *result.created_gap_ids,
        *result.created_contradiction_ids,
    }
    assert call[0] == "completed" and call[1] and call[2]


@pytest.mark.lifecycle
def test_paraphrase_and_swapped_sides_retain_ids_prose_and_generation(
    workspace: Path,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, _item_id = prepare_fact(workspace, ids)
    first = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("experience_fact", fact_id),
                    right=("raw_log", log_id),
                )
            ]
        ),
        ids,
    )
    before_gaps, before_contradictions = current_rows(workspace)
    retained = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("raw_log", log_id),
                    right=("experience_fact", fact_id),
                    question="Which deployment scope was tested?",
                    title="Different detector wording",
                    description="Different detector-authored prose.",
                )
            ]
        ),
        ids,
    )
    assert retained.retained is True
    assert retained.generation_id is None
    assert retained.created_gap_ids == retained.created_contradiction_ids == ()
    assert current_rows(workspace) == (before_gaps, before_contradictions)
    assert ids.counts["gen"] == 2  # Stage 3 plus only the first Stage 4 swap.
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT status FROM processing_runs WHERE id = ?", (retained.run_id,)
        ).fetchone()[0] == "completed"
        assert connection.execute(
            "SELECT COUNT(DISTINCT generation_id) FROM gap_questions"
        ).fetchone()[0] == 1
    assert first.generation_id is not None

    third = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("experience_fact", fact_id),
                    right=("raw_log", log_id),
                )
            ]
        ),
        ids,
    )
    assert third.retained is True
    with read_database(workspace) as connection:
        for table in ("gap_questions", "contradictions"):
            assert connection.execute(
                f"SELECT COUNT(DISTINCT generation_id) FROM {table} "
                "WHERE superseded_at IS NULL"
            ).fetchone()[0] <= 1


@pytest.mark.lifecycle
def test_changed_structural_key_replaces_both_sets_under_one_generation(
    workspace: Path,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, item_id = prepare_fact(workspace, ids)
    first = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("experience_fact", fact_id),
                    right=("raw_log", log_id),
                )
            ]
        ),
        ids,
    )
    replaced = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("experience_fact", fact_id),
                    right=("evidence_item", item_id),
                    reason="missing_metric",
                    priority="high",
                )
            ]
        ),
        ids,
    )
    assert replaced.retained is False
    assert replaced.generation_id and replaced.generation_id != first.generation_id
    assert replaced.superseded_gap_ids == first.created_gap_ids
    assert replaced.superseded_contradiction_ids == first.created_contradiction_ids
    assert replaced.superseded_generation_ids == (first.generation_id,)
    with read_database(workspace) as connection:
        current_generations = {
            row[0]
            for table in ("gap_questions", "contradictions")
            for row in connection.execute(
                f"SELECT DISTINCT generation_id FROM {table} WHERE superseded_at IS NULL"
            )
        }
    assert current_generations == {replaced.generation_id}


@pytest.mark.lifecycle
def test_answered_gap_forces_key_equal_replacement_and_resets_answer_state(
    workspace: Path,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, _item_id = prepare_fact(workspace, ids)
    response = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    first = run_stage4(workspace, FakeContractRunner([response]), ids)
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        connection.create_function("exp2res_owner_delete", 0, lambda: 0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "UPDATE gap_questions SET answered = 1, answer_log_id = ? WHERE id = ?",
            (log_id, first.created_gap_ids[0]),
        )
    replaced = run_stage4(workspace, FakeContractRunner([response]), ids)
    assert replaced.retained is False
    gaps, _contradictions = current_rows(workspace)
    assert len(gaps) == 1 and gaps[0].answered is False
    assert gaps[0].answer_log_id is None


@pytest.mark.lifecycle
@pytest.mark.parametrize("kind", ["gap", "contradiction"])
def test_duplicate_structural_keys_retry_once_then_fail_atomically(
    workspace: Path, kind: str,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, _item_id = prepare_fact(workspace, ids)
    valid = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    first = run_stage4(workspace, FakeContractRunner([valid]), ids)
    invalid = json.loads(valid)
    if kind == "gap":
        invalid["gap_questions"].append(
            {**invalid["gap_questions"][0], "question": "Paraphrased duplicate?"}
        )
    else:
        original = invalid["contradictions"][0]
        invalid["contradictions"].append(
            {
                **original,
                "title": "Swapped duplicate",
                "left_ref_type": original["right_ref_type"],
                "left_ref_id": original["right_ref_id"],
                "right_ref_type": original["left_ref_type"],
                "right_ref_id": original["left_ref_id"],
            }
        )
    payload = json.dumps(invalid, separators=(",", ":")).encode()
    fake = FakeContractRunner([payload, payload])
    with pytest.raises(LLMInvocationError) as caught:
        run_stage4(workspace, fake, ids)
    assert caught.value.failure_code == "response_validation_failed"
    assert len(fake.calls) == 2 and fake.calls[1].validation_errors is not None
    gaps, contradictions = current_rows(workspace)
    assert tuple(gap.id for gap in gaps) == first.created_gap_ids
    assert tuple(item.id for item in contradictions) == first.created_contradiction_ids


@pytest.mark.lifecycle
@pytest.mark.parametrize(
    "mutation",
    ["out_of_context", "wrong_type", "upper_layer", "self_referential"],
)
def test_invalid_references_retry_then_fail_without_persistence(
    workspace: Path, mutation: str,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, _item_id = prepare_fact(workspace, ids)
    invalid = json.loads(
        detector_response(
            target_id=fact_id,
            left=("experience_fact", fact_id),
            right=("raw_log", log_id),
        )
    )
    if mutation == "out_of_context":
        invalid["gap_questions"][0]["target_id"] = "fact_vera_absent"
    elif mutation == "wrong_type":
        invalid["gap_questions"][0]["target_type"] = "raw_log"
    elif mutation == "upper_layer":
        invalid["gap_questions"][0]["target_type"] = "self_claim"
    else:
        contradiction = invalid["contradictions"][0]
        contradiction["right_ref_type"] = contradiction["left_ref_type"]
        contradiction["right_ref_id"] = contradiction["left_ref_id"]
    payload = json.dumps(invalid, separators=(",", ":")).encode()
    fake = FakeContractRunner([payload, payload])
    with pytest.raises(LLMInvocationError):
        run_stage4(workspace, fake, ids)
    assert len(fake.calls) == 2
    assert current_rows(workspace) == ((), ())


@pytest.mark.lifecycle
def test_displaced_records_and_items_are_absent_and_invalid_targets(
    workspace: Path,
) -> None:
    ids = DetectionIds()
    root, root_items = add_log(
        workspace,
        log_id="log_vera_displaced_stage4",
        recorded_at=FIXED_NOW - timedelta(hours=2),
        raw_text="Vera Example displaced record prose.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_displaced_stage4", "manual_claim"),),
    )
    correction, correction_items = add_log(
        workspace,
        log_id="log_vera_effective_stage4",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example corrected current record prose.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_effective_stage4", "manual_claim"),),
        corrects_log_id=root.id,
    )
    extracted = run_stage3(
        workspace,
        FakeContractRunner([fact_response([correction_items[0].id])]),
        ids,  # type: ignore[arg-type]
    )
    invalid = detector_response(
        target_id=extracted.created[0],
        left=("experience_fact", extracted.created[0]),
        right=("raw_log", root.id),
    )
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError):
        run_stage4(workspace, fake, ids)
    decoded = json.loads(fake.calls[0].serialized_input)
    supplied_logs = {
        entry["raw_log"]["id"] for entry in decoded["evidence_context"]
    }
    supplied_items = {
        entry["evidence_item"]["id"] for entry in decoded["evidence_context"]
    }
    assert supplied_logs == {correction.id}
    assert supplied_items == {correction_items[0].id}
    assert root.id not in supplied_logs
    assert root_items[0].id not in supplied_items


@pytest.mark.lifecycle
def test_commit_failure_and_interrupt_keep_prior_current_generation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, item_id = prepare_fact(workspace, ids)
    first_payload = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    first = run_stage4(workspace, FakeContractRunner([first_payload]), ids)

    from exp2res.pipeline import stage4

    original_insert = stage4.insert_gap_question

    def insert_then_fail(*args, **kwargs) -> None:
        original_insert(*args, **kwargs)
        raise RuntimeError("Vera Example injected commit failure")

    monkeypatch.setattr(stage4, "insert_gap_question", insert_then_fail)
    changed = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("evidence_item", item_id),
        reason="missing_metric",
    )
    with pytest.raises(LLMInvocationError) as caught:
        run_stage4(workspace, FakeContractRunner([changed]), ids)
    assert caught.value.failure_code == "business_commit_failed"
    gaps, contradictions = current_rows(workspace)
    assert tuple(gap.id for gap in gaps) == first.created_gap_ids
    assert tuple(item.id for item in contradictions) == first.created_contradiction_ids

    def insert_then_interrupt(*args, **kwargs) -> None:
        original_insert(*args, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(stage4, "insert_gap_question", insert_then_interrupt)
    with pytest.raises(LLMCancelledError):
        run_stage4(workspace, FakeContractRunner([changed]), ids)
    gaps, contradictions = current_rows(workspace)
    assert tuple(gap.id for gap in gaps) == first.created_gap_ids
    assert tuple(item.id for item in contradictions) == first.created_contradiction_ids


@pytest.mark.lifecycle
def test_stage3_replacement_invalidates_detections_but_empty_stage3_does_not(
    workspace: Path,
) -> None:
    ids = DetectionIds()
    fact_id, log_id, item_id = prepare_fact(workspace, ids)
    detected = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("experience_fact", fact_id),
                    right=("raw_log", log_id),
                )
            ]
        ),
        ids,
    )
    replaced = run_stage3(
        workspace,
        FakeContractRunner([fact_response([item_id])]),
        ids,  # type: ignore[arg-type]
    )
    assert replaced.superseded_gap_ids == detected.created_gap_ids
    assert replaced.superseded_contradiction_ids == detected.created_contradiction_ids
    assert detected.generation_id in replaced.superseded_generation_ids
    assert current_rows(workspace) == ((), ())

    no_fact_workspace = workspace.parent / "no-fact-stage3-workspace"
    no_fact_workspace.mkdir()
    from exp2res.storage.workspace import initialize_workspace

    initialize_workspace(no_fact_workspace, clock=lambda: FIXED_NOW)
    raw_log, items = add_log(
        no_fact_workspace,
        log_id="log_vera_no_fact_change",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example effective evidence with no extracted fact.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_no_fact_change", "manual_claim"),),
    )
    no_fact_ids = DetectionIds()
    detection_payload = json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "raw_log",
                    "target_id": raw_log.id,
                    "question": "What context is missing for Vera Example?",
                    "reason": "missing_context",
                    "priority": "low",
                }
            ],
            "contradictions": [
                {
                    "title": "Vera Example evidence tension",
                    "description": "The supplied record and evidence item differ.",
                    "left_ref_type": "raw_log",
                    "left_ref_id": raw_log.id,
                    "right_ref_type": "evidence_item",
                    "right_ref_id": items[0].id,
                }
            ],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode()
    no_fact_detection = run_stage4(
        no_fact_workspace,
        FakeContractRunner([detection_payload]),
        no_fact_ids,
    )
    empty = run_stage3(
        no_fact_workspace,
        FakeContractRunner([b'{"facts":[],"warnings":[]}']),
        no_fact_ids,  # type: ignore[arg-type]
    )
    assert empty.superseded_gap_ids == empty.superseded_contradiction_ids == ()
    gaps, contradictions = current_rows(no_fact_workspace)
    assert tuple(gap.id for gap in gaps) == no_fact_detection.created_gap_ids
    assert tuple(item.id for item in contradictions) == (
        no_fact_detection.created_contradiction_ids
    )


@pytest.mark.unit
def test_detector_candidates_require_explicit_non_null_model_judgments() -> None:
    valid = {
        "target_type": "raw_log",
        "target_id": "log_vera",
        "question": "What did Vera Example measure?",
        "reason": "missing_metric",
        "priority": "high",
    }
    for field in valid:
        with pytest.raises(ValidationError):
            GapCandidate.model_validate(
                {key: value for key, value in valid.items() if key != field}
            )
    with pytest.raises(ValidationError):
        GapCandidate.model_validate({**valid, "priority": None})
    with pytest.raises(ValidationError):
        DetectorOutput.model_validate(
            {"gap_questions": [], "contradictions": [], "warnings": None}
        )
