"""Shared §13.13 Stage 3-4 lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
import sqlite3
from typing import Callable

from exp2res import __version__
from exp2res.domain.results import (
    InvalidatedBranch,
    InvalidatedView,
    Outcome,
    extend_committed,
    merge_outcomes,
)
from exp2res.errors import Exp2ResError, LLMCancelledError, LLMInvocationError
from exp2res.llm.contracts import ContractWarning
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction, stage3_outcome
from exp2res.pipeline.stage4 import (
    Stage4Result,
    detections_generate_outcome,
    run_detection_generation,
)
from exp2res.services.capture import new_id
from exp2res.services.privacy import table_ids
from exp2res.services.stages import build_llm_execution
from exp2res.services.writers import held_writer, transaction
from exp2res.storage.telemetry import (
    committed_runs,
    create_processing_run,
    finish_processing_run,
)
from exp2res.storage.workspace import require_compatible, writer_database


@dataclass(frozen=True)
class LifecycleResult:
    orchestration_run_id: str
    stage3: Stage3Result | None = None
    stage4: Stage4Result | None = None
    no_current_assessment_view: bool = False

    @cached_property
    def merged(self) -> Outcome:
        """The orchestration row plus both stage reports as one §14.14 rule 5 set."""

        parts = [Outcome(run_ids=[self.orchestration_run_id])]
        if self.stage3:
            parts.append(stage3_outcome(self.stage3))
        if self.stage4:
            parts.append(detections_generate_outcome(self.stage4))
        merged = merge_outcomes(*parts)
        if self.no_current_assessment_view:
            merged.warnings.append(
                ContractWarning(
                    type="assessment_view_regeneration_required",
                    message=(
                        "No current assessment view exists; run "
                        "exp2res assess generate after recompute."
                    ),
                )
            )
        return merged

    run_ids = property(lambda self: self.merged.run_ids)
    generation_ids = property(lambda self: self.merged.generation_ids)
    invalidated_views = property(lambda self: self.merged.invalidated_views)
    invalidated_branches = property(lambda self: self.merged.invalidated_branches)
    residual_paths = property(lambda self: self.merged.residual_paths)
    warnings = property(lambda self: self.merged.warnings)
    affected_ids = property(lambda self: self.merged.affected_ids)


def _has_current_assessment_view(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM assessment_snapshots WHERE superseded_at IS NULL LIMIT 1"
    ).fetchone()
    return row is not None


def create_orchestration_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    started_at: datetime,
    input_ids: tuple[str, ...],
    mode: str,
) -> None:
    """§24.47: the content-free `13.13` telemetry row."""

    create_processing_run(
        connection,
        run_id=run_id,
        stage="13.13",
        started_at=started_at,
        provider=None,
        model=None,
        prompt_policy_hash=None,
        input_ids=input_ids,
        metadata={"mode": mode},
    )


def _create_lifecycle_run(
    connection: sqlite3.Connection, *, run_id: str, started_at: datetime, log_id: str | None
) -> None:
    create_orchestration_run(
        connection,
        run_id=run_id,
        started_at=started_at,
        input_ids=(log_id,) if log_id is not None else table_ids(connection, "raw_logs"),
        mode="full" if log_id is None else "selected_lineage",
    )


def _fail_run(
    connection: sqlite3.Connection, *, run_id: str, finished_at: datetime, failure_code: str
) -> None:
    finish_processing_run(
        connection,
        run_id=run_id,
        finished_at=finished_at,
        status="failed",
        failure_code=failure_code,
    )


def record_cancelled_lifecycle(
    connection: sqlite3.Connection,
    *,
    log_id: str | None,
    id_factory: Callable[[str], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> LifecycleResult:
    ids = id_factory or new_id
    now = clock or (lambda: datetime.now(timezone.utc))
    orchestration_run_id = ids("run")

    with transaction(connection) as held:
        _create_lifecycle_run(
            held, run_id=orchestration_run_id, started_at=now(), log_id=log_id
        )
        _fail_run(
            held, run_id=orchestration_run_id, finished_at=now(), failure_code="cancelled"
        )
    return LifecycleResult(orchestration_run_id)


def run_recompute(
    workspace: Path,
    *,
    log_id: str | None,
    id_factory: Callable[[str], str] | None = None,
    clock: Callable[[], datetime] | None = None,
    connection: sqlite3.Connection | None = None,
) -> LifecycleResult:
    require_compatible(workspace)
    ids = id_factory or new_id
    now = clock or (lambda: datetime.now(timezone.utc))
    orchestration_run_id = ids("run")
    allocated_runs = [orchestration_run_id]

    stage3: Stage3Result | None = None
    stage4: Stage4Result | None = None

    def tracking_ids(kind: str) -> str:
        value = ids(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    # §8.1: one writer authority spans the orchestration row, the stage swaps
    # and the terminal transition; correction/deletion pass the one they hold.
    with held_writer(
        connection, writer_database, workspace, reconcile=True
    ) as connection:
        try:
            # Inside the error boundary: an interrupt after this commit must
            # still terminally fail the durable `13.13` row.
            with transaction(connection) as held:
                _create_lifecycle_run(
                    held, run_id=orchestration_run_id, started_at=now(), log_id=log_id
                )
            # §29.2: selection is eager even for a zero-lineage recompute;
            # LazyPreflightRunner keeps it offline.
            selection, budgets, runner = build_llm_execution(workspace)
            stage3 = run_fact_extraction(
                workspace,
                log_id=log_id,
                selection=selection,
                budgets=budgets,
                runner=runner,
                id_factory=tracking_ids,
                parent_run_id=orchestration_run_id,
                connection=connection,
                clock=now,
                cli_version=__version__,
            )
            stage4 = run_detection_generation(
                workspace,
                selection=selection,
                budgets=budgets,
                runner=runner,
                id_factory=tracking_ids,
                parent_run_id=orchestration_run_id,
                connection=connection,
                clock=now,
                cli_version=__version__,
            )
            partial = LifecycleResult(orchestration_run_id, stage3, stage4)
            # Inside the boundary: a late failure must still carry Stage 3-4.
            has_current_view = _has_current_assessment_view(connection)
            with transaction(connection) as held:
                finish_processing_run(
                    held,
                    run_id=orchestration_run_id,
                    finished_at=now(),
                    status="completed",
                    output_ids=tuple(
                        entity_id
                        for group in partial.affected_ids.created
                        for entity_id in group.ids
                    ),
                )
        except BaseException as error:
            failure_code = (
                error.failure_code
                if isinstance(error, LLMInvocationError)
                else "cancelled"
                if isinstance(error, KeyboardInterrupt)
                else error.diagnostic_class
                if isinstance(error, Exp2ResError)
                else "internal_error"
            )
            try:
                with transaction(connection) as held:
                    _fail_run(
                        held,
                        run_id=orchestration_run_id,
                        finished_at=now(),
                        failure_code=failure_code,
                    )
            except Exception:
                pass
            # §14.14 rule 6: a stage interrupted after its committed swap
            # carries its result on the error; fold it in.
            carried = getattr(error, "stage_result", None)
            if isinstance(carried, Stage3Result) and stage3 is None:
                stage3 = carried
            elif isinstance(carried, Stage4Result) and stage4 is None:
                stage4 = carried
            progress = LifecycleResult(orchestration_run_id, stage3, stage4)
            if isinstance(error, KeyboardInterrupt):
                # §14.14 rule 6: a raw KeyboardInterrupt would reach the CLI as
                # an empty envelope, so leave as the in-stage class-9 error.
                carrier: Exp2ResError = LLMCancelledError()
            elif isinstance(error, Exp2ResError):
                carrier = error
            elif isinstance(error, Exception):
                # Secret-safe class-1 error that still carries the result.
                carrier = Exp2ResError()
            else:
                raise
            try:
                extend_committed(
                    carrier,
                    run_ids=list(committed_runs(connection, allocated_runs)),
                )
            except Exception:
                extend_committed(carrier, run_ids=[])
            carrier.lifecycle_result = progress
            if carrier is error:
                raise
            raise carrier from error
    return LifecycleResult(
        orchestration_run_id,
        stage3,
        stage4,
        no_current_assessment_view=(
            not has_current_view and not partial.invalidated_views
        ),
    )


def lifecycle_outcome(
    recomputed: LifecycleResult,
    *,
    base_invalidated_views: tuple[InvalidatedView, ...] = (),
    base_invalidated_branches: tuple[InvalidatedBranch, ...] = (),
) -> Outcome:
    no_view = (
        "\nNo current assessment view exists; run exp2res assess generate."
        if recomputed.no_current_assessment_view
        else ""
    )
    return merge_outcomes(
        replace(
            recomputed.merged,
            human_result="Recomputed derived state through Stage 5." + no_view,
        ),
        Outcome(
            invalidated_views=list(base_invalidated_views),
            invalidated_branches=list(base_invalidated_branches),
        ),
    )
