"""LLM-stage launch (Stages 3, 4, 6, 7, 10, 11) and their read-only inspection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable

from exp2res import __version__
from exp2res.config import call_budgets, load_workspace_config
from exp2res.domain.canonical import id_key
from exp2res.domain.models import (
    AssessmentSnapshot,
    Contradiction,
    ExperienceFact,
    GapQuestion,
    SelfClaim,
)
from exp2res.domain.results import AffectedIds, extend_committed
from exp2res.errors import (
    Exp2ResError,
    LLMCancelledError,
    LLMInvocationError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
)
from exp2res.llm.registry import LLMSelection, registration_for, resolve_selection
from exp2res.llm.runner import CallBudgets, ContractRunner, PreparedCall, RawResult
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction
from exp2res.pipeline.stage4 import Stage4Result, run_detection_generation
from exp2res.pipeline.stage6 import (
    Stage6Result,
    run_assessment_generation,
    run_assessment_repair,
)
from exp2res.pipeline.stage7 import Stage7Result, run_assessment_verification
from exp2res.pipeline.stage10 import (
    Stage10Result,
    run_bullet_generation,
    validated_branch_name,
)
from exp2res.pipeline.stage11 import Stage11Result, run_bullet_verification
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    current_branch_by_folded_name,
    get_assessment_snapshot,
    get_contradiction,
    get_experience_fact,
    get_gap_question,
    get_job_description,
    get_raw_log,
    list_assessment_snapshots,
    list_contradictions,
    list_experience_facts,
    list_gap_questions,
    list_self_claims_for_snapshot,
)
from exp2res.services.writers import transaction
from exp2res.storage.telemetry import (
    committed_runs,
    create_processing_run,
    finish_processing_run,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    read_database,
    require_compatible,
)


def _by_id(rows):
    return tuple(sorted(rows, key=lambda item: id_key(item.id)))


# --- shared launch pieces -------------------------------------------------


class LazyPreflightRunner:
    """Defer adapter preflight to the first request: a zero-call run never probes."""

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
        # §24.46: non-materializing, so preflight stays behind the telemetry rows.
        probe = getattr(self._runner, "runtime_version", None)
        return probe() if callable(probe) else None


def build_llm_execution(
    workspace: Path,
) -> tuple[LLMSelection, CallBudgets, ContractRunner]:
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


def failure_code_for(error: BaseException) -> str:
    """§12.13 `failure_code`: the one mapping from a raised error."""

    if isinstance(error, LLMInvocationError):
        return error.failure_code
    if isinstance(error, KeyboardInterrupt):
        return "cancelled"
    if isinstance(error, Exp2ResError):
        return error.diagnostic_class
    return "internal_error"


class RunIds:
    """§14.14 rule 5: the run IDs a command allocated, so a failure reports the committed ones."""

    def __init__(self, new_id: Callable[[str], str]) -> None:
        self.allocated: list[str] = []
        self._new_id = new_id

    def allocate(self, kind: str) -> str:
        value = self._new_id(kind)
        if kind == "run":
            self.allocated.append(value)
        return value

    def carry(
        self, connection: sqlite3.Connection, error: BaseException, *, created_as: str | None = None
    ) -> bool:
        """Extend `error` with the committed run IDs and, as `created_as`, what the
        completed ones created (§12 rule 11: `output_ids`, not allocated IDs)."""

        run_ids, created = committed_runs(connection, self.allocated)
        fields: dict[str, Any] = {"run_ids": list(run_ids)}
        if created_as and created:
            fields["affected_ids"] = AffectedIds.of(created=((created_as, created),))
        extend_committed(error, **fields)
        return bool(created)


class Run(RunIds):
    """One content-free §12.13 row: `create()`, then `finish()` completed with
    `outputs` (the created rows) or failed with `failure_code_for(error)`. As a
    context manager the exit does the terminal write, carries the committed run
    IDs onto an `Exp2ResError` and re-raises it — §14.14 rule 6: a
    `KeyboardInterrupt` as the class-9 `LLMCancelledError` (a raw one would reach
    the CLI as an empty envelope), another `Exception` as a secret-safe class-1
    error; `failure` keeps the original so a caller can fold its stage result in."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        stage: str,
        input_ids: Callable[[sqlite3.Connection], Iterable[str]],
        metadata: dict[str, str],
        clock: Callable[[], datetime],
        new_id: Callable[[str], str],
        own_transaction: bool = True,
    ) -> None:
        super().__init__(new_id)
        self.id = self.allocate("run")
        self.outputs: list[str] = []
        self.failure: BaseException | None = None
        self._connection = connection
        self._stage, self._input_ids, self._metadata = stage, input_ids, metadata
        self._clock = clock
        self._own_transaction = own_transaction

    def _write(self, work: Callable[[sqlite3.Connection], None]) -> None:
        if self._own_transaction:
            with transaction(self._connection) as held:
                work(held)
        else:
            work(self._connection)

    def create(self) -> None:
        # `input_ids` is read inside the row's own transaction (§8.1).
        self._write(
            lambda held: create_processing_run(
                held,
                run_id=self.id,
                stage=self._stage,
                started_at=self._clock(),
                provider=None,
                model=None,
                prompt_policy_hash=None,
                input_ids=self._input_ids(held),
                metadata=self._metadata,
            )
        )

    def finish(self, *, failure_code: str | None = None) -> None:
        self._write(
            lambda held: finish_processing_run(
                held,
                run_id=self.id,
                finished_at=self._clock(),
                status="failed" if failure_code else "completed",
                failure_code=failure_code,
                output_ids=() if failure_code else self.outputs,
            )
        )

    def __enter__(self) -> "Run":
        try:
            self.create()
        except BaseException as error:
            self._fail(error)
        return self

    def __exit__(self, _type, error: BaseException | None, _traceback) -> None:
        try:
            if error is None:
                # Inside the ladder: an interrupt at this commit must still report.
                self.finish()
                return
        except BaseException as late:
            error = late
        self._fail(error)

    def _fail(self, error: BaseException) -> None:
        self.failure = error
        try:
            self.finish(failure_code=failure_code_for(error))
        except Exception:
            pass
        if isinstance(error, KeyboardInterrupt):
            carrier: Exp2ResError = LLMCancelledError()
        elif isinstance(error, Exp2ResError):
            carrier = error
        elif isinstance(error, Exception):
            carrier = Exp2ResError()
        else:
            raise error
        try:
            self.carry(self._connection, carrier)
        except Exception:
            extend_committed(carrier, run_ids=[])
        if carrier is error:
            raise error
        raise carrier from error


