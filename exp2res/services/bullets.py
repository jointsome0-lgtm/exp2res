"""§14.10 verified bullet-pack services (Stages 10-11)."""

from __future__ import annotations

from pathlib import Path

from exp2res import __version__
from exp2res.errors import LLMInvocationError
from exp2res.pipeline.stage10 import (
    Stage10Result,
    run_bullet_generation,
    validated_branch_name,
)
from exp2res.pipeline.stage11 import Stage11Result, run_bullet_verification
from exp2res.services.capture import new_id
from exp2res.services.extraction import RunTracking, build_llm_execution
from exp2res.storage.workspace import require_compatible


def run_bullets_generate(
    workspace: Path,
    *,
    job_description_id: str,
    snapshot_id: str,
    branch_name: str,
) -> Stage10Result:
    require_compatible(workspace)
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
    # §14.14 rule 4: `--branch` is boundary text, so its §14.10 hygiene is
    # settled in exit class 2 before any adapter is built — the same ordering
    # `run_jd_add` gives vacancy text. *Resolving* the selector stays inside
    # the stage's own writer transaction, where every sibling command puts it.
    branch_name = validated_branch_name(branch_name)
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
