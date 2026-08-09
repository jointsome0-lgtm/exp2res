"""Offline §13.10 bullet-generation tests.

The stage is the first writer of `resume_branches` and `resume_bullets`, so
these hold its whole contract: the anchored, complete-set invocation it is
allowed to make, the deterministic batch it persists from one response, the
folded-name replacement, and the no-bullet answer that completes as blocked.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

import pytest

from exp2res.errors import (
    BranchNameInvalidError,
    IntegrityFailureError,
    LLMCancelledError,
    LLMInvocationError,
    OperationCancelledError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
    SnapshotNotVerifiedError,
)
from exp2res.domain.models import AssessmentSnapshot, SelfClaim
from exp2res.pipeline.orchestration import withdraw_pending_unless_superseded
import exp2res.pipeline.stage10 as stage10_module
from exp2res.pipeline.stage10 import run_bullet_generation
from exp2res.storage.repository import (
    get_assessment_snapshot,
    get_resume_branch,
    list_resume_branches,
    list_resume_bullets_for_branch,
    update_assessment_snapshot_verification,
)
from exp2res.storage.workspace import (
    collect_preamble_residuals,
    connect_database,
    read_database,
    writer_database,
)

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_branch_substrate import (
    BRANCH_NAME,
    JOB_DESCRIPTION_ID,
    REQUIREMENT_ID,
    anchor_snapshot,
    plant_branch,
    plant_branch_set,
    plant_job_description,
)
from test_stage3_extraction import SELECTION, budgets
from test_stage6_assessment import (
    assessment_response,
    prepare_graph,
    run_stage6,
)


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


BULLET_TEXT = "Designed provenance links for an evidence-grounded workflow."
SECOND_TEXT = "Built a deterministic renderer for the mirror report."
THIRD_TEXT = "Wrote the migration that carried the schema forward."


def bullet_candidate(
    text: str = BULLET_TEXT,
    *,
    section: str = "selected_projects",
    requirements: list[str] | None = None,
    fact_ids: list[str] | None = None,
    claim_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "text": text,
        "target_section": section,
        "target_role_relevance": "high",
        "matched_jd_requirements": (
            [REQUIREMENT_ID] if requirements is None else requirements
        ),
        "source_fact_ids": fact_ids or [],
        "source_self_claim_ids": claim_ids or [],
    }


def writer_response(bullets: list[dict[str, object]]) -> bytes:
    payload = {"bullets": bullets, "warnings": []}
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def prepare_anchor(workspace: Path) -> tuple[object, tuple[str, ...], str]:
    """One current fact graph under a verified snapshot and a stored vacancy."""

    ids, facts = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    assert assessed.snapshot_id is not None
    anchor_snapshot(workspace, assessed.snapshot_id)
    plant_job_description(workspace)
    return ids, facts, assessed.snapshot_id


def run_stage10(
    workspace: Path,
    fake: FakeContractRunner,
    ids,
    *,
    snapshot_id: str,
    branch_name: str = BRANCH_NAME,
    job_description_id: str = JOB_DESCRIPTION_ID,
):
    return run_bullet_generation(
        workspace,
        job_description_id=job_description_id,
        snapshot_id=snapshot_id,
        branch_name=branch_name,
        selection=SELECTION,
        budgets=budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def test_one_invocation_carries_the_complete_current_set(workspace: Path) -> None:
    """§13.10: the whole pack in one call, with no service-side narrowing."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    fake = FakeContractRunner(
        [writer_response([bullet_candidate(fact_ids=list(facts))])]
    )

    generated = run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    assert len(fake.calls) == 1
    payload = json.loads(fake.calls[0].serialized_input)
    assert [item["fact"]["id"] for item in payload["selected_facts"]] == sorted(
        facts, key=lambda value: value.encode("utf-8")
    )
    assert payload["branch"]["assessment_snapshot_id"] == snapshot_id
    assert payload["job_description"]["id"] == JOB_DESCRIPTION_ID
    # §15.6: the vacancy reaches the writer as its parsed view only.
    assert "raw_text" not in payload["job_description"]

    assert generated.branch_id is not None
    with read_database(workspace) as connection:
        branch = get_resume_branch(connection, generated.branch_id)
        assert branch is not None
        assert branch.name == BRANCH_NAME
        assert branch.assessment_snapshot_id == snapshot_id
        bullets = list_resume_bullets_for_branch(connection, branch.id)
    assert [bullet.id for bullet in bullets] == list(generated.bullet_ids)
    # §13.10: Stage 10 cannot grant its own output permission to export, and
    # §15.11 keeps the log closure service-derived.
    assert all(bullet.verification_status == "unverified" for bullet in bullets)
    assert bullets[0].source_log_ids == ["log_vera_signal_0"]
    assert bullets[0].matched_jd_requirements == [REQUIREMENT_ID]


