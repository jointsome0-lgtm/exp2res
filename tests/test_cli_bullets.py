"""§14.10 `bullets generate`, `bullets verify`, and `bullets export` CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.exports.managed as managed_module
import exp2res.pipeline.stage10 as stage10_module
import exp2res.pipeline.stage11 as stage11_module
import exp2res.services.bullets as bullets_service
from exp2res.cli import bullets_generate_outcome, app
from exp2res.pipeline.stage10 import Stage10Result
from exp2res.storage.repository import (
    list_resume_branches,
    list_resume_bullets_for_branch,
)
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
from test_stage11_bullet_verification import REWRITE, finding, verifier_response


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

    outcome = bullets_generate_outcome(
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


def test_an_interrupt_in_result_assembly_still_reports_the_swap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: composition is inside the durable swap's guarded window."""

    _facts, snapshot_id = arrange(workspace, monkeypatch, one_bullet)
    compose = cli_module.bullets_generate_outcome
    attempts: list[object] = []

    def interrupt_first(generated):
        attempts.append(generated)
        if len(attempts) == 1:
            raise KeyboardInterrupt()
        return compose(generated)

    monkeypatch.setattr(cli_module, "bullets_generate_outcome", interrupt_first)

    result, envelope = generate(workspace, snapshot_id)

    assert result.exit_code == 9, (result.stderr, envelope)
    assert envelope["status"] == "cancelled"
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["resume_branch"]) == 1
    assert len(created["resume_bullet"]) == 1
    assert envelope["generation_ids"] and envelope["run_ids"]
    with read_database(workspace) as connection:
        assert len(list_resume_branches(connection, current_only=True)) == 1


def test_an_invalid_branch_name_fails_in_the_input_class(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10: bad `--branch` input is class 2, never an internal error."""

    _facts, snapshot_id = arrange(workspace, monkeypatch, one_bullet)

    result, envelope = generate(workspace, snapshot_id, branch="   ")

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "branch_name_invalid"


def test_an_unresolvable_selector_precedes_adapter_construction_on_generate(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: a bad `--jd` is class 2, not the config's class 7 (#258)."""

    _facts, snapshot_id = arrange(workspace, monkeypatch, one_bullet)

    def refuse(_workspace):
        raise AssertionError("the adapter was built for an unresolvable selector")

    monkeypatch.setattr(bullets_service, "build_llm_execution", refuse)
    result, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "bullets",
            "generate",
            "--jd",
            "no-such-jd",
            "--snapshot",
            snapshot_id,
            "--branch",
            BRANCH_NAME,
        ],
    )

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["diagnostic_class"] == "selector_not_found"


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

    monkeypatch.setattr(managed_module, "remove_branch_sets", interrupt_cleanup)
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


def verify(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[bytes],
    *,
    branch: str = BRANCH_NAME,
    json_mode: bool = True,
):
    monkeypatch.setattr(
        bullets_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), FakeContractRunner(responses)),
    )
    arguments = ["--workspace", str(workspace), "bullets", "verify", "--branch", branch]
    if json_mode:
        return invoke_json(workspace, ["bullets", "verify", "--branch", branch])
    return runner.invoke(app, arguments), None


