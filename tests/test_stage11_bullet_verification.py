"""Offline §13.11 bullet-verification tests.

Stage 11 owns the only transition out of a bullet's initial `unverified`
status, so these hold the one whole-branch invocation it may make, the exact
provenance §13.3 rule 10 lets that invocation carry, the complete-or-nothing
finding set it commits, and the published set a changed verdict invalidates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from exp2res.errors import (
    BranchNameInvalidError,
    LLMInvocationError,
    SelectorNotFoundError,
)
from exp2res.pipeline.stage11 import run_bullet_verification
from exp2res.storage.repository import (
    list_resume_bullets_for_branch,
    list_verification_findings,
)
from exp2res.storage.workspace import collect_preamble_residuals, read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_branch_substrate import (
    BRANCH_NAME,
    JOB_DESCRIPTION_ID,
    REQUIREMENT_ID,
    plant_branch_set,
)
from test_stage3_extraction import SELECTION, add_log, budgets, month
from test_stage10_generation import (
    BULLET_TEXT,
    SECOND_TEXT,
    bullet_candidate,
    prepare_anchor,
    run_stage10,
    writer_response,
)


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


REASON = "Every material assertion is carried by the linked record."
REWRITE = "Designed provenance links across an evidence-grounded workflow."


def finding(
    bullet_id: str,
    *,
    status: str = "supported",
    phrases: list[str] | None = None,
    rewrite: str | None = None,
    reason: str = REASON,
) -> dict[str, object]:
    return {
        "bullet_id": bullet_id,
        "status": status,
        "unsupported_phrases": phrases or [],
        "suggested_rewrite": rewrite,
        "reason": reason,
    }


def verifier_response(findings: list[dict[str, object]]) -> bytes:
    return json.dumps({"findings": findings}, separators=(",", ":")).encode("utf-8")


def prepare_generated_branch(
    workspace: Path, *, texts: list[str] | None = None
) -> tuple[object, tuple[str, ...], str, str, tuple[str, ...]]:
    """One current branch whose bullets stand exactly where Stage 10 left them."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    candidates = [
        bullet_candidate(text, fact_ids=list(facts))
        for text in (texts or [BULLET_TEXT])
    ]
    generated = run_stage10(
        workspace,
        FakeContractRunner([writer_response(candidates)]),
        ids,
        snapshot_id=snapshot_id,
    )
    assert generated.branch_id is not None
    return ids, facts, snapshot_id, generated.branch_id, generated.bullet_ids