def test_the_persisted_batch_is_ordered_and_exact_duplicates_are_dropped(
    workspace: Path,
) -> None:
    """§13.10: section order, then match position, then text bytes."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    fake = FakeContractRunner(
        [
            writer_response(
                [
                    bullet_candidate(
                        SECOND_TEXT, section="skills", fact_ids=list(facts)
                    ),
                    bullet_candidate(BULLET_TEXT, fact_ids=list(facts)),
                    # An exact duplicate of the second candidate: byte-equal
                    # text is the only duplicate relation §13.10 recognizes.
                    bullet_candidate(BULLET_TEXT, fact_ids=list(facts)),
                    bullet_candidate(
                        THIRD_TEXT,
                        requirements=[],
                        fact_ids=list(facts),
                    ),
                ]
            )
        ]
    )

    generated = run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    assert generated.branch_id is not None
    with read_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, generated.branch_id)
    # `selected_projects` precedes `skills`, and inside a section the matched
    # candidate precedes the one that answers no requirement.
    assert [bullet.text for bullet in bullets] == [
        BULLET_TEXT,
        THIRD_TEXT,
        SECOND_TEXT,
    ]
    assert [bullet.target_section for bullet in bullets] == [
        "selected_projects",
        "selected_projects",
        "skills",
    ]
    assert len(generated.bullet_ids) == 3


def test_a_generated_name_replaces_exactly_the_branch_it_folds_onto(
    workspace: Path,
) -> None:
    """§13.10/§14.10: at most one generation of the named branch is current."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    prior_id, prior_bullet_id = plant_branch(
        workspace,
        snapshot_id=snapshot_id,
        fact_ids=facts,
        branch_id="branch_vera_0009",
        bullet_id="bullet_vera_0009",
        suffix="0009",
    )
    stale_set = plant_branch_set(workspace, prior_id)
    fake = FakeContractRunner(
        [writer_response([bullet_candidate(fact_ids=list(facts))])]
    )

    generated = run_stage10(
        workspace, fake, ids, snapshot_id=snapshot_id, branch_name="AGENT-ENGINEER"
    )

    assert generated.superseded_branch_ids == (prior_id,)
    assert generated.superseded_bullet_ids == (prior_bullet_id,)
    assert generated.invalidated_branches[0].name == BRANCH_NAME
    assert "gen_vera_branch_0009" in generated.superseded_generation_ids
    assert not stale_set.exists()
    with read_database(workspace) as connection:
        current = list_resume_branches(connection, current_only=True)
    # The owner's exact spelling is stored; only the folded identity matched.
    assert [branch.name for branch in current] == ["AGENT-ENGINEER"]


def test_an_empty_array_persists_nothing_and_blocks(workspace: Path) -> None:
    """§13.10: the honest no-bullet answer is a completed blocked result."""

    ids, _facts, snapshot_id = prepare_anchor(workspace)
    fake = FakeContractRunner([writer_response([])])

    generated = run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    assert generated.branch_id is None
    assert generated.bullet_ids == ()
    assert generated.generation_id is None
    with read_database(workspace) as connection:
        assert list_resume_branches(connection, current_only=False) == ()
        run_status = connection.execute(
            "SELECT status FROM processing_runs WHERE id = ?", (generated.run_id,)
        ).fetchone()
    # §14.14: a class-10 semantic outcome is not an operational failure.
    assert run_status["status"] == "completed"


