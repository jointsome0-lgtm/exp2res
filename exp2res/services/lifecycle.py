"""Shared §13.13 Stage 3-4 lifecycle orchestration."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
from typing import Callable

from exp2res import __version__
from exp2res.domain.canonical import id_key
from exp2res.domain.results import (
    AffectedIds,
    InvalidatedBranch,
    InvalidatedView,
    Outcome,
    extend_committed,
    merged_invalidated_branches,
    merged_invalidated_views,
)
from exp2res.errors import Exp2ResError, LLMCancelledError, LLMInvocationError
from exp2res.llm.contracts import ContractWarning
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction
from exp2res.pipeline.stage4 import Stage4Result, run_detection_generation
from exp2res.services.capture import new_id
from exp2res.services.stages import build_llm_execution
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

    @property
    def run_ids(self) -> tuple[str, ...]:
        return (
            self.orchestration_run_id,
            *(result.run_id for result in (self.stage3, self.stage4) if result),
        )

    @property
    def generation_ids(self) -> tuple[str, ...]:
        values: set[str] = set()
        if self.stage3:
            values.update(self.stage3.generation_ids)
            values.update(self.stage3.superseded_generation_ids)
        if self.stage4:
            if self.stage4.generation_id is not None:
                values.add(self.stage4.generation_id)
            values.update(self.stage4.superseded_generation_ids)
        return tuple(sorted(values, key=id_key))

    @property
    def invalidated_views(self) -> tuple[InvalidatedView, ...]:
        by_id: dict[str, InvalidatedView] = {}
        for result in (self.stage3, self.stage4):
            if result:
                by_id.update((item.snapshot_id, item) for item in result.invalidated_views)
        return tuple(by_id[key] for key in sorted(by_id, key=id_key))

    @property
    def invalidated_branches(self) -> tuple[InvalidatedBranch, ...]:
        # §13.13 rule 9: one report per branch name across both stages.
        by_name: dict[str, InvalidatedBranch] = {}
        for result in (self.stage3, self.stage4):
            if result:
                by_name.update(
                    (item.name, item) for item in result.invalidated_branches
                )
        return tuple(
            by_name[key]
            for key in sorted(by_name, key=id_key)
        )

    @property
    def residual_paths(self) -> tuple[str, ...]:
        values = {
            path
            for result in (self.stage3, self.stage4)
            if result
            for path in result.residual_paths
        }
        # fsencode: residual paths may carry surrogateescape'd bytes.
        return tuple(sorted(values, key=os.fsencode))

    @property
    def warnings(self) -> tuple[ContractWarning, ...]:
        values = [
            warning
            for result in (self.stage3, self.stage4)
            if result
            for warning in result.warnings
        ]
        if self.no_current_assessment_view:
            values.append(
                ContractWarning(
                    type="assessment_view_regeneration_required",
                    message=(
                        "No current assessment view exists; run "
                        "exp2res assess generate after recompute."
                    ),
                )
            )
        return tuple(values)

    @property
    def affected_ids(self) -> AffectedIds:
        # §14.14 rule 5: overlapping classes from two stages, so order by name.
        created: dict[str, set[str]] = {}
        superseded: dict[str, set[str]] = {}

        def add(target: dict[str, set[str]], entity_type: str, ids: tuple[str, ...]) -> None:
            if ids:
                target.setdefault(entity_type, set()).update(ids)

        if self.stage3:
            add(created, "experience_fact", self.stage3.created)
            add(superseded, "experience_fact", self.stage3.superseded)
            add(superseded, "gap_question", self.stage3.superseded_gap_ids)
            add(superseded, "contradiction", self.stage3.superseded_contradiction_ids)
            add(superseded, "self_claim", self.stage3.superseded_claim_ids)
            add(superseded, "assessment_snapshot", self.stage3.superseded_snapshot_ids)
            add(superseded, "resume_branch", self.stage3.superseded_branch_ids)
            add(superseded, "resume_bullet", self.stage3.superseded_bullet_ids)
        if self.stage4:
            add(created, "gap_question", self.stage4.created_gap_ids)
            add(created, "contradiction", self.stage4.created_contradiction_ids)
            add(superseded, "gap_question", self.stage4.superseded_gap_ids)
            add(superseded, "contradiction", self.stage4.superseded_contradiction_ids)
            add(superseded, "self_claim", self.stage4.superseded_claim_ids)
            add(superseded, "assessment_snapshot", self.stage4.superseded_snapshot_ids)
            add(superseded, "resume_branch", self.stage4.superseded_branch_ids)
            add(superseded, "resume_bullet", self.stage4.superseded_bullet_ids)

        return AffectedIds.of(
            created=sorted(created.items()), superseded=sorted(superseded.items())
        )


def _held_transaction(
    connection: sqlite3.Connection, operation: Callable[[sqlite3.Connection], None]
) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        operation(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _has_current_assessment_view(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM assessment_snapshots WHERE superseded_at IS NULL LIMIT 1"
    ).fetchone()
    return row is not None


def _lifecycle_input_ids(
    connection: sqlite3.Connection, log_id: str | None
) -> tuple[str, ...]:
    if log_id is not None:
        return (log_id,)
    return tuple(
        row[0]
        for row in connection.execute(
            "SELECT id FROM raw_logs ORDER BY CAST(id AS BLOB)"
        )
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

    def record(held: sqlite3.Connection) -> None:
        create_processing_run(
            held,
            run_id=orchestration_run_id,
            stage="13.13",
            started_at=now(),
            provider=None,
            model=None,
            prompt_policy_hash=None,
            input_ids=_lifecycle_input_ids(held, log_id),
            metadata={"mode": "full" if log_id is None else "selected_lineage"},
        )
        finish_processing_run(
            held,
            run_id=orchestration_run_id,
            finished_at=now(),
            status="failed",
            failure_code="cancelled",
        )

    _held_transaction(connection, record)
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
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, reconcile=True)
    )
    with held as connection:
        try:
            # Inside the error boundary: an interrupt after this commit must
            # still terminally fail the durable `13.13` row.
            _held_transaction(
                connection,
                lambda held: create_processing_run(
                    held,
                    run_id=orchestration_run_id,
                    stage="13.13",
                    started_at=now(),
                    provider=None,
                    model=None,
                    prompt_policy_hash=None,
                    input_ids=_lifecycle_input_ids(held, log_id),
                    metadata={
                        "mode": "full" if log_id is None else "selected_lineage"
                    },
                ),
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
            _held_transaction(
                connection,
                lambda held: finish_processing_run(
                    held,
                    run_id=orchestration_run_id,
                    finished_at=now(),
                    status="completed",
                    output_ids=tuple(
                        entity_id
                        for group in partial.affected_ids.created
                        for entity_id in group.ids
                    ),
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
                _held_transaction(
                    connection,
                    lambda held: finish_processing_run(
                        held,
                        run_id=orchestration_run_id,
                        finished_at=now(),
                        status="failed",
                        failure_code=failure_code,
                    ),
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
                cancelled = LLMCancelledError()
                try:
                    extend_committed(
                        cancelled,
                        run_ids=list(
                            committed_runs(connection, allocated_runs)
                        ),
                    )
                except Exception:
                    extend_committed(cancelled, run_ids=[])
                cancelled.lifecycle_result = progress
                raise cancelled from error
            if isinstance(error, Exp2ResError):
                try:
                    extend_committed(
                        error,
                        run_ids=list(
                            committed_runs(connection, allocated_runs)
                        ),
                    )
                except Exception:
                    extend_committed(error, run_ids=[])
                error.lifecycle_result = progress
                raise
            if isinstance(error, Exception):
                # Secret-safe class-1 error that still carries the result.
                internal = Exp2ResError()
                try:
                    extend_committed(
                        internal,
                        run_ids=list(
                            committed_runs(connection, allocated_runs)
                        ),
                    )
                except Exception:
                    extend_committed(internal, run_ids=[])
                internal.lifecycle_result = progress
                raise internal from error
            raise
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
    invalidated_views = merged_invalidated_views(
        base_invalidated_views, recomputed.invalidated_views
    )
    invalidated_branches = merged_invalidated_branches(
        base_invalidated_branches, recomputed.invalidated_branches
    )
    no_view = (
        "\nNo current assessment view exists; run exp2res assess generate."
        if recomputed.no_current_assessment_view
        else ""
    )
    return Outcome(
        affected_ids=recomputed.affected_ids,
        generation_ids=list(recomputed.generation_ids),
        run_ids=list(recomputed.run_ids),
        invalidated_views=invalidated_views,
        invalidated_branches=invalidated_branches,
        residual_paths=list(recomputed.residual_paths),
        warnings=list(recomputed.warnings),
        human_result="Recomputed derived state through Stage 5." + no_view,
    )
