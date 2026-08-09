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

from exp2res.domain.models import ResumeBullet
from exp2res.llm.resume_verifier import (
    RESUME_VERIFIER_INSTRUCTIONS,
    ResumeVerifierOutput,
)
from exp2res.errors import (
    BranchNameInvalidError,
    IntegrityFailureError,
    LLMCancelledError,
    LLMInvocationError,
    OperationCancelledError,
    SelectorNotFoundError,
)
from exp2res.pipeline import stage11 as stage11_module
from exp2res.pipeline.stage11 import (
    require_consistent_bullets,
    require_current_anchor,
    require_one_generation,
    run_bullet_verification,
)
from exp2res.storage.repository import (
    current_branch_by_folded_name,
    get_job_description,
    get_resume_branch,
    list_resume_bullets_for_branch,
    list_self_claims_for_snapshot,
    list_verification_findings,
    update_self_claim_verification,
)
from exp2res.storage.workspace import (
    collect_preamble_residuals,
    read_database,
    writer_database,
)

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from assessment_helpers import VeraIds, prepare_facts
from test_branch_substrate import (
    BRANCH_NAME,
    JOB_DESCRIPTION_ID,
    REQUIREMENT_ID,
    anchor_snapshot,
    plant_branch_set,
    plant_job_description,
)
from test_stage6_assessment import assessment_response, run_stage6
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


def prepare_paired_anchor(workspace: Path):
    """`prepare_anchor`, but on two logs so a displacement leaves a chain."""

    ids = VeraIds()
    facts = prepare_facts(workspace, ids, count=2)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    assert assessed.snapshot_id is not None
    anchor_snapshot(workspace, assessed.snapshot_id)
    plant_job_description(workspace)
    return ids, facts, assessed.snapshot_id


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

    # Two facts on two logs: §16.1 still wants one retained direct chain at the
    # end of the bullet's closure, so the displaceable record is the *second*
    # one — a single-log pack has nothing left to stand on once it is gone.
    ids, facts, snapshot_id = prepare_paired_anchor(workspace)
    generated = run_stage10(
        workspace,
        FakeContractRunner(
            [writer_response([bullet_candidate(fact_ids=list(facts))])]
        ),
        ids,
        snapshot_id=snapshot_id,
    )
    branch_id, bullet_ids = generated.branch_id, generated.bullet_ids
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    assert len(bullets[0].source_log_ids) > 1
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
    # The retained half of the same closure still transits as an object.
    assert {item["id"] for item in payload["source_logs"]} == set(
        bullets[0].source_log_ids
    ) - {displaced}


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


def damaged_bullet(bullet: ResumeBullet, **overrides: object) -> ResumeBullet:
    """A stored bullet whose own typed references disagree with the graph.

    `insert_resume_bullet` and the §12 update guard make this state
    unreachable through the service, so the guard is exercised against a
    hand-built row rather than a workspace the writer refuses to produce.
    """

    return bullet.model_copy(update=overrides)