def generated_branch(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """One generated branch, returned as its branch and single bullet ID."""

    _facts, snapshot_id = arrange(workspace, monkeypatch, one_bullet)
    result, envelope = generate(workspace, snapshot_id)
    assert result.exit_code == 0, (result.stderr, envelope)
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    return created["resume_branch"][0], created["resume_bullet"][0]


def test_verification_reports_its_findings_through_the_envelope(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10/§14.14: the canonical command path with `result = null`."""

    branch_id, bullet_id = generated_branch(workspace, monkeypatch)

    result, envelope = verify(
        workspace, monkeypatch, [verifier_response([finding(bullet_id)])]
    )

    assert result.exit_code == 0, (result.stderr, envelope)
    assert envelope["command"] == "bullets verify"
    assert envelope["status"] == "ok"
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["verification_finding"]) == 1
    # §13.11 supersedes nothing: the verified bullets stay current.
    assert envelope["affected_ids"]["superseded"] == []
    assert envelope["findings"][0]["target_id"] == bullet_id
    assert envelope["findings"][0]["status"] == "supported"
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    assert bullets[0].verification_status == "supported"


def test_a_bullet_outside_the_export_allowlist_blocks_the_branch(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16.11/§14.14: only `supported` passes, so anything else is class 10."""

    _branch_id, bullet_id = generated_branch(workspace, monkeypatch)

    result, envelope = verify(
        workspace,
        monkeypatch,
        [
            verifier_response(
                [
                    finding(
                        bullet_id,
                        status="partially_supported",
                        phrases=["provenance links"],
                        rewrite=REWRITE,
                    )
                ]
            )
        ],
    )

    assert result.exit_code == 10, (result.stderr, envelope)
    assert envelope["status"] == "blocked"
    assert envelope["diagnostic_class"] == "verifier_gate_blocked"
    assert envelope["findings"][0]["suggested_rewrite"] == REWRITE


def test_human_mode_presents_the_advisory_rewrite(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10: findings are presented in full, `suggested_rewrite` included."""

    _branch_id, bullet_id = generated_branch(workspace, monkeypatch)

    result, _envelope = verify(
        workspace,
        monkeypatch,
        [
            verifier_response(
                [
                    finding(
                        bullet_id,
                        status="unsupported",
                        phrases=["provenance links"],
                        rewrite=REWRITE,
                    )
                ]
            )
        ],
        json_mode=False,
    )

    assert result.exit_code == 10, result.stdout
    assert f"Target bullet: {bullet_id}" in result.stdout
    assert "Status: unsupported" in result.stdout
    assert "- provenance links" in result.stdout
    assert f"Suggested rewrite (advisory): {REWRITE}" in result.stdout
    assert "bullet-pack export is blocked" in result.stderr


def test_an_unknown_branch_is_a_selector_miss(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: selector resolution precedes the semantic pass."""

    generated_branch(workspace, monkeypatch)

    result, envelope = verify(workspace, monkeypatch, [], branch="no-such-branch")

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "selector_not_found"


def test_an_invalid_branch_name_fails_in_the_input_class_on_verify(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10: the same non-blank hygiene both bullet-pack forms apply."""

    generated_branch(workspace, monkeypatch)

    result, envelope = verify(workspace, monkeypatch, [], branch="   ")

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["diagnostic_class"] == "branch_name_invalid"


def test_an_interrupted_invalidation_reports_the_committed_pass(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: a class-9 envelope still carries the committed verdicts."""

    branch_id, bullet_id = generated_branch(workspace, monkeypatch)
    stale_set = plant_branch_set(workspace, branch_id)

    def interrupt_cleanup(*_arguments, **_keywords):
        raise KeyboardInterrupt()

    monkeypatch.setattr(managed_module, "remove_branch_sets", interrupt_cleanup)

    result, envelope = verify(
        workspace, monkeypatch, [verifier_response([finding(bullet_id)])]
    )

    assert result.exit_code == 9, (result.stderr, envelope)
    assert envelope["status"] == "cancelled"
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["verification_finding"]) == 1
    assert str(stale_set) in envelope["residual_paths"]
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    assert bullets[0].verification_status == "supported"


def test_an_interrupt_in_verify_result_assembly_still_reports_the_pass(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the whole-branch finding set composes inside the guard."""

    _branch_id, bullet_id = generated_branch(workspace, monkeypatch)
    compose = cli_module.bullets_verify_outcome
    attempts: list[object] = []

    def interrupt_first(verified):
        attempts.append(verified)
        if len(attempts) == 1:
            raise KeyboardInterrupt()
        return compose(verified)

    monkeypatch.setattr(cli_module, "bullets_verify_outcome", interrupt_first)

    result, envelope = verify(
        workspace, monkeypatch, [verifier_response([finding(bullet_id)])]
    )

    assert result.exit_code == 9, (result.stderr, envelope)
    assert envelope["status"] == "cancelled"
    assert envelope["run_ids"]
    assert [item["target_id"] for item in envelope["findings"]] == [bullet_id]
    # The verdict is durable, so the cancelled envelope reports it rather than
    # leaving the owner to guess whether the pass landed.
    with read_database(workspace) as connection:
        stored = list_resume_bullets_for_branch(connection, _branch_id)
    assert [bullet.verification_status for bullet in stored] == ["supported"]


def test_branch_hygiene_precedes_adapter_construction_on_verify(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: boundary text is settled before any adapter is built."""

    generated_branch(workspace, monkeypatch)

    def refuse(_workspace):
        raise AssertionError("the adapter was built for rejected boundary text")

    monkeypatch.setattr(bullets_service, "build_llm_execution", refuse)
    result, envelope = invoke_json(
        workspace, ["bullets", "verify", "--branch", "   "]
    )

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["diagnostic_class"] == "branch_name_invalid"


def test_an_unknown_branch_precedes_adapter_construction_on_verify(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: resolution joins hygiene ahead of the adapter (#258)."""

    generated_branch(workspace, monkeypatch)

    def refuse(_workspace):
        raise AssertionError("the adapter was built for an unresolvable selector")

    monkeypatch.setattr(bullets_service, "build_llm_execution", refuse)
    result, envelope = invoke_json(
        workspace, ["bullets", "verify", "--branch", "no-such-branch"]
    )

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["diagnostic_class"] == "selector_not_found"


def verified_branch(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """One current branch whose single bullet carries a `supported` verdict."""

    branch_id, bullet_id = generated_branch(workspace, monkeypatch)
    result, envelope = verify(
        workspace, monkeypatch, [verifier_response([finding(bullet_id)])]
    )
    assert result.exit_code == 0, (result.stderr, envelope)
    return branch_id, bullet_id


def export(workspace: Path, *, branch: str = BRANCH_NAME):
    return invoke_json(workspace, ["bullets", "export", "--branch", branch])


def test_export_reports_only_the_closed_manifest_path_projection(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: `bullets export` shares `export assessment`'s result."""

    branch_id, _bullet_id = verified_branch(workspace, monkeypatch)

    result, envelope = export(workspace)

    assert result.exit_code == 0, (result.stderr, envelope)
    assert envelope["command"] == "bullets export"
    assert envelope["status"] == "ok"
    assert set(envelope["result"]) == {"manifest_path", "managed_paths"}
    published = workspace / "out" / "branch" / branch_id
    assert envelope["result"]["manifest_path"] == str(published / "manifest.json")
    assert sorted(Path(path).name for path in envelope["result"]["managed_paths"]) == [
        "bullet_pack.md",
        "evidence_map.json",
        "manifest.json",
        "verification_report.json",
    ]
    # Nothing is derived: export publishes a projection of persisted state.
    assert envelope["affected_ids"]["created"] == []
    assert envelope["affected_ids"]["superseded"] == []


def test_human_mode_lists_the_manifest_before_its_members(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10: the owner reads the identifying manifest first, then its set."""

    branch_id, _bullet_id = verified_branch(workspace, monkeypatch)

    result = runner.invoke(
        app, ["--workspace", str(workspace), "bullets", "export", "--branch", BRANCH_NAME]
    )

    assert result.exit_code == 0, result.stderr
    published = workspace / "out" / "branch" / branch_id
    lines = result.stdout.splitlines()
    assert lines[0] == str(published / "manifest.json")
    assert sorted(lines[1:]) == sorted(
        str(published / name)
        for name in ("bullet_pack.md", "evidence_map.json", "verification_report.json")
    )


def test_an_unverified_branch_is_a_blocked_class_10_export(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16.11: only `supported` may enter the pack, so the gate refuses."""

    branch_id, _bullet_id = generated_branch(workspace, monkeypatch)

    result, envelope = export(workspace)

    assert result.exit_code == 10, (result.stderr, envelope)
    assert envelope["status"] == "blocked"
    assert envelope["diagnostic_class"] == "bullet_pack_export_blocked"
    assert envelope["result"] is None
    # A refused gate publishes nothing, so the parent stays empty.
    assert not (workspace / "out" / "branch" / branch_id).exists()


def test_an_unknown_branch_is_a_selector_miss_on_export(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: selector resolution precedes the managed-writer path."""

    verified_branch(workspace, monkeypatch)

    result, envelope = export(workspace, branch="no-such-branch")

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "selector_not_found"


def test_an_invalid_branch_name_fails_in_the_input_class_on_export(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10: all three bullet-pack forms apply the same non-blank hygiene."""

    verified_branch(workspace, monkeypatch)

    result, envelope = export(workspace, branch="   ")

    assert result.exit_code == 2, (result.stderr, envelope)
    assert envelope["diagnostic_class"] == "branch_name_invalid"


def test_an_unresolved_residual_stops_export_before_publication(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.14/§14.14: a preamble residual is class 8, not a partial export."""

    branch_id, _bullet_id = verified_branch(workspace, monkeypatch)
    parent = workspace / "out" / "branch"
    parent.mkdir(mode=0o700, exist_ok=True)
    outside = workspace.parent / "Vera Example candidate target"
    outside.mkdir()
    residual = parent / f".exp2res-candidate-{branch_id}-{'d' * 32}"
    residual.symlink_to(outside, target_is_directory=True)

    result, envelope = export(workspace)

    assert result.exit_code == 8, (result.stderr, envelope)
    assert envelope["diagnostic_class"] == "managed_output_incomplete"
    assert str(residual) in envelope["residual_paths"]
    assert not (parent / branch_id).exists()