def test_a_broken_aggregate_fails_before_the_eligibility_verdict(
    workspace: Path,
) -> None:
    """§16.11/§14.14: broken stored state is class 7, not a class-2 refusal."""

    ids, _facts, snapshot_id = prepare_anchor(workspace)
    with writer_database(workspace) as connection:
        # The members still reduce to `supported`; only the stored aggregate
        # is wrong, and it is wrong in a way the allowlist would also refuse.
        update_assessment_snapshot_verification(
            connection,
            snapshot_id=snapshot_id,
            verification_status="unsupported",
        )
        connection.commit()
    exhausted = FakeContractRunner([])

    with pytest.raises(IntegrityFailureError) as caught:
        run_stage10(workspace, exhausted, ids, snapshot_id=snapshot_id)

    assert caught.value.args == ("snapshot_aggregate_mismatch",)
    assert caught.value.exit_code == 7
    assert exhausted.calls == []


def test_a_claim_whose_facts_are_not_current_never_reaches_the_writer(
    workspace: Path,
) -> None:
    """§16.1: a supplied claim's provenance chain must resolve, or nothing runs."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    with writer_database(workspace) as connection:
        # Workspace damage, not an ordinary lifecycle: a claim stays
        # `supported` while one of the facts it cites stops being current.
        connection.execute(
            "UPDATE experience_facts SET superseded_at = ? WHERE id = ?",
            (FIXED_NOW.isoformat(), facts[0]),
        )
        connection.commit()
    exhausted = FakeContractRunner([])

    with pytest.raises(IntegrityFailureError) as caught:
        run_stage10(workspace, exhausted, ids, snapshot_id=snapshot_id)

    assert caught.value.args == ("claim_fact_superseded",)
    assert exhausted.calls == []
    with read_database(workspace) as connection:
        assert list_resume_branches(connection, current_only=False) == ()


def test_a_claim_without_any_provenance_is_refused(workspace: Path) -> None:
    """§16.1: a chainless claim has no closure to ground a bullet with."""

    _ids, _facts, _snapshot_id = prepare_anchor(workspace)
    chainless = SelfClaim(
        id="claim_vera_chainless",
        created_at=FIXED_NOW,
        snapshot_id="snapshot_vera_0001",
        claim="A claim standing on nothing at all.",
        claim_kind="hypothesis",
        dimension="gap",
        source_fact_ids=[],
        confidence="unknown",
        verification_status="supported",
    )

    with read_database(workspace) as connection:
        with pytest.raises(IntegrityFailureError) as caught:
            stage10_module._require_current_claim_facts(connection, (chainless,), ())

    assert caught.value.args == ("claim_source_facts_empty",)


def test_a_snapshot_reference_that_stopped_resolving_fails_closed(
    workspace: Path,
) -> None:
    """§11.7: a read-time consumer revalidates the snapshot's typed references."""

    _ids, _facts, snapshot_id = prepare_anchor(workspace)
    with read_database(workspace) as connection:
        stored = get_assessment_snapshot(connection, snapshot_id)
    assert stored is not None

    def damaged(**overrides) -> AssessmentSnapshot:
        # The stored payload is immutable by trigger, so the damaged states a
        # migration or direct edit could leave behind are built as models.
        values = stored.model_dump() | overrides
        return AssessmentSnapshot(**values)

    cases = (
        (
            damaged(gap_question_ids=["gap_vera_absent"]),
            "snapshot_gap_reference_invalid",
        ),
        (
            damaged(contradiction_ids=["contradiction_vera_absent"]),
            "snapshot_contradiction_reference_invalid",
        ),
    )
    with read_database(workspace) as connection:
        for snapshot, code in cases:
            with pytest.raises(IntegrityFailureError) as caught:
                stage10_module._require_current_snapshot_references(
                    connection, snapshot
                )
            assert caught.value.args == (code,)


