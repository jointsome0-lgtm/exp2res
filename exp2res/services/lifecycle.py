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
    merge_outcomes,
)
from exp2res.errors import Exp2ResError
from exp2res.llm.contracts import ContractWarning
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction, stage3_outcome
from exp2res.pipeline.stage4 import (
    Stage4Result,
    detections_generate_outcome,
    run_detection_generation,
)
from exp2res.services import stages
from exp2res.services.capture import new_id
from exp2res.services.privacy import table_ids
from exp2res.services.stages import Run
from exp2res.services.writers import held_writer, transaction
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


def _lifecycle_run(
    connection: sqlite3.Connection,
    *,
    log_id: str | None,
    id_factory: Callable[[str], str] | None,
    clock: Callable[[], datetime] | None,
    own_transaction: bool = True,
) -> Run:
    """§24.47: the content-free `13.13` telemetry row."""

    return Run(
        connection,
        own_transaction=own_transaction,
        stage="13.13",
        input_ids=lambda held: (
            (log_id,) if log_id is not None else table_ids(held, "raw_logs")
        ),
        metadata={"mode": "full" if log_id is None else "selected_lineage"},
        clock=clock or (lambda: datetime.now(timezone.utc)),
        new_id=id_factory or new_id,
    )


def record_cancelled_lifecycle(
    connection: sqlite3.Connection,
    *,
    log_id: str | None,
    id_factory: Callable[[str], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> LifecycleResult:
    with transaction(connection) as held:
        run = _lifecycle_run(
            held, log_id=log_id, id_factory=id_factory, clock=clock, own_transaction=False
        )
        run.create()
        run.finish(failure_code="cancelled")
    return LifecycleResult(run.id)


def run_recompute(
    workspace: Path,
    *,
    log_id: str | None,
    id_factory: Callable[[str], str] | None = None,
    clock: Callable[[], datetime] | None = None,
    connection: sqlite3.Connection | None = None,
) -> LifecycleResult:
    require_compatible(workspace)
    now = clock or (lambda: datetime.now(timezone.utc))
    stage3: Stage3Result | None = None
    stage4: Stage4Result | None = None

    # §8.1: one writer authority spans the orchestration row, the stage swaps
    # and the terminal transition; correction/deletion pass the one they hold.
    with held_writer(
        connection, writer_database, workspace, reconcile=True
    ) as connection:
        run = _lifecycle_run(connection, log_id=log_id, id_factory=id_factory, clock=now)
        try:
            # Inside the error boundary: an interrupt after the row's commit
            # must still terminally fail the durable `13.13` row.
            with run:
                # §29.2: selection is eager even for a zero-lineage recompute;
                # LazyPreflightRunner keeps it offline.
                selection, budgets, runner = stages.build_llm_execution(workspace)
                stage3 = run_fact_extraction(
                    workspace,
                    log_id=log_id,
                    selection=selection,
                    budgets=budgets,
                    runner=runner,
                    id_factory=run.allocate,
                    parent_run_id=run.id,
                    connection=connection,
                    clock=now,
                    cli_version=__version__,
                )
                stage4 = run_detection_generation(
                    workspace,
                    selection=selection,
                    budgets=budgets,
                    runner=runner,
                    id_factory=run.allocate,
                    parent_run_id=run.id,
                    connection=connection,
                    clock=now,
                    cli_version=__version__,
                )
                partial = LifecycleResult(run.id, stage3, stage4)
                # Inside the boundary: a late failure must still carry Stage 3-4.
                has_current_view = _has_current_assessment_view(connection)
                run.outputs.extend(
                    entity_id
                    for group in partial.affected_ids.created
                    for entity_id in group.ids
                )
        except Exp2ResError as carrier:
            # §14.14 rule 6: a stage interrupted after its committed swap
            # carries its result on the error; fold it in.
            carried = getattr(run.failure, "stage_result", None)
            if isinstance(carried, Stage3Result) and stage3 is None:
                stage3 = carried
            elif isinstance(carried, Stage4Result) and stage4 is None:
                stage4 = carried
            carrier.lifecycle_result = LifecycleResult(run.id, stage3, stage4)
            raise
    return LifecycleResult(
        run.id,
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
