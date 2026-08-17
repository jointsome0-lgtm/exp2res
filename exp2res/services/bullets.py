"""§14.10 verified bullet-pack services (Stages 10-11)."""

from __future__ import annotations

from pathlib import Path

from exp2res import __version__
from exp2res.errors import (
    LLMInvocationError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
)
from exp2res.pipeline.stage10 import (
    Stage10Result,
    run_bullet_generation,
    validated_branch_name,
)
from exp2res.pipeline.stage11 import Stage11Result, run_bullet_verification
from exp2res.services.capture import new_id
from exp2res.services.extraction import RunTracking, build_llm_execution
from exp2res.storage.repository import (
    current_branch_by_folded_name,
    get_assessment_snapshot,
    get_job_description,
)
from exp2res.storage.workspace import read_database, require_compatible


def validate_generate_selection(
    workspace: Path, *, job_description_id: str, snapshot_id: str
) -> None:
    """§14.14 rule 4: a bad selector is class 2, so it precedes the adapter.

    Otherwise a mistyped `--jd` in a workspace whose `[llm]` block is missing
    or malformed reports that configuration failure instead of the input
    error the owner can act on. Stage 10 re-resolves both under its writer
    lock, so a row deleted in between still fails in this same class.
    """

    with read_database(workspace) as connection:
        if get_job_description(connection, job_description_id) is None:
            raise SelectorNotFoundError()
        snapshot = get_assessment_snapshot(
            connection, snapshot_id, current_only=False
        )
    if snapshot is None:
        raise SelectorNotFoundError()
    if snapshot.superseded_at is not None:
        raise SnapshotNotCurrentError()


def run_bullets_generate(
    workspace: Path,
    *,
    job_description_id: str,
    snapshot_id: str,
    branch_name: str,
) -> Stage10Result:
    require_compatible(workspace)
    branch_name = validated_branch_name(branch_name)
    validate_generate_selection(
        workspace, job_description_id=job_description_id, snapshot_id=snapshot_id
    )
    selection, budgets, runner = build_llm_execution(workspace)
    tracking = RunTracking(new_id)
    try:
        return run_bullet_generation(
            workspace,
            job_description_id=job_description_id,
            snapshot_id=snapshot_id,
            branch_name=branch_name,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking.allocate,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        tracking.extend_committed_runs(workspace, error)
        raise


def run_bullets_verify(workspace: Path, *, branch_name: str) -> Stage11Result:
    require_compatible(workspace)
    # §14.14 rule 4: `--branch` is boundary text, so its §14.10 hygiene and
    # its resolution both settle in exit class 2 before any adapter is built.
    # Stage 11 re-resolves it under the writer lock, where a branch superseded
    # in between still fails in this same class.
    branch_name = validated_branch_name(branch_name)
    with read_database(workspace) as connection:
        if current_branch_by_folded_name(connection, branch_name) is None:
            raise SelectorNotFoundError()
    selection, budgets, runner = build_llm_execution(workspace)
    tracking = RunTracking(new_id)
    try:
        return run_bullet_verification(
            workspace,
            branch_name=branch_name,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking.allocate,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        tracking.extend_committed_runs(workspace, error)
        raise