def test_the_run_records_every_entity_the_writer_received(workspace: Path) -> None:
    """§12.13: telemetry names the evidence and requirements that transited."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    fake = FakeContractRunner(
        [writer_response([bullet_candidate(fact_ids=list(facts))])]
    )

    generated = run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    payload = json.loads(fake.calls[0].serialized_input)
    transited = {snapshot_id, JOB_DESCRIPTION_ID, REQUIREMENT_ID}
    for item in payload["selected_facts"]:
        transited.add(item["fact"]["id"])
        for evidence in item["evidence"]:
            transited.add(evidence["evidence_item"]["id"])
            if evidence.get("raw_log") is not None:
                transited.add(evidence["raw_log"]["id"])
    for claim in payload["supported_self_claims"]:
        transited.add(claim["id"])

    with read_database(workspace) as connection:
        row = connection.execute(
            "SELECT input_ids_json FROM processing_runs WHERE id = ?",
            (generated.run_id,),
        ).fetchone()
    recorded = json.loads(row["input_ids_json"])
    assert set(recorded) == transited
    assert recorded == sorted(transited, key=lambda value: value.encode("utf-8"))


def test_a_no_bullet_answer_leaves_the_current_branch_in_place(
    workspace: Path,
) -> None:
    """§15.6: the empty array replaces nothing — no branch, no partial commit."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    prior_id, prior_bullet_id = plant_branch(
        workspace,
        snapshot_id=snapshot_id,
        fact_ids=facts,
        branch_id="branch_vera_0009",
        bullet_id="bullet_vera_0009",
        suffix="0009",
    )
    kept_set = plant_branch_set(workspace, prior_id)
    fake = FakeContractRunner([writer_response([])])

    generated = run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    assert generated.branch_id is None
    assert generated.superseded_branch_ids == ()
    assert generated.superseded_bullet_ids == ()
    assert generated.invalidated_branches == ()
    # The prior generation is untouched, so neither its rows nor its published
    # managed set may be invalidated by a response that persists nothing.
    assert kept_set.exists()
    with read_database(workspace) as connection:
        current = list_resume_branches(connection, current_only=True)
        bullets = list_resume_bullets_for_branch(connection, prior_id)
    assert [branch.id for branch in current] == [prior_id]
    assert [bullet.id for bullet in bullets] == [prior_bullet_id]


def test_an_invalid_branch_name_is_ordinary_input_and_never_a_call(
    workspace: Path,
) -> None:
    """§14.10: a non-blank, §11-hygienic name, refused in exit class 2."""

    ids, _facts, snapshot_id = prepare_anchor(workspace)
    exhausted = FakeContractRunner([])

    for name in ("   ", "agent\u0007engineer"):
        with pytest.raises(BranchNameInvalidError) as caught:
            run_stage10(
                workspace, exhausted, ids, snapshot_id=snapshot_id, branch_name=name
            )
        assert caught.value.exit_code == 2
    assert exhausted.calls == []
    with read_database(workspace) as connection:
        assert list_resume_branches(connection, current_only=False) == ()


def test_an_interrupt_after_the_durable_commit_still_reports_the_swap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: cancellation between commit and return keeps the swap."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    prior_id, _prior_bullet_id = plant_branch(
        workspace,
        snapshot_id=snapshot_id,
        fact_ids=facts,
        branch_id="branch_vera_0009",
        bullet_id="bullet_vera_0009",
        suffix="0009",
    )
    stale_set = plant_branch_set(workspace, prior_id)
    complete_stage = stage10_module.run_complete_stage

    def cancel_after_commit(*arguments, **keywords):
        # The interrupt SQLite cannot undo: the final transaction is durable
        # and the stage has not returned yet.
        complete_stage(*arguments, **keywords)
        raise LLMCancelledError()

    monkeypatch.setattr(stage10_module, "run_complete_stage", cancel_after_commit)
    fake = FakeContractRunner(
        [writer_response([bullet_candidate(fact_ids=list(facts))])]
    )

    with pytest.raises(OperationCancelledError) as caught:
        run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    carried = caught.value.stage_result
    assert carried.branch_id is not None
    assert carried.branch is not None
    assert carried.superseded_branch_ids == (prior_id,)
    assert len(carried.bullets) == 1
    # Cleanup never ran, so the replaced branch's set is still a residual.
    assert str(stale_set) in carried.residual_paths
    with read_database(workspace) as connection:
        current = list_resume_branches(connection, current_only=True)
    assert [branch.id for branch in current] == [carried.branch_id]


