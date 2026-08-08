"""§14.10 `bullets generate` CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.pipeline.stage10 as stage10_module
import exp2res.services.bullets as bullets_service
from exp2res.cli import _bullets_generate_outcome, app
from exp2res.pipeline.stage10 import Stage10Result
from exp2res.storage.repository import list_resume_branches
from exp2res.storage.workspace import read_database

from fakes import FakeContractRunner
from test_branch_substrate import (
    BRANCH_NAME,
    JOB_DESCRIPTION_ID,
    plant_branch,
    plant_branch_set,
)
from test_stage3_extraction import SELECTION, budgets
from test_stage10_generation import (
    bullet_candidate,
    prepare_anchor,
    writer_response,
)


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(app, ["--json", "--workspace", str(workspace), *arguments])
    return result, json.loads(result.stdout)


def arrange(workspace: Path, monkeypatch: pytest.MonkeyPatch, build) -> tuple[
    tuple[str, ...], str
]:
    """One verified anchor plus the canned response the writer will return."""

    _ids, facts, snapshot_id = prepare_anchor(workspace)
    monkeypatch.setattr(
        bullets_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), FakeContractRunner(build(facts))),
    )
    return facts, snapshot_id


def one_bullet(facts: tuple[str, ...]) -> list[bytes]:
    return [writer_response([bullet_candidate(fact_ids=list(facts))])]


def generate(workspace: Path, snapshot_id: str, *, branch: str = BRANCH_NAME):
    return invoke_json(
        workspace,
        [
            "--yes",
            "bullets",
            "generate",
            "--jd",
            JOB_DESCRIPTION_ID,
            "--snapshot",
            snapshot_id,
            "--branch",
            branch,
        ],
    )


def test_generation_reports_its_pack_through_the_envelope(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10/§14.14: the canonical command path with `result = null`."""

    _facts, snapshot_id = arrange(workspace, monkeypatch, one_bullet)

    result, envelope = generate(workspace, snapshot_id)

    assert result.exit_code == 0, (result.stderr, envelope)
    assert envelope["command"] == "bullets generate"
    assert envelope["status"] == "ok"
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["resume_branch"]) == 1
    assert len(created["resume_bullet"]) == 1
    assert envelope["generation_ids"] and envelope["run_ids"]


def test_a_no_bullet_response_is_a_blocked_completion(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14: class 10 under the stable `no_bullet_generated` class."""

    _facts, snapshot_id = arrange(
        workspace, monkeypatch, lambda _facts: [writer_response([])]
    )

    result, envelope = generate(workspace, snapshot_id)

    assert result.exit_code == 10, (result.stderr, envelope)
    assert envelope["status"] == "blocked"
    assert envelope["diagnostic_class"] == "no_bullet_generated"
    assert envelope["affected_ids"]["created"] == []
    assert envelope["run_ids"]
    with read_database(workspace) as connection:
        assert list_resume_branches(connection, current_only=False) == ()


def test_created_bullet_ids_are_ordered_by_their_stable_identity() -> None:
    """§14.14 rule 5: reported groups order by identity, not writer order."""

    outcome = _bullets_generate_outcome(
        Stage10Result(
            run_id="run_vera_0001",
            branch_name=BRANCH_NAME,
            branch_id="branch_vera_0001",
            bullet_ids=("bullet_vera_zz", "bullet_vera_aa"),
            superseded_branch_ids=(),
            superseded_bullet_ids=(),
            generation_id="gen_vera_0001",
            superseded_generation_ids=(),
            invalidated_branches=(),
            residual_paths=(),
            warnings=(),
            branch=None,
            bullets=(),
        )
    )

    created = {group.entity_type: group.ids for group in outcome.affected_ids.created}
    assert created["resume_bullet"] == ["bullet_vera_aa", "bullet_vera_zz"]


def test_an_invalid_branch_name_fails_in_the_input_class(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10: bad `--branch` input is class 2, never an internal error."""

    _facts, snapshot_id = arrange(workspace, monkeypatch, one_bullet)

    result, envelope = generate(workspace, snapshot_id, branch="   ")

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "branch_name_invalid"


def test_a_replacement_generation_reports_both_generations(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: produced and invalidated generation IDs together."""

    _ids, facts, snapshot_id = prepare_anchor(workspace)
    prior_id, prior_bullet_id = plant_branch(
        workspace,
        snapshot_id=snapshot_id,
        fact_ids=facts,
        branch_id="branch_vera_0009",
        bullet_id="bullet_vera_0009",
        suffix="0009",
    )
    stale_set = plant_branch_set(workspace, prior_id)
    monkeypatch.setattr(
        bullets_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [writer_response([bullet_candidate(fact_ids=list(facts))])]
            ),
        ),
    )

    result, envelope = generate(workspace, snapshot_id)

    assert result.exit_code == 0, (result.stderr, envelope)
    superseded = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["superseded"]
    }
    assert superseded["resume_branch"] == [prior_id]
    assert superseded["resume_bullet"] == [prior_bullet_id]
    assert "gen_vera_branch_0009" in envelope["generation_ids"]
    assert envelope["invalidated_branches"][0]["name"] == BRANCH_NAME
    assert not stale_set.exists()


def test_an_interrupted_cleanup_reports_the_committed_pack(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: a class-9 envelope still carries the durable swap."""

    _ids, facts, snapshot_id = prepare_anchor(workspace)
    prior_id, _prior_bullet_id = plant_branch(
        workspace,
        snapshot_id=snapshot_id,
        fact_ids=facts,
        branch_id="branch_vera_0009",
        bullet_id="bullet_vera_0009",
        suffix="0009",
    )
    stale_set = plant_branch_set(workspace, prior_id)

    def interrupt_cleanup(*_arguments, **_keywords):
        raise KeyboardInterrupt()

    monkeypatch.setattr(stage10_module, "remove_branch_sets", interrupt_cleanup)
    monkeypatch.setattr(
        bullets_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [writer_response([bullet_candidate(fact_ids=list(facts))])]
            ),
        ),
    )

    result, envelope = generate(workspace, snapshot_id)

    assert result.exit_code == 9, (result.stderr, envelope)
    assert envelope["status"] == "cancelled"
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["resume_branch"]) == 1
    assert str(stale_set) in envelope["residual_paths"]
