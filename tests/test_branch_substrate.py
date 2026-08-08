"""§11/§12/§13.10 branch and bullet substrate, and its §13.13 invalidation.

Stage 10 lands in its own session, so these tests plant branches and bullets
through the repository the way that stage will. What they exercise is the
substrate underneath it: storage-level provenance validation, §14.10's folded
replacement identity, and the four lifecycle triggers that leave a branch
dishonest against the graph it was generated from.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from exp2res.domain.models import (
    JobDescription,
    JDRequirement,
    ParsedJD,
    ResumeBranch,
    ResumeBullet,
    canonical_branch_identity,
)
from exp2res.errors import IntegrityFailureError
from exp2res.services.correction import capture_correction
from exp2res.services.logs import delete_log
from exp2res.storage.repository import (
    bullet_log_closure,
    current_branch_name_conflict,
    get_resume_branch,
    insert_job_description,
    insert_resume_branch,
    insert_resume_bullet,
    list_resume_branches,
    list_resume_bullets_for_branch,
)
from exp2res.storage.telemetry import create_processing_run, finish_processing_run
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import exact_day, fact_response, run_stage3
from test_stage4_detection import alt_selection, detector_response, run_stage4
from test_stage6_assessment import (
    AssessmentIds,
    assessment_response,
    prepare_graph,
    run_stage6,
)
from test_stage7_verification import run_stage7, verifier_response


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


BRANCH_NAME = "agent-engineer"
JOB_DESCRIPTION_ID = "jd_vera_0001"
REQUIREMENT_ID = "jdreq_vera_0001"


def plant_job_description(workspace: Path) -> str:
    """Store the vacancy a branch is generated against (§13.8)."""

    with writer_database(workspace) as connection:
        insert_job_description(
            connection,
            JobDescription(
                id=JOB_DESCRIPTION_ID,
                created_at=FIXED_NOW,
                title="Agent Engineer",
                company="Example Co",
                raw_text="Vera Example vacancy text.",
                parsed=ParsedJD(
                    requirements=[
                        JDRequirement(
                            id=REQUIREMENT_ID,
                            kind="required_skill",
                            text="Build evidence-grounded LLM workflows.",
                            keywords=["provenance"],
                        )
                    ],
                ),
            ),
        )
        connection.commit()
    return JOB_DESCRIPTION_ID


def plant_branch(
    workspace: Path,
    *,
    snapshot_id: str,
    fact_ids: tuple[str, ...],
    claim_ids: tuple[str, ...] = (),
    job_description_id: str = JOB_DESCRIPTION_ID,
    requirement_ids: tuple[str, ...] = (REQUIREMENT_ID,),
    name: str = BRANCH_NAME,
    branch_id: str = "branch_vera_0001",
    bullet_id: str = "bullet_vera_0001",
    suffix: str = "0001",
) -> tuple[str, str]:
    """Persist one current branch with one current bullet on it."""

    run_id = f"run_vera_branch_{suffix}"
    generation_id = f"gen_vera_branch_{suffix}"
    with writer_database(workspace) as connection:
        create_processing_run(
            connection,
            run_id=run_id,
            stage="13.10",
            started_at=FIXED_NOW,
            provider=None,
            model=None,
            prompt_policy_hash=None,
            input_ids=fact_ids,
        )
        finish_processing_run(
            connection,
            run_id=run_id,
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=(branch_id, bullet_id),
        )
        insert_resume_branch(
            connection,
            ResumeBranch(
                id=branch_id,
                name=name,
                assessment_snapshot_id=snapshot_id,
                job_description_id=job_description_id,
                created_at=FIXED_NOW,
            ),
            produced_by_run_id=run_id,
            generation_id=generation_id,
        )
        insert_resume_bullet(
            connection,
            ResumeBullet(
                id=bullet_id,
                created_at=FIXED_NOW,
                branch_id=branch_id,
                text="Designed provenance links for an evidence-grounded workflow.",
                target_section="selected_projects",
                target_role_relevance="high",
                matched_jd_requirements=list(requirement_ids),
                source_fact_ids=list(fact_ids),
                source_log_ids=list(bullet_log_closure(connection, fact_ids)),
                source_self_claim_ids=list(claim_ids),
                verification_status="unverified",
            ),
            produced_by_run_id=run_id,
            generation_id=generation_id,
        )
        connection.commit()
    return branch_id, bullet_id


def plant_branch_set(workspace: Path, branch_id: str) -> Path:
    """§13.14 rule 1: the ID-keyed managed set a supersession makes stale."""

    parent = workspace / "out" / "branch"
    parent.mkdir(mode=0o700, exist_ok=True)
    path = parent / branch_id
    path.mkdir(mode=0o700)
    (path / "Vera Example stale member").write_text(
        "Vera Example stale member\n", encoding="utf-8"
    )
    return path


def prepare_branch(workspace: Path) -> tuple[AssessmentIds, tuple[str, ...], str, str]:
    """One current fact graph, one current view, one branch on a stale set."""

    ids, facts = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    plant_job_description(workspace)
    branch_id, _bullet_id = plant_branch(
        workspace, snapshot_id=assessed.snapshot_id, fact_ids=facts
    )
    return ids, facts, assessed.snapshot_id, branch_id


def test_folded_identity_controls_replacement_and_never_a_path(
    workspace: Path,
) -> None:
    """§14.10: NFC plus case folding, with no trim and no path semantics."""

    # Surrounding whitespace and a dot segment are ordinary display names,
    # because §13.14 publishes only under `out/branch/<branch-id>/`.
    assert canonical_branch_identity(" Agent-Engineer ") == " agent-engineer "
    assert canonical_branch_identity("../assessment") == "../assessment"
    # Decomposed and precomposed spellings are one identity; case is not.
    assert canonical_branch_identity("Straße") == canonical_branch_identity("STRASSE")
    assert canonical_branch_identity("cafe\u0301") == canonical_branch_identity(
        "caf\u00e9"
    )

    _ids, _facts, snapshot_id, branch_id = prepare_branch(workspace)
    with read_database(workspace) as connection:
        conflict = current_branch_name_conflict(connection, "AGENT-ENGINEER")
        assert conflict is not None and conflict.id == branch_id
        assert current_branch_name_conflict(connection, " agent-engineer") is None

    with writer_database(workspace) as connection:
        create_processing_run(
            connection,
            run_id="run_vera_branch_0002",
            stage="13.10",
            started_at=FIXED_NOW,
            provider=None,
            model=None,
            prompt_policy_hash=None,
        )
        with pytest.raises(IntegrityFailureError) as error:
            insert_resume_branch(
                connection,
                ResumeBranch(
                    id="branch_vera_0002",
                    name="Agent-Engineer",
                    assessment_snapshot_id=snapshot_id,
                    job_description_id=JOB_DESCRIPTION_ID,
                    created_at=FIXED_NOW,
                ),
                produced_by_run_id="run_vera_branch_0002",
                generation_id="gen_vera_branch_0002",
            )
        assert error.value.diagnostic_class == "integrity_failure"
        connection.rollback()


def test_a_blank_branch_name_is_the_only_rejected_spelling() -> None:
    """§14.10: a non-blank display name, with no path-specific rejection."""

    for name in ("../assessment", "a/b", "a\\b", " padded ", ".", "assessment"):
        assert ResumeBranch(
            id="branch_vera_0001",
            name=name,
            assessment_snapshot_id="snapshot_vera_0001",
            job_description_id=JOB_DESCRIPTION_ID,
            created_at=FIXED_NOW,
        ).name == name
    with pytest.raises(ValueError):
        ResumeBranch(
            id="branch_vera_0001",
            name="   ",
            assessment_snapshot_id="snapshot_vera_0001",
            job_description_id=JOB_DESCRIPTION_ID,
            created_at=FIXED_NOW,
        )


def test_bullet_storage_requires_the_exact_service_derived_log_closure(
    workspace: Path,
) -> None:
    """§15.11: `source_log_ids` is derived, so no writer-shaped set is stored."""

    ids, facts = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    plant_job_description(workspace)
    plant_branch(workspace, snapshot_id=assessed.snapshot_id, fact_ids=facts)

    with writer_database(workspace) as connection:
        closure = bullet_log_closure(connection, facts)
        assert closure == ("log_vera_signal_0",)
        with pytest.raises(IntegrityFailureError) as error:
            insert_resume_bullet(
                connection,
                ResumeBullet(
                    id="bullet_vera_0002",
                    created_at=FIXED_NOW,
                    branch_id="branch_vera_0001",
                    text="Designed provenance links without their raw records.",
                    target_section="selected_projects",
                    target_role_relevance="medium",
                    source_fact_ids=list(facts),
                    # A log outside the cited facts' closure: provenance is
                    # not the writer's to reshape.
                    source_log_ids=["log_vera_signal_absent"],
                    verification_status="unverified",
                ),
                produced_by_run_id="run_vera_branch_0001",
                generation_id="gen_vera_branch_0001",
            )
        assert error.value.diagnostic_class == "integrity_failure"
        connection.rollback()


def test_bullet_storage_refuses_an_unverified_claim_and_a_foreign_requirement(
    workspace: Path,
) -> None:
    """§13.10: only `supported` member claims and supplied requirement IDs."""

    ids, facts = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    plant_job_description(workspace)

    def bullet(**overrides) -> ResumeBullet:
        base = dict(
            id="bullet_vera_0002",
            created_at=FIXED_NOW,
            branch_id="branch_vera_0001",
            text="Designed provenance links for an evidence-grounded workflow.",
            target_section="selected_projects",
            target_role_relevance="medium",
            source_fact_ids=list(facts),
            source_log_ids=["log_vera_signal_0"],
            verification_status="unverified",
        )
        base.update(overrides)
        return ResumeBullet(**base)

    plant_branch(workspace, snapshot_id=assessed.snapshot_id, fact_ids=facts)
    with writer_database(workspace) as connection:
        # Stage 6 leaves every claim `unverified`, so none may guide a bullet
        # until Stage 7 has granted it `supported`.
        with pytest.raises(IntegrityFailureError):
            insert_resume_bullet(
                connection,
                bullet(source_self_claim_ids=[assessed.created_claim_ids[0]]),
                produced_by_run_id="run_vera_branch_0001",
                generation_id="gen_vera_branch_0001",
            )
        with pytest.raises(IntegrityFailureError):
            insert_resume_bullet(
                connection,
                bullet(matched_jd_requirements=["jdreq_vera_absent"]),
                produced_by_run_id="run_vera_branch_0001",
                generation_id="gen_vera_branch_0001",
            )
        connection.rollback()


def test_fact_replacement_supersedes_the_branch_and_removes_its_set(
    workspace: Path,
) -> None:
    """§13.10/§13.13 rule 9: Stage 3 replacement invalidates every branch."""

    ids, _facts, snapshot_id, branch_id = prepare_branch(workspace)
    stale_set = plant_branch_set(workspace, branch_id)

    extracted = run_stage3(
        workspace,
        FakeContractRunner([fact_response(["evi_vera_signal_0"])]),
        ids,
        log_id="log_vera_signal_0",
    )

    assert extracted.superseded_branch_ids == (branch_id,)
    assert extracted.superseded_bullet_ids == ("bullet_vera_0001",)
    report = extracted.invalidated_branches[0]
    assert report.name == BRANCH_NAME
    assert report.job_description_id == JOB_DESCRIPTION_ID
    assert report.former_view.snapshot_id == snapshot_id
    assert report.former_view.scope == "global"
    # The shape is deliberately not executable: the new snapshot does not
    # exist until the invalidated view is regenerated (§13.13 rule 9).
    assert report.regeneration_command_shape == (
        "exp2res bullets generate --jd 'jd_vera_0001' "
        "--snapshot <new-snapshot-id> --branch 'agent-engineer'"
    )
    assert not stale_set.exists()
    with read_database(workspace) as connection:
        assert list_resume_branches(connection, current_only=True) == ()
        assert list_resume_bullets_for_branch(
            connection, branch_id, current_only=False
        )[0].superseded_at is not None


def test_detection_replacement_supersedes_the_branch(workspace: Path) -> None:
    """§13.4/§13.10: a replaced detection set replaces the whole view layer."""

    ids, facts, _snapshot_id, branch_id = prepare_branch(workspace)
    stale_set = plant_branch_set(workspace, branch_id)

    changed = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=facts[0],
                    left=("experience_fact", facts[0]),
                    right=("raw_log", "log_vera_signal_0"),
                )
            ]
        ),
        ids,
        selection=alt_selection(1),
    )

    assert changed.superseded_branch_ids == (branch_id,)
    assert changed.invalidated_branches[0].name == BRANCH_NAME
    assert not stale_set.exists()


def test_view_regeneration_supersedes_only_its_dependent_branches(
    workspace: Path,
) -> None:
    """§13.6: the replaced snapshot's branches go; nothing else does."""

    ids, facts, snapshot_id, branch_id = prepare_branch(workspace)
    stale_set = plant_branch_set(workspace, branch_id)

    regenerated = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )

    assert regenerated.superseded_snapshot_ids == (snapshot_id,)
    assert regenerated.superseded_branch_ids == (branch_id,)
    assert regenerated.superseded_bullet_ids == ("bullet_vera_0001",)
    assert regenerated.invalidated_branches[0].former_view.snapshot_id == snapshot_id
    assert not stale_set.exists()