def test_an_interrupt_in_the_result_read_still_reports_the_swap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the whole post-commit window is one guarded report."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    prior_id, _prior_bullet_id = plant_branch(
        workspace,
        snapshot_id=snapshot_id,
        fact_ids=facts,
        branch_id="branch_vera_0009",
        bullet_id="bullet_vera_0009",
        suffix="0009",
    )
    stale_set = plant_branch_set(workspace, prior_id)

    def interrupt_read(*_arguments, **_keywords):
        raise KeyboardInterrupt()

    # The interrupt lands between the durable commit and the cleanup, where no
    # error class marks it: a raw KeyboardInterrupt over committed rows.
    monkeypatch.setattr(stage10_module, "get_resume_branch", interrupt_read)
    fake = FakeContractRunner(
        [writer_response([bullet_candidate(fact_ids=list(facts))])]
    )

    with pytest.raises(OperationCancelledError) as caught:
        run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    carried = caught.value.stage_result
    assert carried.branch_id is not None
    assert carried.superseded_branch_ids == (prior_id,)
    assert len(carried.bullet_ids) == 1
    # Cleanup never started, so the replaced branch's set is still a residual.
    assert str(stale_set) in carried.residual_paths
    assert stale_set.exists()


def test_an_interrupt_in_the_writer_teardown_still_reports_the_swap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the guard outlives the writer lock's own release."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    opened = stage10_module.writer_database

    @contextmanager
    def interrupt_on_teardown(*arguments, **keywords):
        with opened(*arguments, **keywords) as connection:
            yield connection
        # Everything the stage does is done and durable; only the lock release
        # is left, and that is where the owner's Ctrl-C lands.
        raise KeyboardInterrupt()

    monkeypatch.setattr(stage10_module, "writer_database", interrupt_on_teardown)
    fake = FakeContractRunner(
        [writer_response([bullet_candidate(fact_ids=list(facts))])]
    )

    with pytest.raises(OperationCancelledError) as caught:
        run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    carried = caught.value.stage_result
    assert carried.branch_id is not None
    assert len(carried.bullet_ids) == 1
    assert carried.branch is not None
    with read_database(workspace) as connection:
        assert len(list_resume_branches(connection, current_only=True)) == 1


def test_a_rolled_back_branch_swap_withdraws_its_pending_report(
    workspace: Path,
) -> None:
    """The rollback proof must read the table the stage actually supersedes."""

    _ids, facts, snapshot_id = prepare_anchor(workspace)
    prior_id, _prior_bullet_id = plant_branch(
        workspace,
        snapshot_id=snapshot_id,
        fact_ids=facts,
        branch_id="branch_vera_0009",
        bullet_id="bullet_vera_0009",
        suffix="0009",
    )
    pending = ["out/branch/branch_vera_0009/manifest.json"]

    residuals: list[str] = []
    # A plain connection, because the proof runs where the stage's own
    # transaction has already ended.
    connection = connect_database(
        workspace / ".exp2res" / "exp2res.sqlite", readonly=True
    )
    with collect_preamble_residuals(residuals):
        try:
            residuals.extend(pending)
            # The prior branch is still current, which is exactly the proof
            # that the swap rolled back.
            withdraw_pending_unless_superseded(
                connection, pending, (prior_id,), table="resume_branches"
            )
            assert residuals == []
            residuals.extend(pending)
            # The snapshot table knows nothing about a branch ID, so the
            # default proof can never withdraw a branch stage's report.
            withdraw_pending_unless_superseded(connection, pending, (prior_id,))
        finally:
            connection.close()
    assert residuals == pending