def test_an_unresolved_requirement_reference_fails_before_the_call(
    workspace: Path,
) -> None:
    """§15.7: a wrong-job or missing requirement ID is not a verdict."""

    _ids, _facts, _snapshot, branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        job_description = get_job_description(connection, JOB_DESCRIPTION_ID)
        assert job_description is not None
        with pytest.raises(IntegrityFailureError):
            require_consistent_bullets(
                connection,
                [
                    damaged_bullet(
                        bullets[0], matched_jd_requirements=["jdreq_vera_other_job"]
                    )
                ],
                job_description,
            )


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param("extra", id="extra-log"),
        pytest.param("missing", id="missing-log"),
    ],
)
def test_source_log_ids_must_equal_the_fact_closure(
    workspace: Path, spoil: str
) -> None:
    """§18: the named set equals the closure, so a superset also fails."""

    _ids, _facts, _snapshot, branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        job_description = get_job_description(connection, JOB_DESCRIPTION_ID)
        assert job_description is not None
        stored = bullets[0]
        assert stored.source_log_ids
        spoiled = (
            [*stored.source_log_ids, "log_vera_not_in_closure"]
            if spoil == "extra"
            else list(stored.source_log_ids[:-1])
        )
        # The unspoiled row passes, so the guard is rejecting the change.
        require_consistent_bullets(connection, [stored], job_description)
        with pytest.raises(IntegrityFailureError):
            require_consistent_bullets(
                connection,
                [damaged_bullet(stored, source_log_ids=spoiled)],
                job_description,
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




def test_cancellation_after_the_commit_reports_the_committed_pass(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: a durable pass survives an interrupt in the read window."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(workspace)
    fake = FakeContractRunner([verifier_response([finding(bullet_ids[0])])])
    genuine = stage11_module.list_verification_findings
    interrupts: list[int] = []

    def interrupt_once(*args: object, **kwargs: object):
        # One Ctrl-C, landing on the first post-commit read; the recovery
        # re-read that follows is the ordinary one.
        if not interrupts:
            interrupts.append(1)
            raise KeyboardInterrupt
        return genuine(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stage11_module, "list_verification_findings", interrupt_once)

    with pytest.raises(OperationCancelledError) as raised:
        run_stage11(workspace, fake, ids)

    recovered = raised.value.stage_result
    assert recovered is not None
    assert [item.target_id for item in recovered.findings] == [bullet_ids[0]]
    assert recovered.bullet_statuses == ((bullet_ids[0], "supported"),)
    assert recovered.export_blocked is False
    # The verdict is durable, not rolled back with the cancelled invocation.
    with read_database(workspace) as connection:
        stored = list_resume_bullets_for_branch(connection, branch_id, current_only=True)
    assert [bullet.verification_status for bullet in stored] == ["supported"]


def test_a_dead_assessment_anchor_fails_before_the_call(workspace: Path) -> None:
    """§18: a branch whose anchor no longer resolves never reaches a verdict."""

    _ids, _facts, snapshot_id, branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )
    with read_database(workspace) as connection:
        branch = get_resume_branch(connection, branch_id)
        assert branch is not None
        # The unspoiled branch passes, so the guard is rejecting the anchor.
        require_current_anchor(connection, branch)
        with pytest.raises(IntegrityFailureError):
            require_current_anchor(
                connection,
                branch.model_copy(update={"assessment_snapshot_id": "asmt_vera_gone"}),
            )

    # A superseded anchor is the second half of the same guard; §13.13 rule 4
    # supersedes the branch with it, so only damaged state reaches here.
    with writer_database(workspace) as connection:
        connection.execute(
            "UPDATE assessment_snapshots SET superseded_at = ? WHERE id = ?",
            (FIXED_NOW.isoformat(), snapshot_id),
        )
        connection.commit()
        branch = get_resume_branch(connection, branch_id)
        assert branch is not None
        with pytest.raises(IntegrityFailureError):
            require_current_anchor(connection, branch)


def test_two_current_branches_folding_equal_fail_the_selector(
    workspace: Path,
) -> None:
    """§14.10: an ambiguous folded identity resolves to nothing, not to one."""

    _ids, _facts, _snapshot, branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )
    with writer_database(workspace) as connection:
        # The uniqueness invariant is trigger-enforced, so the damaged state a
        # restore can produce is only reachable with the guard dropped.
        connection.execute("DROP TRIGGER resume_branches_lifecycle_update_guard")
        columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(resume_branches)")
        ]
        stored = connection.execute(
            "SELECT * FROM resume_branches WHERE id = ?", (branch_id,)
        ).fetchone()
        twin = {column: stored[column] for column in columns}
        # The `name` column is unique, so the ambiguity is two *spellings* that
        # fold to one identity — exactly what §14.10's fold is for.
        twin["id"] = "branch_vera_folded_twin"
        twin["name"] = BRANCH_NAME.upper()
        connection.execute(
            "INSERT INTO resume_branches ({columns}) VALUES ({binds})".format(
                columns=", ".join(columns),
                binds=", ".join(f":{column}" for column in columns),
            ),
            twin,
        )
        connection.commit()

    with read_database(workspace) as connection:
        with pytest.raises(IntegrityFailureError):
            current_branch_by_folded_name(connection, BRANCH_NAME)


