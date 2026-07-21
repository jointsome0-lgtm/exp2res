"""§13.4 complete-set gap and contradiction detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence, cast

from pydantic import BaseModel, ValidationError

from exp2res.domain.models import Contradiction, GapQuestion
from exp2res.domain.results import InvalidatedView, invalidated_view
from exp2res.exports.managed import assessment_set_paths, remove_assessment_sets
from exp2res.llm.contracts import (
    ContractValidationError,
    ContractWarning,
    validation_diagnostics,
)
from exp2res.llm.detector import (
    DETECTOR_CONTRACT,
    ContradictionCandidate,
    DetectorInput,
    DetectorOutput,
    EvidenceContextEntry,
    GapCandidate,
)
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    insert_contradiction,
    insert_gap_question,
    list_assessment_snapshots,
    list_contradictions,
    list_experience_facts,
    list_gap_questions,
    list_self_signals,
    list_self_claims_for_snapshot,
    mark_assessment_snapshots_superseded,
    mark_contradictions_superseded,
    mark_gap_questions_superseded,
    mark_self_signals_superseded,
    mark_self_claims_superseded,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    report_managed_residuals,
    withdraw_managed_residuals,
    writer_database,
)

from .lineage import plan_lineages
from .orchestration import PlannedCall, run_complete_stage


GapStructuralKey = tuple[str, str, str, str]
ContradictionStructuralKey = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Stage4Result:
    run_id: str
    retained: bool
    created_gap_ids: tuple[str, ...]
    created_contradiction_ids: tuple[str, ...]
    superseded_gap_ids: tuple[str, ...]
    superseded_contradiction_ids: tuple[str, ...]
    superseded_signal_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    superseded_snapshot_ids: tuple[str, ...]
    invalidated_views: tuple[InvalidatedView, ...]
    residual_paths: tuple[str, ...]
    generation_id: str | None
    superseded_generation_ids: tuple[str, ...]
    warnings: tuple[ContractWarning, ...]
    current_gaps: tuple[GapQuestion, ...]
    current_contradictions: tuple[Contradiction, ...]


@dataclass(frozen=True)
class _ResolvedDetection:
    gaps: tuple[GapQuestion, ...]
    contradictions: tuple[Contradiction, ...]
    warnings: tuple[ContractWarning, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def gap_structural_key(gap: GapCandidate | GapQuestion) -> GapStructuralKey:
    return (gap.target_type, gap.target_id, gap.reason, gap.priority)


def contradiction_structural_key(
    contradiction: ContradictionCandidate | Contradiction,
) -> ContradictionStructuralKey:
    references = {
        (contradiction.left_ref_type, contradiction.left_ref_id),
        (contradiction.right_ref_type, contradiction.right_ref_id),
    }
    return tuple(
        sorted(references, key=lambda ref: (_id_key(ref[0]), _id_key(ref[1])))
    )


def _diagnostic(
    *, collection: str, index: int, field: str | None, kind: str
) -> dict[str, object]:
    location: tuple[object, ...] = (collection, index)
    if field is not None:
        location = (*location, field)
    return {"loc": location, "type": kind}


def _enrich_for(
    input_payload: DetectorInput,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    supplied: dict[str, frozenset[str]] = {
        "experience_fact": frozenset(fact.id for fact in input_payload.facts),
        "raw_log": frozenset(
            entry.raw_log.id for entry in input_payload.evidence_context
        ),
        "evidence_item": frozenset(
            entry.evidence_item.id for entry in input_payload.evidence_context
        ),
    }

    def reference_kind(ref_type: str, ref_id: str) -> str | None:
        if ref_id in supplied[ref_type]:
            return None
        if any(
            ref_id in ids for candidate_type, ids in supplied.items()
            if candidate_type != ref_type
        ):
            return "wrong_type_target"
        return "out_of_context_target"

    def enrich(decoded: dict[str, Any]) -> dict[str, Any]:
        try:
            output = DetectorOutput.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
            )
        except ValidationError as error:
            raise ContractValidationError(
                validation_diagnostics(DETECTOR_CONTRACT, error.errors())
            ) from None

        errors: list[dict[str, object]] = []
        for index, gap in enumerate(output.gap_questions):
            kind = reference_kind(gap.target_type, gap.target_id)
            if kind is not None:
                errors.append(
                    _diagnostic(
                        collection="gap_questions",
                        index=index,
                        field="target_id",
                        kind=kind,
                    )
                )

        for index, contradiction in enumerate(output.contradictions):
            left = (contradiction.left_ref_type, contradiction.left_ref_id)
            right = (contradiction.right_ref_type, contradiction.right_ref_id)
            if left == right:
                errors.append(
                    _diagnostic(
                        collection="contradictions",
                        index=index,
                        field=None,
                        kind="self_referential_contradiction",
                    )
                )
            for side in ("left", "right"):
                ref_type = cast(str, getattr(contradiction, f"{side}_ref_type"))
                ref_id = cast(str, getattr(contradiction, f"{side}_ref_id"))
                kind = reference_kind(ref_type, ref_id)
                if kind is not None:
                    errors.append(
                        _diagnostic(
                            collection="contradictions",
                            index=index,
                            field=f"{side}_ref_id",
                            kind=kind,
                        )
                    )

        gap_keys: set[GapStructuralKey] = set()
        for index, gap in enumerate(output.gap_questions):
            key = gap_structural_key(gap)
            if key in gap_keys:
                errors.append(
                    _diagnostic(
                        collection="gap_questions",
                        index=index,
                        field=None,
                        kind="duplicate_structural_key",
                    )
                )
            gap_keys.add(key)

        contradiction_keys: set[ContradictionStructuralKey] = set()
        for index, contradiction in enumerate(output.contradictions):
            key = contradiction_structural_key(contradiction)
            if key in contradiction_keys:
                errors.append(
                    _diagnostic(
                        collection="contradictions",
                        index=index,
                        field=None,
                        kind="duplicate_structural_key",
                    )
                )
            contradiction_keys.add(key)

        if errors:
            raise ContractValidationError(
                validation_diagnostics(DETECTOR_CONTRACT, errors)
            )
        return decoded

    return enrich


def _resolve_for(
    *,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> Callable[[BaseModel], object]:
    def resolve(validated: BaseModel) -> object:
        output = cast(DetectorOutput, validated)
        gaps = tuple(
            GapQuestion(
                id=id_factory("gap"),
                created_at=clock(),
                superseded_at=None,
                target_type=candidate.target_type,
                target_id=candidate.target_id,
                question=candidate.question,
                reason=candidate.reason,
                priority=candidate.priority,
                answered=False,
                answer_log_id=None,
            )
            for candidate in output.gap_questions
        )
        contradictions = tuple(
            Contradiction(
                id=id_factory("contradiction"),
                created_at=clock(),
                superseded_at=None,
                title=candidate.title,
                description=candidate.description,
                left_ref_type=candidate.left_ref_type,
                left_ref_id=candidate.left_ref_id,
                right_ref_type=candidate.right_ref_type,
                right_ref_id=candidate.right_ref_id,
                metadata={},
            )
            for candidate in output.contradictions
        )
        return _ResolvedDetection(gaps, contradictions, tuple(output.warnings))

    return resolve


def run_detection_generation(
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
) -> Stage4Result:
    """Run the one-call complete Stage 4 candidate and atomic retain/swap."""

    now = clock or (lambda: datetime.now(timezone.utc))
    with writer_database(
        workspace, timeout_ms=timeout_ms, reconcile=True
    ) as connection:
        facts = tuple(
            sorted(list_experience_facts(connection), key=lambda fact: _id_key(fact.id))
        )
        contexts = plan_lineages(connection, log_id=None)
        effective_logs = {
            log.id: log for context in contexts for log in context.effective_logs
        }
        evidence_items = {
            item.id: item for context in contexts for item in context.evidence_items
        }
        evidence_context = tuple(
            EvidenceContextEntry(
                evidence_item=evidence_items[item_id],
                raw_log=effective_logs[evidence_items[item_id].raw_log_id],
            )
            for item_id in sorted(evidence_items, key=_id_key)
        )
        input_payload = DetectorInput(
            facts=list(facts), evidence_context=list(evidence_context)
        )
        run_id = id_factory("run")
        input_ids = tuple(
            sorted(
                {
                    *(fact.id for fact in facts),
                    *(entry.evidence_item.id for entry in evidence_context),
                    *(entry.raw_log.id for entry in evidence_context),
                },
                key=_id_key,
            )
        )
        planned = (
            ()
            if not facts and not effective_logs
            else (
                PlannedCall(
                    input_payload=input_payload,
                    input_ids=input_ids,
                    enrich=_enrich_for(input_payload),
                    resolve=_resolve_for(id_factory=id_factory, clock=now),
                ),
            )
        )

        retained = not planned
        created_gap_ids: tuple[str, ...] = ()
        created_contradiction_ids: tuple[str, ...] = ()
        superseded_gap_ids: tuple[str, ...] = ()
        superseded_contradiction_ids: tuple[str, ...] = ()
        superseded_signal_ids: tuple[str, ...] = ()
        superseded_claim_ids: tuple[str, ...] = ()
        superseded_snapshot_ids: tuple[str, ...] = ()
        invalidated_views: tuple[InvalidatedView, ...] = ()
        generation_id: str | None = None
        superseded_generation_ids: set[str] = set()

        def commit(
            held: sqlite3.Connection, resolved: Sequence[object]
        ) -> Iterable[str]:
            nonlocal retained
            nonlocal created_gap_ids, created_contradiction_ids
            nonlocal superseded_gap_ids, superseded_contradiction_ids
            nonlocal superseded_signal_ids
            nonlocal superseded_claim_ids, superseded_snapshot_ids, invalidated_views
            nonlocal generation_id

            candidate = cast(_ResolvedDetection, resolved[0])
            current_gaps = list_gap_questions(held)
            current_contradictions = list_contradictions(held)
            current_gap_keys = {gap_structural_key(gap) for gap in current_gaps}
            current_contradiction_keys = {
                contradiction_structural_key(item)
                for item in current_contradictions
            }
            candidate_gap_keys = {gap_structural_key(gap) for gap in candidate.gaps}
            candidate_contradiction_keys = {
                contradiction_structural_key(item)
                for item in candidate.contradictions
            }
            if (
                candidate_gap_keys == current_gap_keys
                and candidate_contradiction_keys == current_contradiction_keys
                and all(not gap.answered for gap in current_gaps)
            ):
                retained = True
                return ()

            retained = False
            swap_time = now()
            superseded_gap_ids = tuple(gap.id for gap in current_gaps)
            superseded_contradiction_ids = tuple(
                item.id for item in current_contradictions
            )
            current_signals = list_self_signals(held)
            superseded_signal_ids = tuple(item.id for item in current_signals)
            current_snapshots = list_assessment_snapshots(held)
            superseded_snapshot_ids = tuple(item.id for item in current_snapshots)
            superseded_claim_ids = tuple(
                claim.id
                for snapshot in current_snapshots
                for claim in list_self_claims_for_snapshot(held, snapshot.id)
            )
            invalidated_views = tuple(
                invalidated_view(
                    scope=snapshot.scope,
                    scope_target=snapshot.scope_target,
                    snapshot_id=snapshot.id,
                )
                for snapshot in current_snapshots
            )
            for table, ids in (
                ("gap_questions", superseded_gap_ids),
                ("contradictions", superseded_contradiction_ids),
                ("self_signals", superseded_signal_ids),
                ("self_claims", superseded_claim_ids),
                ("assessment_snapshots", superseded_snapshot_ids),
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
            mark_gap_questions_superseded(held, superseded_gap_ids, swap_time)
            mark_contradictions_superseded(
                held, superseded_contradiction_ids, swap_time
            )
            mark_self_signals_superseded(
                held, superseded_signal_ids, swap_time
            )
            mark_self_claims_superseded(held, superseded_claim_ids, swap_time)
            mark_assessment_snapshots_superseded(
                held, superseded_snapshot_ids, swap_time
            )
            generation_id = id_factory("gen")
            for gap in candidate.gaps:
                insert_gap_question(
                    held,
                    gap,
                    produced_by_run_id=run_id,
                    generation_id=generation_id,
                )
            for contradiction in candidate.contradictions:
                insert_contradiction(
                    held,
                    contradiction,
                    produced_by_run_id=run_id,
                    generation_id=generation_id,
                )
            created_gap_ids = tuple(gap.id for gap in candidate.gaps)
            created_contradiction_ids = tuple(
                item.id for item in candidate.contradictions
            )
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
            return (*created_gap_ids, *created_contradiction_ids)

        pending_stale_paths: tuple[str, ...] = ()
        try:
            outcome = run_complete_stage(
            workspace,
            connection,
            stage="13.4",
            contract=DETECTOR_CONTRACT,
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
        # Capture the complete post-run sets while the command still owns the
        # writer lock, so the §14.7 result cannot race a following writer.
        current_gaps = tuple(
            sorted(list_gap_questions(connection), key=lambda gap: _id_key(gap.id))
        )
        current_contradictions = tuple(
            sorted(
                list_contradictions(connection),
                key=lambda contradiction: _id_key(contradiction.id),
            )
        )

    resolved = tuple(cast(_ResolvedDetection, item) for item in outcome.resolved)
    return Stage4Result(
        run_id=run_id,
        retained=retained,
        created_gap_ids=created_gap_ids,
        created_contradiction_ids=created_contradiction_ids,
        superseded_gap_ids=superseded_gap_ids,
        superseded_contradiction_ids=superseded_contradiction_ids,
        superseded_signal_ids=tuple(sorted(superseded_signal_ids, key=_id_key)),
        superseded_claim_ids=tuple(sorted(superseded_claim_ids, key=_id_key)),
        superseded_snapshot_ids=tuple(
            sorted(superseded_snapshot_ids, key=_id_key)
        ),
        invalidated_views=tuple(
            sorted(invalidated_views, key=lambda item: _id_key(item.snapshot_id))
        ),
        residual_paths=residual_paths,
        generation_id=generation_id,
        superseded_generation_ids=tuple(
            sorted(superseded_generation_ids, key=_id_key)
        ),
        warnings=tuple(
            warning for candidate in resolved for warning in candidate.warnings
        ),
        current_gaps=current_gaps,
        current_contradictions=current_contradictions,
    )
