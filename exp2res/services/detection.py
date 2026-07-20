"""Stage 4 execution and read-only detection inspection services."""

from __future__ import annotations

from pathlib import Path

from exp2res import __version__
from exp2res.domain.models import Contradiction, GapQuestion
from exp2res.errors import LLMInvocationError, SelectorNotFoundError
from exp2res.pipeline.stage4 import Stage4Result, run_detection_generation
from exp2res.services.capture import new_id
from exp2res.services.extraction import LazyPreflightRunner, build_llm_execution
from exp2res.storage.repository import (
    get_contradiction,
    list_contradictions,
    list_gap_questions,
)
from exp2res.storage.workspace import read_database, require_compatible


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _committed_runs(workspace: Path, run_ids: list[str]) -> tuple[str, ...]:
    if not run_ids:
        return ()
    placeholders = ",".join("?" for _ in run_ids)
    with read_database(workspace) as connection:
        rows = connection.execute(
            f"SELECT id FROM processing_runs WHERE id IN ({placeholders})",
            run_ids,
        ).fetchall()
    committed = {row[0] for row in rows}
    return tuple(run_id for run_id in run_ids if run_id in committed)


def run_detections_generate(workspace: Path) -> Stage4Result:
    """Resolve configured execution lazily and run the complete Stage 4 set."""

    require_compatible(workspace)
    # build_llm_execution returns the shared LazyPreflightRunner: resolving
    # selection and budgets is eager, adapter/provider probing is first-call.
    selection, budgets, runner = build_llm_execution(workspace)
    allocated_runs: list[str] = []

    def tracking_id_factory(kind: str) -> str:
        value = new_id(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    try:
        return run_detection_generation(
            workspace,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking_id_factory,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        error.run_ids = _committed_runs(workspace, allocated_runs)
        raise


def list_current_gaps(workspace: Path) -> tuple[GapQuestion, ...]:
    with read_database(workspace) as connection:
        rows = list_gap_questions(connection)
    return tuple(sorted(rows, key=lambda item: _id_key(item.id)))


def list_current_contradictions(workspace: Path) -> tuple[Contradiction, ...]:
    with read_database(workspace) as connection:
        rows = list_contradictions(connection)
    return tuple(sorted(rows, key=lambda item: _id_key(item.id)))


def show_contradiction(
    workspace: Path, *, contradiction_id: str
) -> Contradiction:
    with read_database(workspace) as connection:
        contradiction = get_contradiction(
            connection, contradiction_id, current_only=False
        )
    if contradiction is None:
        raise SelectorNotFoundError()
    return contradiction