def run_stage11(
    workspace: Path,
    fake: FakeContractRunner,
    ids,
    *,
    branch_name: str = BRANCH_NAME,
):
    return run_bullet_verification(
        workspace,
        branch_name=branch_name,
        selection=SELECTION,
        budgets=budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def test_one_invocation_carries_the_whole_current_bullet_set(
    workspace: Path,
) -> None:
    """§13.11: one §15.7 call for the branch, in ascending ID-byte order."""

    ids, facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(
        workspace, texts=[BULLET_TEXT, SECOND_TEXT]
    )
    ordered = sorted(bullet_ids, key=lambda value: value.encode("utf-8"))
    fake = FakeContractRunner(
        [verifier_response([finding(bullet_id) for bullet_id in ordered])]
    )

    verified = run_stage11(workspace, fake, ids)

    assert len(fake.calls) == 1
    payload = json.loads(fake.calls[0].serialized_input)
    assert [item["id"] for item in payload["resume_bullets"]] == ordered
    assert payload["job_description"]["id"] == JOB_DESCRIPTION_ID
    # §15.7: the vacancy reaches the verifier as its parsed view only.
    assert "raw_text" not in payload["job_description"]
    assert [
        item["id"] for item in payload["job_description"]["parsed"]["requirements"]
    ] == [REQUIREMENT_ID]

    assert verified.branch_id == branch_id
    assert verified.export_blocked is False
    assert [status for _id, status in verified.bullet_statuses] == [
        "supported",
        "supported",
    ]
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    assert all(bullet.verification_status == "supported" for bullet in bullets)
    assert all(bullet.verifier_reason == REASON for bullet in bullets)
    assert sorted(facts) == sorted(bullets[0].source_fact_ids)
    # §11.14/§12.13: every committed finding resolves to this one Stage 11 run.
    assert {item.produced_by_run_id for item in verified.findings} == {
        verified.run_id
    }
    assert {item.target_type for item in verified.findings} == {"resume_bullet"}


def test_the_provenance_arrays_are_exactly_what_the_bullets_name(
    workspace: Path,
) -> None:
    """§13.3 rule 10: cited facts, retained logs, cited claims, no evidence item."""

    ids, facts, _snapshot, _branch_id, bullet_ids = prepare_generated_branch(workspace)
    fake = FakeContractRunner(
        [verifier_response([finding(bullet_id) for bullet_id in bullet_ids])]
    )

    run_stage11(workspace, fake, ids)

    payload = json.loads(fake.calls[0].serialized_input)
    assert [item["id"] for item in payload["source_facts"]] == sorted(
        facts, key=lambda value: value.encode("utf-8")
    )
    named_logs = {
        log_id
        for bullet in payload["resume_bullets"]
        for log_id in bullet["source_log_ids"]
    }
    assert {item["id"] for item in payload["source_logs"]} == named_logs
    assert payload["source_self_claims"] == []
    # §15.7 serializes no `EvidenceItem` object at all.
    assert "evidence_items" not in payload
    assert "source_evidence" not in payload


def test_a_displaced_record_keeps_its_identity_and_loses_its_object(
    workspace: Path,
) -> None:
    """§13.3 rule 10: displacement withholds prose, never provenance."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(workspace)
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    displaced = bullets[0].source_log_ids[0]
    # A stored correction displaces the owning record directly, leaving the
    # branch current: §13.13 rule 4's supersession belongs to the §14.4
    # capture flow, and this test is about the projection, not that flow.
    add_log(
        workspace,
        log_id="log_vera_stage11_correction",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example self-contained corrected workflow statement.",
        occurred=month(),
        item_specs=(("evi_vera_stage11_correction", "manual_claim"),),
        corrects_log_id=displaced,
    )

    fake = FakeContractRunner(
        [verifier_response([finding(bullet_id) for bullet_id in bullet_ids])]
    )
    run_stage11(workspace, fake, ids)

    payload = json.loads(fake.calls[0].serialized_input)
    assert displaced in payload["resume_bullets"][0]["source_log_ids"]
    assert displaced not in {item["id"] for item in payload["source_logs"]}


def test_an_incomplete_finding_set_commits_nothing(workspace: Path) -> None:
    """§13.11: no bullet update and no finding row survive an invalid response."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(
        workspace, texts=[BULLET_TEXT, SECOND_TEXT]
    )
    fake = FakeContractRunner(
        [verifier_response([finding(bullet_ids[0])])] * 3
    )

    with pytest.raises(LLMInvocationError):
        run_stage11(workspace, fake, ids)

    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        assert list_verification_findings(connection) == ()
    assert all(bullet.verification_status == "unverified" for bullet in bullets)


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param("duplicate", id="duplicate"),
        pytest.param("unknown", id="unknown"),
    ],
)
def test_a_duplicate_or_unknown_bullet_id_is_invalid_output(
    workspace: Path, spoil: str
) -> None:
    """§13.11: one verdict per supplied bullet, addressed by its own ID."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(
        workspace, texts=[BULLET_TEXT, SECOND_TEXT]
    )
    first = bullet_ids[0]
    findings = (
        [finding(first), finding(first)]
        if spoil == "duplicate"
        else [finding(first), finding("bullet_vera_not_in_context")]
    )
    fake = FakeContractRunner([verifier_response(findings)] * 3)

    with pytest.raises(LLMInvocationError):
        run_stage11(workspace, fake, ids)

    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        assert list_verification_findings(connection) == ()
    assert all(bullet.verification_status == "unverified" for bullet in bullets)


def test_the_finding_history_is_immutable_and_the_rewrite_is_advisory(
    workspace: Path,
) -> None:
    """§11.14/§13.11: a re-verification appends and never applies a rewrite."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(workspace)
    bullet_id = bullet_ids[0]
    first = FakeContractRunner(
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
        ]
    )
    blocked = run_stage11(workspace, first, ids)
    assert blocked.export_blocked is True

    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    assert bullets[0].verification_status == "partially_supported"
    assert bullets[0].unsupported_phrases == ["provenance links"]
    # The advisory rewrite is presented, never applied: the stored text is
    # still Stage 10's, and §11.8 gives it no rewrite column to land in.
    assert bullets[0].text == BULLET_TEXT

    second = FakeContractRunner([verifier_response([finding(bullet_id)])])
    passed = run_stage11(workspace, second, ids)
    assert passed.export_blocked is False

    with read_database(workspace) as connection:
        history = list_verification_findings(
            connection, target_type="resume_bullet", target_id=bullet_id
        )
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    assert len(history) == 2
    assert {item.status for item in history} == {"partially_supported", "supported"}
    assert {item.suggested_rewrite for item in history} == {REWRITE, None}
    assert bullets[0].verification_status == "supported"
    assert bullets[0].unsupported_phrases == []