def launch_stage(workspace: Path, stage: Callable[..., Any], **selectors: Any) -> Any:
    # §14.14 rule 4: callers check compatibility and selectors before this.
    selection, budgets, runner = build_llm_execution(workspace)
    ids = RunIds(new_id)
    try:
        return stage(
            workspace,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=ids.allocate,
            cli_version=__version__,
            **selectors,
        )
    except LLMInvocationError as error:
        with read_database(workspace) as connection:
            ids.carry(connection, error)
        raise


# --- Stage 3: extraction, §14.6 facts -------------------------------------


def validate_extract_selection(workspace: Path, *, log_id: str | None) -> None:
    # §14.14 rule 4: class 2 before any adapter work; re-checked under the lock.
    require_compatible(workspace)
    if log_id is None:
        return
    with read_database(workspace) as connection:
        if get_raw_log(connection, log_id) is None:
            raise SelectorNotFoundError()


def run_extract(workspace: Path, *, log_id: str | None) -> Stage3Result:
    require_compatible(workspace)
    return launch_stage(workspace, run_fact_extraction, log_id=log_id)


def list_facts(
    workspace: Path, *, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> tuple[ExperienceFact, ...]:
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        return list_experience_facts(connection)


def show_fact(
    workspace: Path,
    *,
    fact_id: str,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ExperienceFact:
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        fact = get_experience_fact(connection, fact_id)
        # §14.14 rule 7: current facts only; a superseded row is not addressable.
        if fact is None or fact.superseded_at is not None:
            raise SelectorNotFoundError()
        return fact


# --- Stage 4: detection ---------------------------------------------------


def run_detections_generate(workspace: Path) -> Stage4Result:
    require_compatible(workspace)
    return launch_stage(workspace, run_detection_generation)


def list_current_gaps(workspace: Path) -> tuple[GapQuestion, ...]:
    with read_database(workspace) as connection:
        return _by_id(list_gap_questions(connection))


def list_current_contradictions(workspace: Path) -> tuple[Contradiction, ...]:
    with read_database(workspace) as connection:
        return _by_id(list_contradictions(connection))


def show_contradiction(
    workspace: Path, *, contradiction_id: str
) -> Contradiction:
    # §14.14 rule 7: like `facts show`, a superseded row is selector_not_found.
    with read_database(workspace) as connection:
        contradiction = get_contradiction(
            connection, contradiction_id, current_only=True
        )
    if contradiction is None:
        raise SelectorNotFoundError()
    return contradiction


# --- Stages 6-7: assessment -----------------------------------------------


@dataclass(frozen=True)
class AssessmentDetails:
    snapshot: AssessmentSnapshot
    claims: tuple[SelfClaim, ...]
    gaps: tuple[GapQuestion, ...]
    contradictions: tuple[Contradiction, ...]


def run_assess_generate(workspace: Path) -> Stage6Result:
    # §14.9: one declared view, so generation takes no scope selector.
    require_compatible(workspace)
    return launch_stage(workspace, run_assessment_generation)


def run_assess_repair(workspace: Path, *, snapshot_id: str) -> Stage6Result:
    # §13.6 deterministic repair — no LLM execution is built.
    require_compatible(workspace)
    return run_assessment_repair(workspace, snapshot_id=snapshot_id)


def run_assess_verify(workspace: Path, *, snapshot_id: str) -> Stage7Result:
    require_compatible(workspace)
    return launch_stage(
        workspace, run_assessment_verification, snapshot_id=snapshot_id
    )


def list_current_snapshots(workspace: Path) -> tuple[AssessmentSnapshot, ...]:
    with read_database(workspace) as connection:
        return _by_id(list_assessment_snapshots(connection))


def show_snapshot(workspace: Path, *, snapshot_id: str) -> AssessmentDetails:
    with read_database(workspace) as connection:
        snapshot = get_assessment_snapshot(connection, snapshot_id)
        if snapshot is None:
            raise SelectorNotFoundError()
        claims = list_self_claims_for_snapshot(connection, snapshot.id)
        gaps: list[GapQuestion] = []
        for gap_id in snapshot.gap_question_ids:
            gap = get_gap_question(connection, gap_id, current_only=False)
            if gap is None:
                raise SelectorNotFoundError()
            gaps.append(gap)
        contradictions: list[Contradiction] = []
        for contradiction_id in snapshot.contradiction_ids:
            contradiction = get_contradiction(
                connection, contradiction_id, current_only=False
            )
            if contradiction is None:
                raise SelectorNotFoundError()
            contradictions.append(contradiction)
    return AssessmentDetails(
        snapshot=snapshot,
        claims=_by_id(claims),
        gaps=_by_id(gaps),
        contradictions=_by_id(contradictions),
    )


# --- Stages 10-11: §14.10 verified bullet pack ----------------------------


def validate_generate_selection(
    workspace: Path, *, job_description_id: str, snapshot_id: str
) -> None:
    # §14.14 rule 4: class 2 before any adapter work; re-checked under the lock.
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
    return launch_stage(
        workspace,
        run_bullet_generation,
        job_description_id=job_description_id,
        snapshot_id=snapshot_id,
        branch_name=branch_name,
    )


def run_bullets_verify(workspace: Path, *, branch_name: str) -> Stage11Result:
    require_compatible(workspace)
    # §14.14 rule 4: class 2 before any adapter work; re-checked under the lock.
    branch_name = validated_branch_name(branch_name)
    with read_database(workspace) as connection:
        if current_branch_by_folded_name(connection, branch_name) is None:
            raise SelectorNotFoundError()
    return launch_stage(workspace, run_bullet_verification, branch_name=branch_name)
