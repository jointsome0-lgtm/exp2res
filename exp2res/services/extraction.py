"""Stage 3 execution wiring for the §14.6 extraction command."""

from __future__ import annotations

from pathlib import Path

from exp2res import __version__
from exp2res.config import call_budgets, load_workspace_config, resolve_codex_home
from exp2res.errors import LLMInvocationError, SelectorNotFoundError
from exp2res.llm.adapter import preflight_adapter
from exp2res.llm.registry import LLMSelection, registration_for, resolve_selection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction
from exp2res.services.capture import new_id
from exp2res.storage.repository import get_raw_log
from exp2res.storage.workspace import read_database, require_compatible


def validate_extract_selection(workspace: Path, *, log_id: str | None) -> None:
    """§14.14 rule 3: selector validity precedes consent and adapter preflight.

    An unknown `--log-id` must fail as class-2 `selector_not_found` before
    the cost-consent prompt and before any provider-side construction; the
    lineage planner re-checks under the writer lock, so a record deleted
    between this check and extraction still fails with the same class.
    """

    require_compatible(workspace)
    if log_id is None:
        return
    with read_database(workspace) as connection:
        if get_raw_log(connection, log_id) is None:
            raise SelectorNotFoundError()


def build_llm_execution(
    workspace: Path,
) -> tuple[LLMSelection, CallBudgets, ContractRunner]:
    """Resolve config-owned bounds and construct this build's registered runner."""

    config = load_workspace_config(workspace).llm
    selected = config.selection
    selection = resolve_selection(selected.adapter, selected.model)
    budgets = call_budgets(
        config,
        planned_output_tokens=config.output_token_budget,
        planned_call_count=1,
        model_context_tokens=config.input_token_budget + config.output_token_budget,
        model_max_output_tokens=config.output_token_budget,
    )
    registration = registration_for(selection)
    runtime = preflight_adapter(
        repository_root=Path(__file__).resolve().parents[2],
        codex_home=resolve_codex_home(config),
        declaration=registration.declaration,
    )
    runner = registration.runner_type(
        codex_binary=runtime.codex_binary,
        bwrap_binary=runtime.bwrap_binary,
        codex_home=runtime.codex_home,
    )
    return selection, budgets, runner


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


def run_extract(workspace: Path, *, log_id: str | None) -> Stage3Result:
    require_compatible(workspace)
    selection, budgets, runner = build_llm_execution(workspace)
    # §14.14 rule 5: `run_ids` reports the processing runs the command
    # created, and a failed extraction's durable telemetry row is exactly
    # what `runs show` needs — so a raised §15 failure carries the committed
    # run IDs out to the envelope instead of dropping them with the Outcome.
    allocated_runs: list[str] = []

    def tracking_id_factory(kind: str) -> str:
        value = new_id(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    try:
        return run_fact_extraction(
            workspace,
            log_id=log_id,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking_id_factory,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        error.run_ids = _committed_runs(workspace, allocated_runs)
        raise
