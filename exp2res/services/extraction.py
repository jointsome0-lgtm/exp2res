"""Stage 3 execution wiring for the §14.6 extraction command."""

from __future__ import annotations

from pathlib import Path

from exp2res import __version__
from exp2res.config import call_budgets, load_workspace_config, resolve_codex_home
from exp2res.llm.adapter import preflight_adapter
from exp2res.llm.registry import LLMSelection, registration_for, resolve_selection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction
from exp2res.storage.workspace import require_compatible


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


def run_extract(workspace: Path, *, log_id: str | None) -> Stage3Result:
    require_compatible(workspace)
    selection, budgets, runner = build_llm_execution(workspace)
    return run_fact_extraction(
        workspace,
        log_id=log_id,
        selection=selection,
        budgets=budgets,
        runner=runner,
        cli_version=__version__,
    )