def test_a_mixed_generation_pack_fails_before_the_call(workspace: Path) -> None:
    """§12 rule 13: a branch and its bullets are one jointly swapped batch."""

    _ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(
        workspace
    )
    with read_database(workspace) as connection:
        # The healthy pack passes, so the guard is rejecting the mismatch.
        require_one_generation(connection, branch_id)

    with writer_database(workspace) as connection:
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        connection.execute(
            "UPDATE resume_bullets SET generation_id = ? WHERE id = ?",
            ("gen_vera_other_batch", bullet_ids[0]),
        )
        connection.commit()
        with pytest.raises(IntegrityFailureError):
            require_one_generation(connection, branch_id)


def test_a_cited_claim_that_lost_supported_fails_before_the_call(
    workspace: Path,
) -> None:
    """§18: only a supported cited claim may ground an exported bullet."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    with read_database(workspace) as connection:
        claims = list_self_claims_for_snapshot(connection, snapshot_id)
    assert claims
    generated = run_stage10(
        workspace,
        FakeContractRunner(
            [
                writer_response(
                    [
                        bullet_candidate(
                            fact_ids=list(facts), claim_ids=[claims[0].id]
                        )
                    ]
                )
            ]
        ),
        ids,
        snapshot_id=snapshot_id,
    )
    assert generated.bullet_ids

    # Stage 10 checked the same thing at insert, so only a later change — a
    # re-verification, a restore — can leave the citation standing.
    with writer_database(workspace) as connection:
        update_self_claim_verification(
            connection,
            claim_id=claims[0].id,
            verification_status="needs_clarification",
            counterevidence=[],
        )
        connection.commit()

    fake = FakeContractRunner([verifier_response([finding(generated.bullet_ids[0])])])
    with pytest.raises(IntegrityFailureError):
        run_stage11(workspace, fake, ids)
    assert fake.calls == []


def test_a_pack_whose_direct_chain_is_all_displaced_never_reaches_a_verdict(
    workspace: Path,
) -> None:
    """§16.1: the chain's last link is a *retained* raw log, not just an ID."""

    ids, _facts, _snapshot, branch_id, _bullet_ids = prepare_generated_branch(
        workspace
    )
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
    assert len(bullets[0].source_log_ids) == 1
    add_log(
        workspace,
        log_id="log_vera_stage11_last_chain",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example self-contained corrected sole workflow statement.",
        occurred=month(),
        item_specs=(("evi_vera_stage11_last_chain", "manual_claim"),),
        corrects_log_id=bullets[0].source_log_ids[0],
    )

    fake = FakeContractRunner([])
    with pytest.raises(IntegrityFailureError):
        run_stage11(workspace, fake, ids)
    assert fake.calls == []


def test_the_findings_array_carries_the_rule_38_cap() -> None:
    """§11 rule 38: only §15.7's *input* arrays are exempt from the cap."""

    field = ResumeVerifierOutput.model_fields["findings"]
    assert any(
        getattr(item, "max_length", None) == 1_000 for item in field.metadata
    )


