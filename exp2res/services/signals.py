"""Stage 5 execution and read-only self-signal inspection services."""

from __future__ import annotations

from pathlib import Path

from exp2res import __version__
from exp2res.domain.models import SelfSignal
from exp2res.errors import LLMInvocationError
from exp2res.pipeline.stage5 import Stage5Result, run_signal_generation
from exp2res.services.capture import new_id
from exp2res.services.extraction import build_llm_execution
from exp2res.storage.repository import list_self_signals
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


def run_signals_generate(workspace: Path) -> Stage5Result:
    """Resolve configured execution lazily and replace the complete Stage 5 set."""

    require_compatible(workspace)
    selection, budgets, runner = build_llm_execution(workspace)
    allocated_runs: list[str] = []

    def tracking_id_factory(kind: str) -> str:
        value = new_id(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    try:
        return run_signal_generation(
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


def list_current_signals(workspace: Path) -> tuple[SelfSignal, ...]:
    with read_database(workspace) as connection:
        rows = list_self_signals(connection)
    return tuple(sorted(rows, key=lambda item: _id_key(item.id)))
