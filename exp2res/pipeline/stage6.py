"""§13.6 self-assessment synthesis for one assessment view."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence, cast

from pydantic import BaseModel, ValidationError

from exp2res.domain.calibration import claim_confidence_cap
from exp2res.domain.enums import AssessmentScope
from exp2res.domain.models import (
    AssessmentSnapshot,
    SelfClaim,
    SelfSignal,
    canonical_project_key,
)
from exp2res.domain.temporal import confidence_exceeds
from exp2res.errors import EmptyAssessmentViewError, IntegrityFailureError
from exp2res.exports.managed import assessment_set_paths, remove_assessment_sets
from exp2res.llm.assessment_writer import (
    ASSESSMENT_WRITER_CONTRACT,
    AssessmentWriterInput,
    AssessmentWriterOutput,
)
from exp2res.llm.contracts import (
    ContractValidationError,
    ContractWarning,
    validation_diagnostics,
)
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    insert_assessment_snapshot,
    insert_self_claim,
    list_assessment_snapshots,
    list_contradictions,
    list_gap_questions,
    list_self_claims_for_snapshot,
    mark_assessment_snapshots_superseded,
    mark_self_claims_superseded,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    report_managed_residuals,
    withdraw_managed_residuals,
    writer_database,
)

from .orchestration import PlannedCall, run_complete_stage
from .view_selection import select_assessment_view


@dataclass(frozen=True)
class ReplacedAssessmentView:
    scope: AssessmentScope
    scope_target: str | None
    snapshot_id: str


@dataclass(frozen=True)
class Stage6Result:
    run_id: str
    snapshot_id: str | None
    created_claim_ids: tuple[str, ...]
    superseded_snapshot_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    generation_id: str | None
    superseded_generation_ids: tuple[str, ...]
    replaced_view: ReplacedAssessmentView | None
    residual_paths: tuple[str, ...]
    warnings: tuple[ContractWarning, ...]
    snapshot: AssessmentSnapshot | None
    claims: tuple[SelfClaim, ...]


@dataclass(frozen=True)
class _ResolvedAssessment:
    snapshot: AssessmentSnapshot
    claims: tuple[SelfClaim, ...]
    warnings: tuple[ContractWarning, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def assessment_view_key(
    scope: AssessmentScope, scope_target: str | None
) -> tuple[AssessmentScope, str | None]:
    """Return the folded §11.7 replacement identity for a stored view."""

    return (
        scope,
        canonical_project_key(scope_target) if scope == "project" and scope_target is not None else None,
    )


def _enrich_for(
    input_payload: AssessmentWriterInput,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    signal_by_id = {signal.id: signal for signal in input_payload.signals}
    fact_by_id = {
        fact.id: fact for fact in (*input_payload.facts, *input_payload.context_facts)
    }

    def enrich(decoded: dict[str, Any]) -> dict[str, Any]:
        try:
            output = AssessmentWriterOutput.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
            )
        except ValidationError as error:
            raise ContractValidationError(
                validation_diagnostics(ASSESSMENT_WRITER_CONTRACT, error.errors())
            ) from None

        errors: list[dict[str, object]] = []
        narrative_count = sum(
            candidate.claim_kind == "narrative_summary"
            for candidate in output.self_claims
        )
        if narrative_count != 1:
            errors.append(
                {"loc": ("self_claims",), "type": "narrative_summary_count"}
            )
        for index, candidate in enumerate(output.self_claims):
            missing = False
            for member_index, signal_id in enumerate(candidate.source_signal_ids):
                if signal_id not in signal_by_id:
                    errors.append(
                        {
                            "loc": ("self_claims", index, "source_signal_ids", member_index),
                            "type": "out_of_context_target",
                        }
                    )
                    missing = True
            for member_index, fact_id in enumerate(candidate.source_fact_ids):
                if fact_id not in fact_by_id:
                    errors.append(
                        {
                            "loc": ("self_claims", index, "source_fact_ids", member_index),
                            "type": "out_of_context_target",
                        }
                    )
                    missing = True
            if missing:
                continue
            cap = claim_confidence_cap(
                source_confidences=(
                    *(
                        signal_by_id[item].confidence
                        for item in candidate.source_signal_ids
                    ),
                    *(fact_by_id[item].confidence for item in candidate.source_fact_ids),
                )
            )
            if confidence_exceeds(candidate.confidence, cap):
                errors.append(
                    {
                        "loc": ("self_claims", index, "confidence"),
                        "type": "confidence_above_cap",
                    }
                )
        if errors:
            raise ContractValidationError(
                validation_diagnostics(ASSESSMENT_WRITER_CONTRACT, errors)
            )
        return decoded

    return enrich


def _resolve_for(
    *,
    scope: AssessmentScope,
    scope_target: str | None,
    gaps: Sequence[object],
    contradictions: Sequence[object],
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> Callable[[BaseModel], object]:
    def resolve(validated: BaseModel) -> object:
        output = cast(AssessmentWriterOutput, validated)
        snapshot_id = id_factory("snapshot")
        narrative = next(
            item for item in output.self_claims if item.claim_kind == "narrative_summary"
        )
        snapshot = AssessmentSnapshot(
            id=snapshot_id,
            created_at=clock(),
            superseded_at=None,
            scope=scope,
            scope_target=scope_target,
            title=(
                "Self-Assessment — Global"
                if scope == "global"
                else f"Self-Assessment — {scope_target}"
            ),
            summary=narrative.claim,
            gap_question_ids=sorted((item.id for item in gaps), key=_id_key),  # type: ignore[attr-defined]
            contradiction_ids=sorted(
                (item.id for item in contradictions), key=_id_key  # type: ignore[attr-defined]
            ),
            verification_status="unverified",
            metadata={},
        )
        claims = tuple(
            SelfClaim(
                id=id_factory("claim"),
                created_at=clock(),
                superseded_at=None,
                snapshot_id=snapshot_id,
                claim=candidate.claim,
                claim_kind=candidate.claim_kind,
                dimension=candidate.dimension,
                source_signal_ids=sorted(candidate.source_signal_ids, key=_id_key),
                source_fact_ids=sorted(candidate.source_fact_ids, key=_id_key),
                confidence=candidate.confidence,
                verification_status="unverified",
                counterevidence=[],
                uncertainty=candidate.uncertainty,
                metadata={},
            )
            for candidate in output.self_claims
        )
        return _ResolvedAssessment(snapshot, claims, tuple(output.warnings))

    return resolve


def run_assessment_generation(
    workspace: Path,
    *,
    scope: AssessmentScope,
    scope_target: str | None,
    selection: LLMSelection,
    budgets: CallBudgets,
    runner: ContractRunner,
    id_factory: Callable[[str], str] = new_id,
    clock: Callable[[], datetime] | None = None,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    cli_version: str = "test-double",
    capability_check: Callable[[], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] | None = None,
    resolved_credentials: Iterable[bytes] = (),
) -> Stage6Result:
    """Run one complete writer call and atomically replace its assessment view."""

    now = clock or (lambda: datetime.now(timezone.utc))
    with writer_database(workspace, timeout_ms=timeout_ms, reconcile=True) as connection:
        view = select_assessment_view(
            connection, scope=scope, scope_target=scope_target
        )
        facts = view.facts
        signals = view.signals
        context_facts = view.context_facts

        gaps = tuple(
            sorted(
                (gap for gap in list_gap_questions(connection) if not gap.answered),
                key=lambda item: _id_key(item.id),
            )
        )
        contradictions = tuple(
            sorted(list_contradictions(connection), key=lambda item: _id_key(item.id))
        )

        # §13.6 defines the empty-subject failure for project views only. A global
        # view may legitimately mirror open gaps/contradictions with no facts or
        # signals yet; it fails only when the writer would receive zero supplied
        # objects of any kind and could therefore only fabricate.
        if scope == "project":
            if not facts and not signals:
                raise EmptyAssessmentViewError()
        elif not (facts or signals or gaps or contradictions):
            raise EmptyAssessmentViewError()
        input_payload = AssessmentWriterInput(
            scope=scope,
            scope_target=scope_target,
            signals=list(signals),
            facts=list(facts),
            context_facts=list(context_facts),
            gaps=list(gaps),
            contradictions=list(contradictions),
        )
        run_id = id_factory("run")
        planned = (
            PlannedCall(
                input_payload=input_payload,
                input_ids=tuple(
                    sorted(
                        {
                            *(item.id for item in signals),
                            *(item.id for item in facts),
                            *(item.id for item in context_facts),
                            *(item.id for item in gaps),
                            *(item.id for item in contradictions),
                        },
                        key=_id_key,
                    )
                ),
                enrich=_enrich_for(input_payload),
                resolve=_resolve_for(
                    scope=scope,
                    scope_target=scope_target,
                    gaps=gaps,
                    contradictions=contradictions,
                    id_factory=id_factory,
                    clock=now,
                ),
            ),
        )

        snapshot_id: str | None = None
        created_claim_ids: tuple[str, ...] = ()
        superseded_snapshot_ids: tuple[str, ...] = ()
        superseded_claim_ids: tuple[str, ...] = ()
        generation_id: str | None = None
        superseded_generation_ids: set[str] = set()
        replaced_view: ReplacedAssessmentView | None = None

        def commit(held: sqlite3.Connection, resolved: Sequence[object]) -> Iterable[str]:
            nonlocal snapshot_id, created_claim_ids, superseded_snapshot_ids
            nonlocal superseded_claim_ids, generation_id, replaced_view
            candidate = cast(_ResolvedAssessment, resolved[0])
            current = list_assessment_snapshots(held)
            matching = tuple(
                item
                for item in current
                if assessment_view_key(item.scope, item.scope_target)
                == assessment_view_key(scope, scope_target)
            )
            if len(matching) > 1:
                raise IntegrityFailureError("assessment_view_not_unique")
            swap_time = now()
            if matching:
                prior = matching[0]
                prior_claims = list_self_claims_for_snapshot(held, prior.id)
                superseded_snapshot_ids = (prior.id,)
                superseded_claim_ids = tuple(item.id for item in prior_claims)
                replaced_view = ReplacedAssessmentView(
                    prior.scope, prior.scope_target, prior.id
                )
                for table, ids in (
                    ("assessment_snapshots", superseded_snapshot_ids),
                    ("self_claims", superseded_claim_ids),
                ):
                    if ids:
                        placeholders = ",".join("?" for _ in ids)
                        superseded_generation_ids.update(
                            row[0]
                            for row in held.execute(
                                f"SELECT DISTINCT generation_id FROM {table} "
                                f"WHERE id IN ({placeholders})",
                                ids,
                            )
                        )
                mark_self_claims_superseded(held, superseded_claim_ids, swap_time)
                mark_assessment_snapshots_superseded(
                    held, superseded_snapshot_ids, swap_time
                )

            generation_id = id_factory("gen")
            insert_assessment_snapshot(
                held,
                candidate.snapshot,
                produced_by_run_id=run_id,
                generation_id=generation_id,
            )
            for claim in candidate.claims:
                if claim.snapshot_id != candidate.snapshot.id:
                    raise IntegrityFailureError("claim_snapshot_mismatch")
                insert_self_claim(
                    held,
                    claim,
                    produced_by_run_id=run_id,
                    generation_id=generation_id,
                )

            current_after = list_assessment_snapshots(held)
            keys = [assessment_view_key(item.scope, item.scope_target) for item in current_after]
            if len(keys) != len(set(keys)):
                raise IntegrityFailureError("assessment_view_not_unique")
            current_gap_ids = {
                row[0]
                for row in held.execute(
                    "SELECT id FROM gap_questions "
                    "WHERE superseded_at IS NULL AND answered = 0"
                )
            }
            current_contradiction_ids = {
                row[0]
                for row in held.execute(
                    "SELECT id FROM contradictions WHERE superseded_at IS NULL"
                )
            }
            if set(candidate.snapshot.gap_question_ids) != current_gap_ids:
                raise IntegrityFailureError("snapshot_gap_set_incomplete")
            if set(candidate.snapshot.contradiction_ids) != current_contradiction_ids:
                raise IntegrityFailureError("snapshot_contradiction_set_incomplete")
            members = list_self_claims_for_snapshot(held, candidate.snapshot.id)
            summaries = [item for item in members if item.claim_kind == "narrative_summary"]
            if len(summaries) != 1 or summaries[0].claim != candidate.snapshot.summary:
                raise IntegrityFailureError("snapshot_summary_mismatch")
            orphan = held.execute(
                """
                SELECT 1 FROM self_claims AS claim
                JOIN assessment_snapshots AS snapshot ON snapshot.id = claim.snapshot_id
                WHERE claim.superseded_at IS NULL AND snapshot.superseded_at IS NOT NULL
                LIMIT 1
                """
            ).fetchone()
            if orphan is not None:
                raise IntegrityFailureError("current_claim_superseded_snapshot")
            # §13.6: branch/bullet supersession joins this swap when those tables land.
            snapshot_id = candidate.snapshot.id
            created_claim_ids = tuple(sorted((item.id for item in candidate.claims), key=_id_key))
            # Pre-commit pending report: the paths this supersession makes
            # stale are reported before COMMIT, so an interrupt anywhere in
            # the commit-to-cleanup window still reports the retained set. A
            # completed removal clears the report through the existence
            # re-check; a rolled-back transaction withdraws it below.
            nonlocal pending_stale_paths
            pending_stale_paths = assessment_set_paths(
                workspace, superseded_snapshot_ids
            )
            report_managed_residuals(pending_stale_paths)
            return (snapshot_id, *created_claim_ids)

        pending_stale_paths: tuple[str, ...] = ()
        try:
            outcome = run_complete_stage(
            workspace,
            connection,
            stage="13.6",
            contract=ASSESSMENT_WRITER_CONTRACT,
            selection=selection,
            budgets=budgets,
            runner=runner,
            planned=planned,
            commit=commit,
            run_id=run_id,
            clock=now,
            cli_version=cli_version,
            capability_check=capability_check,
            monotonic=monotonic,
            sleeper=sleeper,
            jitter=jitter,
            token_patterns=token_patterns,
                resolved_credentials=resolved_credentials,
            )
        except BaseException:
            # The transaction did not commit (or the stage failed before its
            # cleanup); the pre-commit pending report must not survive as a
            # residual for sets that are still valid current output.
            withdraw_managed_residuals(pending_stale_paths)
            raise
        residual_paths = remove_assessment_sets(
            workspace, superseded_snapshot_ids
        )
        snapshot = (
            None
            if snapshot_id is None
            else next(item for item in list_assessment_snapshots(connection) if item.id == snapshot_id)
        )
        claims = (
            ()
            if snapshot_id is None
            else list_self_claims_for_snapshot(connection, snapshot_id)
        )

    resolved = cast(_ResolvedAssessment, outcome.resolved[0])
    return Stage6Result(
        run_id=run_id,
        snapshot_id=snapshot_id,
        created_claim_ids=created_claim_ids,
        superseded_snapshot_ids=tuple(sorted(superseded_snapshot_ids, key=_id_key)),
        superseded_claim_ids=tuple(sorted(superseded_claim_ids, key=_id_key)),
        generation_id=generation_id,
        superseded_generation_ids=tuple(sorted(superseded_generation_ids, key=_id_key)),
        replaced_view=replaced_view,
        residual_paths=residual_paths,
        warnings=resolved.warnings,
        snapshot=snapshot,
        claims=claims,
    )
