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

from exp2res.domain.calibration import (
    claim_confidence_cap,
    pattern_generalization_cap,
)
from exp2res.domain.enums import AssessmentScope
from exp2res.domain.results import (
    AffectedIds,
    EntityIdGroup,
    InvalidatedBranch,
    Outcome,
    extend_committed,
)
from exp2res.domain.models import (
    AssessmentSnapshot,
    ExperienceFact,
    SelfClaim,
)
from exp2res.domain.temporal import confidence_exceeds
from exp2res.domain.verification import aggregate_verification_status
from exp2res.errors import (
    EmptyAssessmentViewError,
    Exp2ResError,
    IntegrityFailureError,
    NothingToRepairError,
    OperationCancelledError,
    RewriteUnavailableError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
    SnapshotNotVerifiedError,
)
from exp2res.exports.managed import (
    assessment_set_paths,
    branch_set_paths,
    remove_assessment_sets,
    remove_branch_sets,
)
from exp2res.llm.assessment_writer import (
    ASSESSMENT_WRITER_CONTRACT,
    AssessmentWriterInput,
    AssessmentWriterOutput,
    PatternClaimCandidate,
    ScratchPattern,
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
    get_assessment_snapshot,
    insert_assessment_snapshot,
    insert_self_claim,
    list_assessment_snapshots,
    list_contradictions,
    list_gap_questions,
    list_self_claims_for_snapshot,
    list_verification_findings,
    mark_assessment_snapshots_superseded,
    mark_self_claims_superseded,
)
from exp2res.storage.telemetry import create_processing_run, finish_processing_run
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    report_managed_residuals,
    writer_database,
)

from .branch_lifecycle import BranchSupersession, supersede_dependent_branches
from .orchestration import (
    PlannedCall,
    run_complete_stage,
    unfinished_stale_paths,
    withdraw_pending_unless_superseded,
)
from .view_selection import select_assessment_view


@dataclass(frozen=True)
class ReplacedAssessmentView:
    scope: AssessmentScope
    snapshot_id: str


@dataclass(frozen=True)
class Stage6Result:
    run_id: str
    snapshot_id: str | None
    created_claim_ids: tuple[str, ...]
    superseded_snapshot_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    superseded_branch_ids: tuple[str, ...]
    superseded_bullet_ids: tuple[str, ...]
    generation_id: str | None
    superseded_generation_ids: tuple[str, ...]
    replaced_view: ReplacedAssessmentView | None
    invalidated_branches: tuple[InvalidatedBranch, ...]
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


def claim_counter_fact_ids(
    candidate: object, patterns: Sequence[ScratchPattern]
) -> list[str]:
    """Derive §11.6's contrary-role marking from the cited §15.4 patterns.

    The duplicate-free union of the cited patterns' counter facts, empty for a
    claim citing no patterns. §15.4's equality rule makes it a subset of the
    candidate's `source_fact_ids`, so the split stays re-checkable after the
    patterns are discarded at this boundary.
    """

    if not isinstance(candidate, PatternClaimCandidate):
        return []
    by_label = {item.label: item for item in patterns}
    cited = frozenset().union(
        *(
            frozenset(by_label[label].counter_fact_ids)
            for label in candidate.source_pattern_labels
        )
    )
    return sorted(cited, key=_id_key)


def _pattern_cap(
    candidate: PatternClaimCandidate,
    counter_fact_ids: Sequence[str],
    fact_by_id: dict[str, ExperienceFact],
) -> str:
    """Compute §9.4's pattern-generalization cap from the persisted split."""

    supporting = [
        fact_by_id[fact_id]
        for fact_id in candidate.source_fact_ids
        if fact_id not in set(counter_fact_ids)
    ]
    return pattern_generalization_cap(
        supporting_confidences=(fact.confidence for fact in supporting),
        distinct_source_log_count=len(
            {log_id for fact in supporting for log_id in fact.source_log_ids}
        ),
        has_counter_facts=bool(counter_fact_ids),
    )


