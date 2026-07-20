"""Stage 6 execution and current assessment inspection services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from exp2res import __version__
from exp2res.domain.enums import AssessmentScope
from exp2res.domain.models import (
    AssessmentSnapshot,
    Contradiction,
    GapQuestion,
    SelfClaim,
    canonical_project_key,
)
from exp2res.errors import InvalidUsageError, LLMInvocationError, SelectorNotFoundError
from exp2res.pipeline.stage6 import Stage6Result, run_assessment_generation
from exp2res.services.capture import new_id
from exp2res.services.extraction import build_llm_execution
from exp2res.storage.repository import (
    get_assessment_snapshot,
    get_contradiction,
    get_gap_question,
    list_assessment_snapshots,
    list_self_claims_for_snapshot,
)
from exp2res.storage.workspace import read_database, require_compatible


@dataclass(frozen=True)
class AssessmentDetails:
    snapshot: AssessmentSnapshot
    claims: tuple[SelfClaim, ...]
    gaps: tuple[GapQuestion, ...]
    contradictions: tuple[Contradiction, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _committed_runs(workspace: Path, run_ids: list[str]) -> tuple[str, ...]:
    if not run_ids:
        return ()
    placeholders = ",".join("?" for _ in run_ids)
    with read_database(workspace) as connection:
        rows = connection.execute(
            f"SELECT id FROM processing_runs WHERE id IN ({placeholders})", run_ids
        ).fetchall()
    committed = {row[0] for row in rows}
    return tuple(run_id for run_id in run_ids if run_id in committed)


def validate_assessment_selection(
    *, scope: str, project: str | None
) -> tuple[AssessmentScope, str | None]:
    if scope not in {"global", "project"}:
        raise InvalidUsageError()
    if scope == "global":
        if project is not None:
            raise InvalidUsageError()
        return "global", None
    if project is None or not canonical_project_key(project):
        raise InvalidUsageError()
    return "project", project


def run_assess_generate(
    workspace: Path, *, scope: str, project: str | None
) -> Stage6Result:
    selected_scope, selected_project = validate_assessment_selection(
        scope=scope, project=project
    )
    require_compatible(workspace)
    selection, budgets, runner = build_llm_execution(workspace)
    allocated_runs: list[str] = []

    def tracking_id_factory(kind: str) -> str:
        value = new_id(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    try:
        return run_assessment_generation(
            workspace,
            scope=selected_scope,
            scope_target=selected_project,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking_id_factory,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        error.run_ids = _committed_runs(workspace, allocated_runs)
        raise


def list_current_snapshots(workspace: Path) -> tuple[AssessmentSnapshot, ...]:
    with read_database(workspace) as connection:
        snapshots = list_assessment_snapshots(connection)
    return tuple(sorted(snapshots, key=lambda item: _id_key(item.id)))


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
        claims=tuple(sorted(claims, key=lambda item: _id_key(item.id))),
        gaps=tuple(sorted(gaps, key=lambda item: _id_key(item.id))),
        contradictions=tuple(
            sorted(contradictions, key=lambda item: _id_key(item.id))
        ),
    )