def test_a_changed_verdict_removes_the_branch_published_set(
    workspace: Path,
) -> None:
    """§13.11: a verifier result may not leave an older manifest current."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(workspace)
    published = plant_branch_set(workspace, branch_id)
    fake = FakeContractRunner(
        [verifier_response([finding(bullet_ids[0])])]
    )

    residuals: list[str] = []
    with collect_preamble_residuals(residuals):
        verified = run_stage11(workspace, fake, ids)

    assert verified.residual_paths == ()
    assert not published.exists()


def test_an_unchanged_re_verification_keeps_the_published_set(
    workspace: Path,
) -> None:
    """§13.11: only a *changed* verifier field invalidates the branch's set."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(workspace)
    bullet_id = bullet_ids[0]
    run_stage11(workspace, FakeContractRunner([verifier_response([finding(bullet_id)])]), ids)
    published = plant_branch_set(workspace, branch_id)

    verified = run_stage11(
        workspace, FakeContractRunner([verifier_response([finding(bullet_id)])]), ids
    )

    assert verified.export_blocked is False
    assert published.exists()


def test_the_selector_resolves_a_current_branch_by_its_folded_name(
    workspace: Path,
) -> None:
    """§14.10: `--branch` selects by NFC case fold, never by stored spelling."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(workspace)
    fake = FakeContractRunner([verifier_response([finding(bullet_ids[0])])])

    verified = run_stage11(workspace, fake, ids, branch_name="AGENT-Engineer")

    assert verified.branch_id == branch_id
    assert verified.branch_name == BRANCH_NAME


def test_an_unknown_branch_is_a_selector_miss_before_any_call(
    workspace: Path,
) -> None:
    """§14.14 rule 3: selector resolution precedes the semantic pass."""

    ids, _facts, _snapshot, _branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )
    fake = FakeContractRunner([])

    with pytest.raises(SelectorNotFoundError):
        run_stage11(workspace, fake, ids, branch_name="no-such-branch")
    assert fake.calls == []


def test_a_blank_branch_name_is_rejected_as_bad_input(workspace: Path) -> None:
    """§14.10: the same non-blank §11 hygiene both generation forms apply."""

    ids, _facts, _snapshot, _branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )

    with pytest.raises(BranchNameInvalidError):
        run_stage11(workspace, FakeContractRunner([]), ids, branch_name="   ")


def test_a_superseded_branch_is_never_verified(workspace: Path) -> None:
    """§13.11: verification rejects a superseded branch."""

    ids, facts, snapshot_id, _branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )
    # A second generation of the same folded name supersedes the first, and
    # the selector then resolves only the replacement.
    replacement = run_stage10(
        workspace,
        FakeContractRunner(
            [writer_response([bullet_candidate(SECOND_TEXT, fact_ids=list(facts))])]
        ),
        ids,
        snapshot_id=snapshot_id,
    )
    assert replacement.branch_id is not None
    fake = FakeContractRunner(
        [verifier_response([finding(replacement.bullet_ids[0])])]
    )

    verified = run_stage11(workspace, fake, ids)

    assert verified.branch_id == replacement.branch_id
    payload = json.loads(fake.calls[0].serialized_input)
    assert [item["id"] for item in payload["resume_bullets"]] == list(
        replacement.bullet_ids
    )


def test_a_verified_bullet_never_returns_to_unverified(workspace: Path) -> None:
    """§13.11: Stage 11 owns the transition *out* of the generated state."""

    ids, _facts, _snapshot, _branch_id, bullet_ids = prepare_generated_branch(workspace)
    fake = FakeContractRunner(
        [
            verifier_response(
                [{**finding(bullet_ids[0]), "status": "unverified"}]
            )
        ]
        * 3
    )

    with pytest.raises(LLMInvocationError):
        run_stage11(workspace, fake, ids)