def _enrich_for(
    input_payload: AssessmentWriterInput,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    fact_by_id = {fact.id: fact for fact in input_payload.facts}

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
        for index, pattern in enumerate(output.patterns):
            for field in ("supporting_fact_ids", "counter_fact_ids"):
                for member_index, fact_id in enumerate(getattr(pattern, field)):
                    if fact_id not in fact_by_id:
                        errors.append(
                            {
                                "loc": ("patterns", index, field, member_index),
                                "type": "out_of_context_target",
                            }
                        )
        for index, candidate in enumerate(output.self_claims):
            missing = False
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
                    fact_by_id[item].confidence for item in candidate.source_fact_ids
                )
            )
            if isinstance(candidate, PatternClaimCandidate):
                # The §9.4 pattern-generalization cap is never weaker than the
                # source maximum, but taking the stricter of the two keeps the
                # bound total whatever the split.
                pattern_cap = _pattern_cap(
                    candidate,
                    claim_counter_fact_ids(candidate, output.patterns),
                    fact_by_id,
                )
                if confidence_exceeds(cap, pattern_cap):
                    cap = pattern_cap
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
            scope="global",
            title="Self-Assessment — Global",
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
                source_fact_ids=sorted(candidate.source_fact_ids, key=_id_key),
                counter_fact_ids=claim_counter_fact_ids(candidate, output.patterns),
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
        facts = select_assessment_view(connection)

        gaps = tuple(
            sorted(
                (gap for gap in list_gap_questions(connection) if not gap.answered),
                key=lambda item: _id_key(item.id),
            )
        )
        contradictions = tuple(
            sorted(list_contradictions(connection), key=lambda item: _id_key(item.id))
        )

        # §15.4 rejects an empty `source_fact_ids` on every claim, so a
        # factless view cannot produce even the required narrative summary. It
        # fails here rather than after a provider call that can only be invalid.
        if not facts:
            raise EmptyAssessmentViewError()
        input_payload = AssessmentWriterInput(
            scope="global",
            facts=list(facts),
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
                            *(item.id for item in facts),
                            *(item.id for item in gaps),
                            *(item.id for item in contradictions),
                        },
                        key=_id_key,
                    )
                ),
                enrich=_enrich_for(input_payload),
                resolve=_resolve_for(
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
        branch_swap = BranchSupersession()

        def commit(held: sqlite3.Connection, resolved: Sequence[object]) -> Iterable[str]:
            nonlocal snapshot_id, created_claim_ids, superseded_snapshot_ids
            nonlocal superseded_claim_ids, generation_id, replaced_view, branch_swap
            candidate = cast(_ResolvedAssessment, resolved[0])
            # §11.7: one declared view, so at most one snapshot is current
            # and it is unconditionally the one this swap replaces.
            current = list_assessment_snapshots(held)
            if len(current) > 1:
                raise IntegrityFailureError("assessment_view_not_unique")
            swap_time = now()
            if current:
                prior = current[0]
                prior_claims = list_self_claims_for_snapshot(held, prior.id)
                superseded_snapshot_ids = (prior.id,)
                superseded_claim_ids = tuple(item.id for item in prior_claims)
                replaced_view = ReplacedAssessmentView(prior.scope, prior.id)
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
                # §13.10: a replacement assessment generation supersedes every
                # branch anchored to the view it replaces, before that anchor
                # stops being current.
                branch_swap = supersede_dependent_branches(
                    held, superseded_snapshot_ids, superseded_at=swap_time
                )
                superseded_generation_ids.update(
                    branch_swap.superseded_generation_ids
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

            if len(list_assessment_snapshots(held)) > 1:
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
            snapshot_id = candidate.snapshot.id
            created_claim_ids = tuple(sorted((item.id for item in candidate.claims), key=_id_key))
            # Pre-commit pending report: the paths this supersession makes
            # stale are reported before COMMIT, so an interrupt anywhere in
            # the commit-to-cleanup window still reports the retained set. A
            # completed removal clears the report through the existence
            # re-check; a rolled-back transaction withdraws it below.
            nonlocal pending_stale_paths
            pending_stale_paths = (
                *assessment_set_paths(workspace, superseded_snapshot_ids),
                *branch_set_paths(workspace, branch_swap.branch_ids),
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
            # Withdraw the pre-commit pending report only when the rollback
            # is proven; an interrupt after a durable commit keeps the
            # stale-set report in the cancelled envelope.
            withdraw_pending_unless_superseded(
                connection, pending_stale_paths, superseded_snapshot_ids
            )
            raise
        # Read the committed rows before the interruptible cleanup below, so
        # the guard has a complete result to carry.
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

        def build_result(residuals: tuple[str, ...]) -> Stage6Result:
            return Stage6Result(
                run_id=run_id,
                snapshot_id=snapshot_id,
                created_claim_ids=created_claim_ids,
                superseded_snapshot_ids=tuple(
                    sorted(superseded_snapshot_ids, key=_id_key)
                ),
                superseded_claim_ids=tuple(sorted(superseded_claim_ids, key=_id_key)),
                superseded_branch_ids=branch_swap.branch_ids,
                superseded_bullet_ids=branch_swap.bullet_ids,
                generation_id=generation_id,
                superseded_generation_ids=tuple(
                    sorted(superseded_generation_ids, key=_id_key)
                ),
                replaced_view=replaced_view,
                invalidated_branches=branch_swap.invalidated_branches,
                residual_paths=residuals,
                warnings=resolved.warnings,
                snapshot=snapshot,
                claims=claims,
            )

        # §13 stale-export trigger class 1: the swap is already committed, so
        # cleanup failure or interruption never rolls it back.
        cleaned_sets: list[str] = []
        try:
            residual_paths = (
                *remove_assessment_sets(
                    workspace, superseded_snapshot_ids, removed_ledger=cleaned_sets
                ),
                *remove_branch_sets(
                    workspace, branch_swap.branch_ids, removed_ledger=cleaned_sets
                ),
            )
        except KeyboardInterrupt:
            # §14.14 rule 6: the class-9 error carries the complete committed
            # result, and the sets this pass never reached stay reported.
            cancelled = OperationCancelledError()
            cancelled.stage_result = build_result(
                unfinished_stale_paths(pending_stale_paths, cleaned_sets)
            )
            raise cancelled from None
        return build_result(residual_paths)


REPAIRABLE_STATUSES = frozenset({"rejected", "unsupported"})


def run_assessment_repair(
    workspace: Path,
    *,
    snapshot_id: str,
    id_factory: Callable[[str], str] = new_id,
    clock: Callable[[], datetime] | None = None,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Stage6Result:
    """§13.6 deterministic repair: adopt latest rewrites into a new generation.

    Non-LLM second form of Stage 6 — no provider call, no §15 contract, one
    stage-`13.6` run row with NULL execution identity and zero llm_calls rows.
    """

    now = clock or (lambda: datetime.now(timezone.utc))
    with writer_database(workspace, timeout_ms=timeout_ms, reconcile=True) as connection:
        snapshot = get_assessment_snapshot(connection, snapshot_id, current_only=False)
        if snapshot is None:
            raise SelectorNotFoundError()
        if snapshot.superseded_at is not None:
            raise SnapshotNotCurrentError()
        members = tuple(
            sorted(
                list_self_claims_for_snapshot(
                    connection, snapshot_id, current_only=False
                ),
                key=lambda item: _id_key(item.id),
            )
        )
        if not members:
            raise IntegrityFailureError("snapshot_claim_set_empty")
        if any(item.superseded_at is not None for item in members):
            raise IntegrityFailureError("snapshot_claim_not_current")
        summaries = tuple(
            item for item in members if item.claim_kind == "narrative_summary"
        )
        if len(summaries) != 1 or summaries[0].claim != snapshot.summary:
            raise IntegrityFailureError("snapshot_summary_mismatch")
        fresh_status = aggregate_verification_status(
            item.verification_status for item in members
        )
        if snapshot.verification_status != fresh_status:
            raise IntegrityFailureError("snapshot_aggregate_mismatch")
        # §13.6 preconditions, checked fail-closed in order before any mutation.
        if any(item.verification_status == "unverified" for item in members):
            raise SnapshotNotVerifiedError()
        repairable = tuple(
            item for item in members if item.verification_status in REPAIRABLE_STATUSES
        )
        if not repairable:
            raise NothingToRepairError()
        rewrites: dict[str, str] = {}
        for claim in repairable:
            findings = list_verification_findings(
                connection,
                target_type="self_claim",
                target_id=claim.id,
            )
            if not findings:
                raise RewriteUnavailableError()
            latest = max(
                findings, key=lambda item: (item.created_at, _id_key(item.id))
            )
            if latest.suggested_rewrite is None:
                raise RewriteUnavailableError()
            rewrites[claim.id] = latest.suggested_rewrite

        run_id = id_factory("run")
        superseded_snapshot_ids = (snapshot.id,)
        superseded_claim_ids = tuple(item.id for item in members)
        superseded_generation_ids: set[str] = set()
        replaced_view = ReplacedAssessmentView(snapshot.scope, snapshot.id)
        branch_swap = BranchSupersession()
        pending_stale_paths: tuple[str, ...] = ()

        def finalize_durable_run(
            failure_code: str, *, known_durable: bool = False
        ) -> tuple[bool, bool]:
            """Finish a discoverable run, retrying one ambiguous interrupt."""

            durable = known_durable
            interrupted = False
            for _attempt in range(2):
                try:
                    row = connection.execute(
                        "SELECT status FROM processing_runs WHERE id = ?",
                        (run_id,),
                    ).fetchone()
                except KeyboardInterrupt:
                    interrupted = True
                    continue
                except Exception:
                    return durable, interrupted
                if row is None:
                    return durable, interrupted
                durable = True
                if row["status"] in {"completed", "failed"}:
                    return durable, interrupted
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    finish_processing_run(
                        connection,
                        run_id=run_id,
                        finished_at=now(),
                        status="failed",
                        failure_code=failure_code,
                    )
                    connection.commit()
                except KeyboardInterrupt:
                    connection.rollback()
                    interrupted = True
                    continue
                except Exception:
                    connection.rollback()
                    return durable, interrupted
                return durable, interrupted
            return durable, interrupted

        # §12.13: the run row is the durable attempt record, committed on
        # its own before the candidate swap so a failed swap rolls back
        # every business row while the run survives as `failed`.
        try:
            connection.execute("BEGIN IMMEDIATE")
            create_processing_run(
                connection,
                run_id=run_id,
                stage="13.6",
                started_at=now(),
                provider=None,
                model=None,
                prompt_policy_hash=None,
                input_ids=(snapshot.id, *superseded_claim_ids),
                metadata={"mode": "repair"},
            )
            connection.commit()
        except BaseException as run_error:
            connection.rollback()
            if isinstance(run_error, KeyboardInterrupt):
                durable, _interrupted = finalize_durable_run("cancelled")
                if durable:
                    cancelled = OperationCancelledError()
                    extend_committed(cancelled, run_ids=[run_id])
                    raise cancelled from run_error
            raise

        new_snapshot: AssessmentSnapshot | None = None
        new_claims: tuple[SelfClaim, ...] = ()
        generation_id: str | None = None

        def committed_result(
            residuals: tuple[str, ...],
            snapshot_row: AssessmentSnapshot,
            claim_rows: Sequence[SelfClaim],
        ) -> Stage6Result:
            assert new_snapshot is not None and generation_id is not None
            return Stage6Result(
                run_id=run_id,
                snapshot_id=new_snapshot.id,
                created_claim_ids=tuple(
                    sorted((item.id for item in new_claims), key=_id_key)
                ),
                superseded_snapshot_ids=superseded_snapshot_ids,
                superseded_claim_ids=superseded_claim_ids,
                superseded_branch_ids=branch_swap.branch_ids,
                superseded_bullet_ids=branch_swap.bullet_ids,
                generation_id=generation_id,
                superseded_generation_ids=tuple(
                    sorted(superseded_generation_ids, key=_id_key)
                ),
                replaced_view=replaced_view,
                invalidated_branches=branch_swap.invalidated_branches,
                residual_paths=residuals,
                warnings=(),
                snapshot=snapshot_row,
                claims=list(claim_rows),
            )

        def durable_committed_result() -> Stage6Result | None:
            """Prove the candidate swap committed after an ambiguous interrupt."""

            if new_snapshot is None or generation_id is None or connection.in_transaction:
                return None
            run_row = connection.execute(
                "SELECT status FROM processing_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            snapshot_row = connection.execute(
                """
                SELECT produced_by_run_id, generation_id
                FROM assessment_snapshots
                WHERE id = ? AND superseded_at IS NULL
                """,
                (new_snapshot.id,),
            ).fetchone()
            claim_rows = connection.execute(
                """
                SELECT id, produced_by_run_id, generation_id
                FROM self_claims
                WHERE snapshot_id = ? AND superseded_at IS NULL
                """,
                (new_snapshot.id,),
            ).fetchall()
            expected_claim_ids = {item.id for item in new_claims}
            if (
                run_row is None
                or run_row["status"] != "completed"
                or snapshot_row is None
                or snapshot_row["produced_by_run_id"] != run_id
                or snapshot_row["generation_id"] != generation_id
                or {row["id"] for row in claim_rows} != expected_claim_ids
                or any(
                    row["produced_by_run_id"] != run_id
                    or row["generation_id"] != generation_id
                    for row in claim_rows
                )
            ):
                return None
            stored_snapshot = get_assessment_snapshot(
                connection, new_snapshot.id, current_only=True
            )
            stored_claims = list_self_claims_for_snapshot(
                connection, new_snapshot.id
            )
            if stored_snapshot is None or len(stored_claims) != len(new_claims):
                return None
            return committed_result(
                pending_stale_paths, stored_snapshot, stored_claims
            )

        try:
            connection.execute("BEGIN IMMEDIATE")
            new_snapshot_id = id_factory("snapshot")
            new_claims = tuple(
                SelfClaim(
                    id=id_factory("claim"),
                    created_at=now(),
                    superseded_at=None,
                    snapshot_id=new_snapshot_id,
                    claim=rewrites.get(old.id, old.claim),
                    claim_kind=old.claim_kind,
                    dimension=old.dimension,
                    source_fact_ids=list(old.source_fact_ids),
                    counter_fact_ids=list(old.counter_fact_ids),
                    confidence=old.confidence,
                    verification_status="unverified",
                    counterevidence=[],
                    uncertainty=old.uncertainty,
                    metadata=(
                        {
                            **old.metadata,
                            "adopted_rewrite_of_claim_id": old.id,
                        }
                        if old.id in rewrites
                        else dict(old.metadata)
                    ),
                )
                for old in members
            )
            narrative = next(
                item for item in new_claims if item.claim_kind == "narrative_summary"
            )
            new_snapshot = AssessmentSnapshot(
                id=new_snapshot_id,
                created_at=now(),
                superseded_at=None,
                scope=snapshot.scope,
                title=snapshot.title,
                summary=narrative.claim,
                gap_question_ids=sorted(
                    (
                        gap.id
                        for gap in list_gap_questions(connection)
                        if not gap.answered
                    ),
                    key=_id_key,
                ),
                contradiction_ids=sorted(
                    (item.id for item in list_contradictions(connection)),
                    key=_id_key,
                ),
                verification_status="unverified",
                metadata={"repaired_from_snapshot_id": snapshot.id},
            )
            swap_time = now()
            for table, ids in (
                ("assessment_snapshots", superseded_snapshot_ids),
                ("self_claims", superseded_claim_ids),
            ):
                placeholders = ",".join("?" for _ in ids)
                superseded_generation_ids.update(
                    row[0]
                    for row in connection.execute(
                        f"SELECT DISTINCT generation_id FROM {table} "
                        f"WHERE id IN ({placeholders})",
                        ids,
                    )
                )
            # §13.10: the repair form is an ordinary Stage 6 swap, so it
            # invalidates the replaced view's branches exactly like generation.
            branch_swap = supersede_dependent_branches(
                connection, superseded_snapshot_ids, superseded_at=swap_time
            )
            superseded_generation_ids.update(branch_swap.superseded_generation_ids)
            mark_self_claims_superseded(connection, superseded_claim_ids, swap_time)
            mark_assessment_snapshots_superseded(
                connection, superseded_snapshot_ids, swap_time
            )
            generation_id = id_factory("gen")
            insert_assessment_snapshot(
                connection,
                new_snapshot,
                produced_by_run_id=run_id,
                generation_id=generation_id,
            )
            for claim in new_claims:
                insert_self_claim(
                    connection,
                    claim,
                    produced_by_run_id=run_id,
                    generation_id=generation_id,
                )
            # The ordinary §12 Stage 6 transaction checks bind this swap too.
            if len(list_assessment_snapshots(connection)) > 1:
                raise IntegrityFailureError("assessment_view_not_unique")
            orphan = connection.execute(
                """
                SELECT 1 FROM self_claims AS claim
                JOIN assessment_snapshots AS snapshot ON snapshot.id = claim.snapshot_id
                WHERE claim.superseded_at IS NULL AND snapshot.superseded_at IS NOT NULL
                LIMIT 1
                """
            ).fetchone()
            if orphan is not None:
                raise IntegrityFailureError("current_claim_superseded_snapshot")
            finish_processing_run(
                connection,
                run_id=run_id,
                finished_at=now(),
                status="completed",
                output_ids=(new_snapshot.id, *(item.id for item in new_claims)),
            )
            # Same pre-commit pending-report pattern as the generated form.
            pending_stale_paths = (
                *assessment_set_paths(workspace, superseded_snapshot_ids),
                *branch_set_paths(workspace, branch_swap.branch_ids),
            )
            report_managed_residuals(pending_stale_paths)
            connection.commit()
        except BaseException as swap_error:
            connection.rollback()
            withdraw_pending_unless_superseded(
                connection, pending_stale_paths, superseded_snapshot_ids
            )
            if isinstance(swap_error, KeyboardInterrupt):
                durable_result = durable_committed_result()
                if durable_result is not None:
                    cancelled = OperationCancelledError()
                    cancelled.stage_result = durable_result
                    raise cancelled from swap_error
            # §12.13: the rolled-back candidate leaves the run behind as
            # the durable failed attempt owning no business rows.
            failure_code = (
                "cancelled"
                if isinstance(swap_error, KeyboardInterrupt)
                else "business_commit_failed"
            )
            durable_run, finalization_interrupted = finalize_durable_run(
                failure_code, known_durable=True
            )
            # §14.14 rule 5: the command boundary reports the durable
            # failed run, so the raised error must carry its ID — a
            # non-typed storage failure is wrapped to reach the envelope.
            if isinstance(swap_error, KeyboardInterrupt) or finalization_interrupted:
                cancelled = OperationCancelledError()
                extend_committed(
                    cancelled, run_ids=[run_id] if durable_run else []
                )
                raise cancelled from swap_error
            if isinstance(swap_error, Exp2ResError):
                extend_committed(swap_error, run_ids=[run_id])
                raise
            wrapped = IntegrityFailureError(failure_code)
            extend_committed(wrapped, run_ids=[run_id])
            raise wrapped from swap_error

        # §13 stale-export trigger class 1: the swap is already committed;
        # cleanup failure or interruption never rolls it back.
        repair_cleaned_sets: list[str] = []
        try:
            residual_paths = (
                *remove_assessment_sets(
                    workspace,
                    superseded_snapshot_ids,
                    removed_ledger=repair_cleaned_sets,
                ),
                *remove_branch_sets(
                    workspace,
                    branch_swap.branch_ids,
                    removed_ledger=repair_cleaned_sets,
                ),
            )
        except KeyboardInterrupt:
            # §14.14 rule 6: the class-9 error carries the complete
            # committed result, and only the sets never reached stay reported.
            cancelled = OperationCancelledError()
            cancelled.stage_result = committed_result(
                unfinished_stale_paths(pending_stale_paths, repair_cleaned_sets),
                new_snapshot,
                sorted(new_claims, key=lambda item: _id_key(item.id)),
            )
            raise cancelled from None
        stored_snapshot = next(
            item
            for item in list_assessment_snapshots(connection)
            if item.id == new_snapshot.id
        )
        stored_claims = list_self_claims_for_snapshot(connection, new_snapshot.id)

    return committed_result(residual_paths, stored_snapshot, stored_claims)


def assess_generate_outcome(generated: Stage6Result) -> Outcome:
    """One §14.14 rule 5 composition for completed and interrupted swaps."""

    assert generated.snapshot is not None and generated.snapshot_id is not None
    created_groups = [
        EntityIdGroup(
            entity_type="assessment_snapshot", ids=[generated.snapshot_id]
        ),
        EntityIdGroup(
            entity_type="self_claim", ids=list(generated.created_claim_ids)
        ),
    ]
    superseded_groups: list[EntityIdGroup] = []
    if generated.superseded_claim_ids:
        superseded_groups.append(
            EntityIdGroup(
                entity_type="self_claim",
                ids=list(generated.superseded_claim_ids),
            )
        )
    if generated.superseded_snapshot_ids:
        superseded_groups.append(
            EntityIdGroup(
                entity_type="assessment_snapshot",
                ids=list(generated.superseded_snapshot_ids),
            )
        )
    if generated.superseded_branch_ids:
        superseded_groups.append(
            EntityIdGroup(
                entity_type="resume_branch",
                ids=list(generated.superseded_branch_ids),
            )
        )
    if generated.superseded_bullet_ids:
        superseded_groups.append(
            EntityIdGroup(
                entity_type="resume_bullet",
                ids=list(generated.superseded_bullet_ids),
            )
        )
    prior = (
        ""
        if generated.replaced_view is None
        else f"; superseded {generated.replaced_view.snapshot_id}"
    )
    return Outcome(
        affected_ids=AffectedIds(
            created=created_groups,
            superseded=superseded_groups,
            deleted=[],
        ),
        generation_ids=sorted(
            {
                *(
                    [generated.generation_id]
                    if generated.generation_id is not None
                    else []
                ),
                *generated.superseded_generation_ids,
            },
            key=lambda value: value.encode("utf-8"),
        ),
        run_ids=[generated.run_id],
        invalidated_branches=list(generated.invalidated_branches),
        residual_paths=list(generated.residual_paths),
        warnings=list(generated.warnings),
        result=None,
        human_result=(
            f"Created {generated.snapshot.id} — {generated.snapshot.title}; "
            f"{len(generated.claims)} claims{prior}."
        ),
    )


def repair_outcome(repaired: Stage6Result) -> Outcome:
    """One §14.14 rule 5 composition for completed and interrupted swaps."""

    assert repaired.snapshot is not None and repaired.snapshot_id is not None
    superseded_claim_ids = set(repaired.superseded_claim_ids)
    adopted = sum(
        1
        for claim in repaired.claims
        if claim.metadata.get("adopted_rewrite_of_claim_id") in superseded_claim_ids
    )
    created_groups = [
        EntityIdGroup(
            entity_type="assessment_snapshot", ids=[repaired.snapshot_id]
        ),
        EntityIdGroup(
            entity_type="self_claim", ids=list(repaired.created_claim_ids)
        ),
    ]
    superseded_groups = [
        EntityIdGroup(
            entity_type="self_claim",
            ids=list(repaired.superseded_claim_ids),
        ),
        EntityIdGroup(
            entity_type="assessment_snapshot",
            ids=list(repaired.superseded_snapshot_ids),
        ),
    ]
    if repaired.superseded_branch_ids:
        superseded_groups.append(
            EntityIdGroup(
                entity_type="resume_branch",
                ids=list(repaired.superseded_branch_ids),
            )
        )
    if repaired.superseded_bullet_ids:
        superseded_groups.append(
            EntityIdGroup(
                entity_type="resume_bullet",
                ids=list(repaired.superseded_bullet_ids),
            )
        )
    return Outcome(
        affected_ids=AffectedIds(
            created=created_groups,
            superseded=superseded_groups,
            deleted=[],
        ),
        generation_ids=sorted(
            {
                *(
                    [repaired.generation_id]
                    if repaired.generation_id is not None
                    else []
                ),
                *repaired.superseded_generation_ids,
            },
            key=lambda value: value.encode("utf-8"),
        ),
        run_ids=[repaired.run_id],
        invalidated_branches=list(repaired.invalidated_branches),
        residual_paths=list(repaired.residual_paths),
        result=None,
        human_result=(
            f"Repaired {repaired.snapshot.id} — {repaired.snapshot.title}; "
            f"adopted {adopted} of {len(repaired.claims)} claims; "
            f"superseded {repaired.superseded_snapshot_ids[0]}. "
            f"Every claim is unverified; run exp2res assess verify "
            f"--snapshot {repaired.snapshot.id}."
        ),
    )
