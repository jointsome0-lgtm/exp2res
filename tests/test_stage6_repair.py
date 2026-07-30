"""§13.6 deterministic claim repair (§21.60)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from exp2res.errors import (
    NothingToRepairError,
    RewriteUnavailableError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
    SnapshotNotVerifiedError,
)
from exp2res.pipeline.stage6 import run_assessment_repair
from exp2res.storage.repository import (
    list_assessment_snapshots,
    list_self_claims_for_snapshot,
)
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage6_assessment import plant_assessment_set
from test_stage7_verification import (
    generated_snapshot,
    run_stage7,
    verifier_response,
)


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


REWRITE = "The supplied evidence supports a narrower statement."


def custom_verifier_response(status: str, rewrite: str | None) -> bytes:
    return json.dumps(
        {
            "status": status,
            "reason": "The supplied evidence requires a non-passing verdict.",
            "unsupported_phrases": ["unsupported scale wording"],
            "counterevidence": [],
            "suggested_rewrite": rewrite,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def repair(workspace: Path, ids, snapshot_id: str):
    return run_assessment_repair(
        workspace,
        snapshot_id=snapshot_id,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
    )


def test_repair_adopts_latest_rewrite_and_supersedes_the_view(
    workspace: Path,
) -> None:
    """§13.6/§11.14: one swap adopts the rewrite into a new unverified set."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response("rejected"), verifier_response("supported")]
        ),
        ids,
        generated.snapshot_id,
    )
    with read_database(workspace) as connection:
        prior_claims = {
            claim.id: claim
            for claim in list_self_claims_for_snapshot(
                connection, generated.snapshot_id
            )
        }
    stale_path = plant_assessment_set(workspace, generated.snapshot_id)

    repaired = repair(workspace, ids, generated.snapshot_id)

    assert repaired.snapshot is not None
    assert repaired.snapshot_id != generated.snapshot_id
    assert repaired.superseded_snapshot_ids == (generated.snapshot_id,)
    assert set(repaired.superseded_claim_ids) == set(prior_claims)
    assert repaired.warnings == ()
    assert repaired.residual_paths == ()
    assert not stale_path.exists()
    assert (
        repaired.replaced_view is not None
        and repaired.replaced_view.snapshot_id == generated.snapshot_id
    )

    # Every member copies as a new unverified row; only the rejected claim's
    # text changes, and it changes to exactly the adopted rewrite.
    assert repaired.snapshot.metadata == {
        "repaired_from_snapshot_id": generated.snapshot_id
    }
    assert repaired.snapshot.verification_status == "unverified"
    by_source = {
        claim.metadata.get("adopted_rewrite_of_claim_id"): claim
        for claim in repaired.claims
    }
    rejected_prior = next(
        claim
        for claim in prior_claims.values()
        if claim.verification_status == "rejected"
    )
    supported_prior = next(
        claim
        for claim in prior_claims.values()
        if claim.verification_status == "supported"
    )
    adopted = by_source[rejected_prior.id]
    untouched = by_source[None]
    assert adopted.claim == REWRITE
    assert adopted.claim_kind == rejected_prior.claim_kind
    assert adopted.dimension == rejected_prior.dimension
    assert adopted.source_signal_ids == rejected_prior.source_signal_ids
    assert adopted.source_fact_ids == rejected_prior.source_fact_ids
    assert adopted.confidence == rejected_prior.confidence
    assert untouched.claim == supported_prior.claim
    assert untouched.metadata == {}
    for claim in repaired.claims:
        assert claim.verification_status == "unverified"
        assert claim.counterevidence == []
        assert claim.snapshot_id == repaired.snapshot_id
        assert claim.id not in prior_claims
    # Summary copies the (here unrepaired) narrative member's text.
    assert repaired.snapshot.summary == supported_prior.claim
    assert repaired.snapshot.gap_question_ids == list(
        generated.snapshot.gap_question_ids
    )
    assert repaired.snapshot.contradiction_ids == list(
        generated.snapshot.contradiction_ids
    )

    with read_database(workspace) as connection:
        current = list_assessment_snapshots(connection)
        assert [item.id for item in current] == [repaired.snapshot_id]
        run = connection.execute(
            "SELECT stage, status, provider, model, prompt_policy_hash "
            "FROM processing_runs WHERE id = ?",
            (repaired.run_id,),
        ).fetchone()
        calls = connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE run_id = ?",
            (repaired.run_id,),
        ).fetchone()[0]
    assert tuple(run) == ("13.6", "completed", None, None, None)
    assert calls == 0
    assert repaired.generation_id is not None
    assert repaired.generation_id not in repaired.superseded_generation_ids