def test_cancellation_inside_orchestration_reports_the_committed_pass(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the commit-to-return window is class 9 *with* the pass."""

    ids, _facts, _snapshot, branch_id, bullet_ids = prepare_generated_branch(workspace)
    fake = FakeContractRunner([verifier_response([finding(bullet_ids[0])])])
    orchestrate = stage11_module.run_complete_stage

    def commit_then_cancel(*args, **kwargs):
        # Exactly what orchestration does when Ctrl-C lands after its business
        # transaction commits: the run row is `completed`, and the interrupt
        # leaves as `LLMCancelledError` rather than a `KeyboardInterrupt`.
        orchestrate(*args, **kwargs)
        raise LLMCancelledError() from None

    monkeypatch.setattr(stage11_module, "run_complete_stage", commit_then_cancel)

    with pytest.raises(OperationCancelledError) as raised:
        run_stage11(workspace, fake, ids)

    recovered = raised.value.stage_result
    assert recovered is not None
    assert [item.target_id for item in recovered.findings] == [bullet_ids[0]]
    assert recovered.bullet_statuses == ((bullet_ids[0], "supported"),)
    with read_database(workspace) as connection:
        stored = list_resume_bullets_for_branch(connection, branch_id, current_only=True)
    assert [bullet.verification_status for bullet in stored] == ["supported"]


def test_an_unsorted_stored_closure_is_still_the_same_closure(
    workspace: Path,
) -> None:
    """§18 equality is on the set: `insert_resume_bullet` stores caller order."""

    ids, facts, snapshot_id = prepare_paired_anchor(workspace)
    generated = run_stage10(
        workspace,
        FakeContractRunner(
            [writer_response([bullet_candidate(fact_ids=list(facts))])]
        ),
        ids,
        snapshot_id=snapshot_id,
    )
    with read_database(workspace) as connection:
        stored = list_resume_bullets_for_branch(connection, generated.branch_id)[0]
        job_description = get_job_description(connection, JOB_DESCRIPTION_ID)
        assert job_description is not None
        assert len(stored.source_log_ids) > 1
        # The same closure, written in the other order, is the same closure.
        require_consistent_bullets(
            connection,
            [
                damaged_bullet(
                    stored, source_log_ids=list(reversed(stored.source_log_ids))
                )
            ],
            job_description,
        )


def test_the_verifier_is_told_what_a_counter_fact_means() -> None:
    """§15.6: Stage 11 judges against the same contrary-role marking."""

    assert "counter_fact_ids" in RESUME_VERIFIER_INSTRUCTIONS
    # §15.1 rule 11 wants both halves: the prohibition and the licensed form.
    assert "grounds no bullet wording through that claim" in (
        RESUME_VERIFIER_INSTRUCTIONS
    )
    assert "may still ground the bullet directly" in RESUME_VERIFIER_INSTRUCTIONS


@pytest.mark.parametrize(
    "clause",
    [
        # §16.4: the fail-closed half of the ownership rule.
        pytest.param("fails closed", id="ownership-unnormalizable"),
        # §16.6: `impact` is the first member of the protected list.
        pytest.param("Impact, production, customer", id="impact-language"),
        # §16.7 rule 3: comparisons run on the UTC instant, not wall clock.
        pytest.param("by the UTC instant", id="temporal-utc"),
        # §16.7 rules 4-6: the widths and the anchored half-open intervals.
        pytest.param("quarter 92 days", id="temporal-widths"),
        pytest.param("half-open interval from its start", id="temporal-intervals"),
        # §16.9: the licensed bounded-pattern half, not the prohibition alone.
        pytest.param("a recurring pattern appears", id="identity-licensed"),
        # §16.10: reporting an experience in the owner's own terms is allowed.
        pytest.param("you report burnout under ambitious plans", id="diagnostic-licensed"),
        # §13.10: the relevance grade is judged, not only its requirement IDs.
        pytest.param("invented relevance", id="relevance-grade"),
        # §16.7 rule 18: approximate bounds are weaker at equal width.
        pytest.param("at equal width an approximate_range", id="temporal-exactness"),
        # §16.7 rules 16/20: another record may license the end date.
        pytest.param(
            "additional linked evidence itself states the bound", id="temporal-bound"
        ),
        # §16.12: an advisory rewrite is generated voice and is bound by §16.
        pytest.param(
            "A rewrite you cannot ground is no rewrite", id="rewrite-grounded"
        ),
    ],
)
def test_the_instructions_carry_each_16_rule_in_full(clause: str) -> None:
    """§15.1 rule 11: every §16 rule this contract enforces, in both halves."""

    assert clause in RESUME_VERIFIER_INSTRUCTIONS
