"""§14.10 verified bullet-pack services (Stage 10)."""

from __future__ import annotations

from pathlib import Path

from exp2res import __version__
from exp2res.errors import LLMInvocationError
from exp2res.pipeline.stage10 import Stage10Result, run_bullet_generation
from exp2res.services.capture import new_id
from exp2res.services.extraction import build_llm_execution
from exp2res.storage.workspace import read_database, require_compatible


def _committed_runs(workspace: Path, run_ids: list[str]) -> tuple[str, ...]:
    if not run_ids:
        return ()
    placeholders = ",".join("?" for _ in run_ids)
    with read_database(workspace) as connection:
        rows = connection.execute(
            f"SELECT id FROM processing_runs WHERE id IN ({placeholders})", run_ids
        ).fetchall()
    committed = {row[0] for row in rows}
    return tuple(run_id for run_id in run_ids if run_id in committed)


def run_bullets_generate(
    workspace: Path,
    *,
    job_description_id: str,
    snapshot_id: str,
    branch_name: str,
) -> Stage10Result:
    require_compatible(workspace)
    selection, budgets, runner = build_llm_execution(workspace)
    allocated_runs: list[str] = []

    def tracking_id_factory(kind: str) -> str:
        value = new_id(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    try:
        return run_bullet_generation(
            workspace,
            job_description_id=job_description_id,
            snapshot_id=snapshot_id,
            branch_name=branch_name,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking_id_factory,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        error.run_ids = _committed_runs(workspace, allocated_runs)
        raise