def test_repaired_snapshot_takes_an_ordinary_full_reverification(
    workspace: Path,
) -> None:
    """§13.6/§13.7: repair pre-authorizes nothing; Stage 7 judges afresh."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response("unsupported"), verifier_response("supported")]
        ),
        ids,
        generated.snapshot_id,
    )
    repaired = repair(workspace, ids, generated.snapshot_id)
    reverified = run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response("supported"), verifier_response("supported")]
        ),
        ids,
        repaired.snapshot_id,
    )
    assert reverified.snapshot_status == "supported"
    assert len(reverified.findings) == 2


def test_latest_finding_wins_when_history_holds_two_attempts(
    workspace: Path,
) -> None:
    """§13.6: the adopted text is the latest (created_at, ID) finding's."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner(
            [
                custom_verifier_response("rejected", "An earlier rewrite."),
                verifier_response("supported"),
            ]
        ),
        ids,
        generated.snapshot_id,
    )
    run_stage7(
        workspace,
        FakeContractRunner(
            [
                custom_verifier_response("rejected", "The later rewrite."),
                verifier_response("supported"),
            ]
        ),
        ids,
        generated.snapshot_id,
    )
    repaired = repair(workspace, ids, generated.snapshot_id)
    adopted = next(
        claim
        for claim in repaired.claims
        if "adopted_rewrite_of_claim_id" in claim.metadata
    )
    assert adopted.claim == "The later rewrite."


def test_precondition_failures_are_stable_and_change_nothing(
    workspace: Path,
) -> None:
    """§13.6/§14.9: each violated precondition fails closed in class 2."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)

    def snapshot_state():
        with read_database(workspace) as connection:
            snapshots = [
                (item.id, item.verification_status)
                for item in list_assessment_snapshots(connection)
            ]
            claims = [
                (item.id, item.verification_status, item.claim)
                for item in list_self_claims_for_snapshot(
                    connection, generated.snapshot_id
                )
            ]
            runs = connection.execute(
                "SELECT COUNT(*) FROM processing_runs WHERE stage = '13.6' "
                "AND provider IS NULL"
            ).fetchone()[0]
        return snapshots, claims, runs

    with pytest.raises(SelectorNotFoundError):
        repair(workspace, ids, "snapshot_vera_missing")

    # Unverified members: generation alone is not repairable state.
    before = snapshot_state()
    with pytest.raises(SnapshotNotVerifiedError):
        repair(workspace, ids, generated.snapshot_id)
    assert snapshot_state() == before

    # Fully verified with no rejected/unsupported member: nothing to repair.
    run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response("contradicted"), verifier_response("supported")]
        ),
        ids,
        generated.snapshot_id,
    )
    before = snapshot_state()
    with pytest.raises(NothingToRepairError):
        repair(workspace, ids, generated.snapshot_id)
    assert snapshot_state() == before

    # A repairable member whose latest finding has a null rewrite.
    run_stage7(
        workspace,
        FakeContractRunner(
            [
                custom_verifier_response("rejected", None),
                verifier_response("supported"),
            ]
        ),
        ids,
        generated.snapshot_id,
    )
    before = snapshot_state()
    with pytest.raises(RewriteUnavailableError):
        repair(workspace, ids, generated.snapshot_id)
    assert snapshot_state() == before


def test_superseded_snapshot_selector_is_not_repairable(workspace: Path) -> None:
    """§13.6: repair operates on the current snapshot only."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response("rejected"), verifier_response("supported")]
        ),
        ids,
        generated.snapshot_id,
    )
    repair(workspace, ids, generated.snapshot_id)
    with pytest.raises(SnapshotNotCurrentError):
        repair(workspace, ids, generated.snapshot_id)