def test_the_anchor_must_be_current_supplied_and_verified(workspace: Path) -> None:
    """§13.10/§16.11: no implicit latest snapshot and no unverified anchor."""

    ids, facts = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    assert assessed.snapshot_id is not None
    plant_job_description(workspace)
    exhausted = FakeContractRunner([])

    with pytest.raises(SelectorNotFoundError):
        run_stage10(
            workspace,
            exhausted,
            ids,
            snapshot_id=assessed.snapshot_id,
            job_description_id="jd_vera_absent",
        )
    with pytest.raises(SelectorNotFoundError):
        run_stage10(workspace, exhausted, ids, snapshot_id="snapshot_vera_absent")
    with pytest.raises(SnapshotNotVerifiedError) as ineligible:
        run_stage10(workspace, exhausted, ids, snapshot_id=assessed.snapshot_id)
    # The owner-facing message must describe this command, not `assess repair`,
    # while the diagnostic class stays the shared stable one.
    assert "Bullet generation" in ineligible.value.public_message
    assert ineligible.value.diagnostic_class == "snapshot_not_verified"

    anchor_snapshot(workspace, assessed.snapshot_id)
    regenerated = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    assert regenerated.snapshot_id != assessed.snapshot_id
    with pytest.raises(SnapshotNotCurrentError):
        run_stage10(workspace, exhausted, ids, snapshot_id=assessed.snapshot_id)
    assert exhausted.calls == []


def test_a_reference_outside_the_supplied_context_fails_the_response(
    workspace: Path,
) -> None:
    """§13.10: no free-form requirement label and no foreign typed ID."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    fake = FakeContractRunner(
        [
            writer_response(
                [
                    bullet_candidate(
                        requirements=["jdreq_vera_absent"], fact_ids=list(facts)
                    )
                ]
            )
        ]
        * 3
    )

    with pytest.raises(LLMInvocationError) as caught:
        run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    assert caught.value.exit_code == 7
    with read_database(workspace) as connection:
        assert list_resume_branches(connection, current_only=False) == ()


def test_texts_that_collapse_under_the_export_projection_fail_closed(
    workspace: Path,
) -> None:
    """§13.10: a projection collision is refused, never silently resolved."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
    # Two distinct stored spellings — combining acute versus precomposed —
    # that Stage 12's mandatory NFC projection would render byte-equal.
    decomposed = "Built a cafe\u0301 ordering flow end to end."
    precomposed = "Built a caf\u00e9 ordering flow end to end."
    assert decomposed != precomposed
    fake = FakeContractRunner(
        [
            writer_response(
                [
                    bullet_candidate(decomposed, fact_ids=list(facts)),
                    bullet_candidate(precomposed, fact_ids=list(facts)),
                ]
            )
        ]
    )

    # Like every other §13 business-invariant refusal, the collision reaches
    # the owner as the stage's class-7 failed commit; what §13.10 requires is
    # that the complete batch is refused rather than silently resolved.
    with pytest.raises(LLMInvocationError) as caught:
        run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    assert caught.value.exit_code == 7
    with read_database(workspace) as connection:
        assert list_resume_branches(connection, current_only=False) == ()
        run_row = connection.execute(
            "SELECT status FROM processing_runs WHERE stage = '13.10'"
        ).fetchone()
    assert run_row["status"] == "failed"


def test_a_post_commit_interrupt_keeps_the_committed_pack(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the durable swap is reported through the class-9 error."""

    ids, facts, snapshot_id = prepare_anchor(workspace)
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

    fake = FakeContractRunner(
        [writer_response([bullet_candidate(fact_ids=list(facts))])]
    )
    with pytest.raises(OperationCancelledError) as caught:
        run_stage10(workspace, fake, ids, snapshot_id=snapshot_id)

    carried = caught.value.stage_result
    assert carried.branch_id is not None
    assert carried.superseded_branch_ids == (prior_id,)
    assert str(stale_set) in carried.residual_paths
    with read_database(workspace) as connection:
        assert len(list_resume_branches(connection, current_only=True)) == 1
