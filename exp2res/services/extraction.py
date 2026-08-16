"""Stage 3 execution wiring and the shared §14 stage-launch pieces."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from exp2res import __version__
from exp2res.config import call_budgets, load_workspace_config
from exp2res.domain.results import extend_committed
from exp2res.errors import LLMInvocationError, SelectorNotFoundError
from exp2res.llm.registry import LLMSelection, registration_for, resolve_selection
from exp2res.llm.runner import CallBudgets, ContractRunner, PreparedCall, RawResult
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction
from exp2res.services.capture import new_id
from exp2res.storage.repository import get_raw_log
from exp2res.storage.telemetry import committed_runs
from exp2res.storage.workspace import read_database, require_compatible


def validate_extract_selection(workspace: Path, *, log_id: str | None) -> None:
    """§14.14 rule 3: selector validity precedes adapter resolution/preflight.

    An unknown `--log-id` must fail as class-2 `selector_not_found` before
    any provider-side construction; the lineage planner re-checks under the
    writer lock, so a record deleted between this check and extraction still
    fails with the same class.
    """

    require_compatible(workspace)
    if log_id is None:
        return
    with read_database(workspace) as connection:
        if get_raw_log(connection, log_id) is None:
            raise SelectorNotFoundError()


class LazyPreflightRunner:
    """Defer adapter preflight to the first physical request.

    A zero-lineage extraction plans no calls and completes its empty run
    without any provider invocation, so probing Codex/bwrap/auth up front
    would fail a fresh workspace's `extract` as `capability_mismatch` for
    an adapter it never needs. Building on the first `run_contract` keeps
    the zero-call decision where it is made — under the writer lock after
    lineage planning — so no pre-check can race a concurrent capture.
    """

    def __init__(self, build: Callable[[], ContractRunner]) -> None:
        self._build = build
        self._runner: ContractRunner | None = None

    def materialize(self) -> ContractRunner:
        if self._runner is None:
            self._runner = self._build()
        return self._runner

    def run_contract(self, call: PreparedCall) -> RawResult:
        return self.materialize().run_contract(call)

    def runtime_version(self) -> str | None:
        """Non-materializing: report identity only once the build happened.

        Materializing here would move adapter preflight ahead of the
        run/call telemetry rows, making a missing session or absent
        sandbox uninspectable via `runs show` (§24.46, §14.14 rule 5).
        """

        probe = getattr(self._runner, "runtime_version", None)
        return probe() if callable(probe) else None


def build_llm_execution(
    workspace: Path,
) -> tuple[LLMSelection, CallBudgets, ContractRunner]:
    """Resolve config-owned bounds and defer runner preflight to first use."""

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

    def build_runner() -> ContractRunner:
        return registration.build_runner(
            config,
            Path(__file__).resolve().parents[2],
        )

    return selection, budgets, LazyPreflightRunner(build_runner)


class RunTracking:
    """Record the run IDs one command allocates, for its failure to report.

    §14.14 rule 5: `run_ids` reports the processing runs the command created,
    and a failed stage's durable telemetry row is exactly what `runs show`
    needs — so a raised §15 failure carries the committed run IDs out to the
    envelope instead of dropping them with the Outcome. Allocation happens
    inside the stage under its writer authority and the read back happens
    once that authority is gone, so only `committed_runs` can tell a durable
    row from an ID no stage ever wrote.

    `new_id` is a parameter rather than this module's global so that each
    command's own allocator — the one its tests replace — stays the one that
    runs.
    """

    def __init__(self, new_id: Callable[[str], str]) -> None:
        self.allocated_runs: list[str] = []
        self._new_id = new_id

    def allocate(self, kind: str) -> str:
        value = self._new_id(kind)
        if kind == "run":
            self.allocated_runs.append(value)
        return value

    def extend_committed_runs(
        self, workspace: Path, error: BaseException
    ) -> None:
        with read_database(workspace) as connection:
            extend_committed(
                error,
                run_ids=list(committed_runs(connection, self.allocated_runs)),
            )


def run_extract(workspace: Path, *, log_id: str | None) -> Stage3Result:
    require_compatible(workspace)
    selection, budgets, runner = build_llm_execution(workspace)
    tracking = RunTracking(new_id)
    try:
        return run_fact_extraction(
            workspace,
            log_id=log_id,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking.allocate,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        tracking.extend_committed_runs(workspace, error)
        raise
