"""Shared §13.13 Stage 3-5 lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
from typing import Callable

from exp2res import __version__
from exp2res.domain.results import AffectedIds, EntityIdGroup, InvalidatedView
from exp2res.errors import Exp2ResError, LLMCancelledError, LLMInvocationError
from exp2res.llm.contracts import ContractWarning
from exp2res.pipeline.stage3 import Stage3Result, run_fact_extraction
from exp2res.pipeline.stage4 import Stage4Result, run_detection_generation
from exp2res.pipeline.stage5 import Stage5Result, run_signal_generation
from exp2res.services.capture import new_id
from exp2res.services.extraction import build_llm_execution
from exp2res.storage.telemetry import create_processing_run, finish_processing_run
from exp2res.storage.workspace import require_compatible, writer_database


@dataclass(frozen=True)
class LifecycleResult:
    orchestration_run_id: str
    stage3: Stage3Result | None = None
    stage4: Stage4Result | None = None
    stage5: Stage5Result | None = None
    no_current_assessment_view: bool = False

    @property
    def run_ids(self) -> tuple[str, ...]:
        return (
            self.orchestration_run_id,
            *(result.run_id for result in (self.stage3, self.stage4, self.stage5) if result),
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
        if self.stage5:
            if self.stage5.generation_id is not None:
                values.add(self.stage5.generation_id)
            values.update(self.stage5.superseded_generation_ids)
        return tuple(sorted(values, key=lambda value: value.encode("utf-8")))

    @property
    def invalidated_views(self) -> tuple[InvalidatedView, ...]:
        by_id: dict[str, InvalidatedView] = {}
        for result in (self.stage3, self.stage4, self.stage5):
            if result:
                by_id.update((item.snapshot_id, item) for item in result.invalidated_views)
        return tuple(by_id[key] for key in sorted(by_id, key=lambda value: value.encode("utf-8")))

    @property
    def residual_paths(self) -> tuple[str, ...]:
        values = {
            path
            for result in (self.stage3, self.stage4, self.stage5)
            if result
            for path in result.residual_paths
        }
        # fsencode: filesystem-derived residual paths may carry
        # surrogateescape'd undecodable bytes that plain UTF-8 rejects.
        return tuple(sorted(values, key=os.fsencode))

    @property
    def warnings(self) -> tuple[ContractWarning, ...]:
        values = [
            warning
            for result in (self.stage3, self.stage4, self.stage5)
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
            add(superseded, "self_signal", self.stage3.superseded_signal_ids)
            add(superseded, "self_claim", self.stage3.superseded_claim_ids)
            add(superseded, "assessment_snapshot", self.stage3.superseded_snapshot_ids)
        if self.stage4:
            add(created, "gap_question", self.stage4.created_gap_ids)
            add(created, "contradiction", self.stage4.created_contradiction_ids)
            add(superseded, "gap_question", self.stage4.superseded_gap_ids)
            add(superseded, "contradiction", self.stage4.superseded_contradiction_ids)
            add(superseded, "self_signal", self.stage4.superseded_signal_ids)
            add(superseded, "self_claim", self.stage4.superseded_claim_ids)
            add(superseded, "assessment_snapshot", self.stage4.superseded_snapshot_ids)
        if self.stage5:
            add(created, "self_signal", self.stage5.created_signal_ids)
            add(superseded, "self_signal", self.stage5.superseded_signal_ids)
            add(superseded, "self_claim", self.stage5.superseded_claim_ids)
            add(superseded, "assessment_snapshot", self.stage5.superseded_snapshot_ids)

        def groups(values: dict[str, set[str]]) -> list[EntityIdGroup]:
            return [
                EntityIdGroup(
                    entity_type=entity_type,
                    ids=sorted(ids, key=lambda value: value.encode("utf-8")),
                )
                for entity_type, ids in sorted(values.items())
            ]

        return AffectedIds(created=groups(created), superseded=groups(superseded), deleted=[])


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


def _committed_runs(
    connection: sqlite3.Connection, run_ids: list[str]
) -> tuple[str, ...]:
    placeholders = ",".join("?" for _ in run_ids)
    rows = connection.execute(
        f"SELECT id FROM processing_runs WHERE id IN ({placeholders})", run_ids
    ).fetchall()
    committed = {row[0] for row in rows}
    return tuple(item for item in run_ids if item in committed)


def run_recompute(
    workspace: Path,
    *,
    log_id: str | None,
    id_factory: Callable[[str], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> LifecycleResult:
    """Replace selected/all lineages, then rebuild the global Stage 4-5 graph."""

    require_compatible(workspace)
    ids = id_factory or new_id
    now = clock or (lambda: datetime.now(timezone.utc))
    orchestration_run_id = ids("run")
    allocated_runs = [orchestration_run_id]

    stage3: Stage3Result | None = None
    stage4: Stage4Result | None = None
    stage5: Stage5Result | None = None

    def tracking_ids(kind: str) -> str:
        value = ids(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    # §8.1: the whole Stage 3-5 lifecycle runs under one held writer
    # authority — no other business writer can interleave between the
    # orchestration row, the stage swaps, and the terminal transition.
    with writer_database(workspace, reconcile=True) as connection:
        input_ids = tuple(
            row[0]
            for row in connection.execute(
                "SELECT id FROM raw_logs ORDER BY CAST(id AS BLOB)"
            )
        )
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
                input_ids=(input_ids if log_id is None else (log_id,)),
                metadata={
                    "mode": "full" if log_id is None else "selected_lineage"
                },
            ),
        )

        try:
            # §29.2 selection stays eagerly required exactly like a direct
            # `extract` (PR #125): a zero-lineage recompute still resolves the
            # configured adapter, while LazyPreflightRunner keeps it offline —
            # the stage runners plan zero calls and complete empty runs.
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
            stage5 = run_signal_generation(
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
            partial = LifecycleResult(orchestration_run_id, stage3, stage4, stage5)
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
            progress = LifecycleResult(orchestration_run_id, stage3, stage4, stage5)
            if isinstance(error, KeyboardInterrupt):
                # §14.14 rule 6: an interrupt between committed stage swaps
                # still reports every committed effect and run — a raw
                # KeyboardInterrupt would reach the CLI as an empty cancelled
                # envelope, so it leaves as the same class-9 §15.10 error the
                # in-stage path raises.
                cancelled = LLMCancelledError()
                try:
                    cancelled.run_ids = _committed_runs(connection, allocated_runs)
                except Exception:
                    cancelled.run_ids = ()
                cancelled.lifecycle_result = progress
                raise cancelled from error
            if isinstance(error, Exp2ResError):
                try:
                    error.run_ids = _committed_runs(connection, allocated_runs)
                except Exception:
                    error.run_ids = ()
                error.lifecycle_result = progress
            raise

        row = connection.execute(
            "SELECT 1 FROM assessment_snapshots WHERE superseded_at IS NULL LIMIT 1"
        ).fetchone()
        has_current_view = row is not None
    return LifecycleResult(
        orchestration_run_id,
        stage3,
        stage4,
        stage5,
        no_current_assessment_view=(
            not has_current_view and not partial.invalidated_views
        ),
    )