def test_a_changed_verifier_state_supersedes_the_anchored_branches(
    workspace: Path,
) -> None:
    """§13.7: the branch was generated against the prior verifier state."""

    ids, _facts, snapshot_id, branch_id = prepare_branch(workspace)
    stale_set = plant_branch_set(workspace, branch_id)

    verified = run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response(status="supported") for _ in range(2)]
        ),
        ids,
        snapshot_id=snapshot_id,
    )

    assert verified.superseded_branch_ids == (branch_id,)
    assert verified.superseded_bullet_ids == ("bullet_vera_0001",)
    assert verified.invalidated_branches[0].name == BRANCH_NAME
    assert verified.superseded_generation_ids == ("gen_vera_branch_0001",)
    assert not stale_set.exists()


def test_correction_capture_supersedes_every_current_branch(
    workspace: Path,
) -> None:
    """§13.13 rule 4: one atomic visibility boundary takes the branches too."""

    _ids, _facts, _snapshot_id, branch_id = prepare_branch(workspace)
    stale_set = plant_branch_set(workspace, branch_id)

    corrected = capture_correction(
        workspace,
        log_id="log_vera_signal_0",
        raw_text="Vera Example corrected the workflow record.",
        occurred=exact_day(15),
        project=None,
        clock=lambda: FIXED_NOW + timedelta(hours=1),
    )

    assert corrected.superseded_branch_ids == (branch_id,)
    assert corrected.superseded_bullet_ids == ("bullet_vera_0001",)
    assert corrected.invalidated_branches[0].name == BRANCH_NAME
    assert not stale_set.exists()


def test_owner_log_deletion_purges_branches_and_bullets(workspace: Path) -> None:
    """§13.13 rule 5: the privacy purge reaches the branch layer too."""

    _ids, _facts, snapshot_id, branch_id = prepare_branch(workspace)
    stale_set = plant_branch_set(workspace, branch_id)

    deleted = delete_log(workspace, log_id="log_vera_signal_0")

    assert deleted.purged_branch_ids == (branch_id,)
    assert deleted.purged_bullet_ids == ("bullet_vera_0001",)
    assert deleted.invalidated_branches[0].former_view.snapshot_id == snapshot_id
    assert not stale_set.exists()
    with read_database(workspace) as connection:
        assert get_resume_branch(connection, branch_id, current_only=False) is None
        assert connection.execute(
            "SELECT COUNT(*) FROM resume_bullets"
        ).fetchone()[0] == 0
