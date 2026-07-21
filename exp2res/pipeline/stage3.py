"""§13.3 fact extraction over complete correction lineages."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence, cast

from pydantic import BaseModel, ValidationError

from exp2res.domain.models import ExperienceFact, RawLog
from exp2res.domain.results import InvalidatedView, invalidated_view
from exp2res.domain.temporal import (
    confidence_exceeds,
    interval_contains,
    occurred_interval,
    placement_supports,
)
from exp2res.exports.managed import assessment_set_paths, remove_assessment_sets
from exp2res.llm.contracts import (
    ContractValidationError,
    ContractWarning,
    validation_diagnostics,
)
from exp2res.llm.fact_extractor import (
    FACT_EXTRACTOR_CONTRACT,
    FactCandidate,
    FactExtractorOutput,
)
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    insert_experience_fact,
    list_assessment_snapshots,
    list_contradictions,
    list_gap_questions,
    list_self_signals,
    list_self_claims_for_snapshot,
    mark_assessment_snapshots_superseded,
    mark_contradictions_superseded,
    mark_facts_superseded,
    mark_gap_questions_superseded,
    mark_self_signals_superseded,
    mark_self_claims_superseded,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    report_managed_residuals,
    writer_database,
)

from .lineage import LineageContext, plan_lineages
from .orchestration import (
    PlannedCall,
    run_complete_stage,
    withdraw_pending_unless_superseded,
)


@dataclass(frozen=True)
class Stage3Result:
    run_id: str
    created: tuple[str, ...]
    superseded: tuple[str, ...]
    generation_ids: tuple[str, ...]
    superseded_generation_ids: tuple[str, ...]
    superseded_gap_ids: tuple[str, ...]
    superseded_contradiction_ids: tuple[str, ...]
    superseded_signal_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    superseded_snapshot_ids: tuple[str, ...]
    invalidated_views: tuple[InvalidatedView, ...]
    residual_paths: tuple[str, ...]
    warnings: tuple[ContractWarning, ...]


@dataclass(frozen=True)
class _ResolvedLineage:
    context: LineageContext
    generation_id: str
    facts: tuple[ExperienceFact, ...]
    warnings: tuple[ContractWarning, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _log_order(log: RawLog) -> tuple[object, bytes]:
    return (log.recorded_at.astimezone(timezone.utc), _id_key(log.id))


def _effective_logs_for(
    context: LineageContext, candidate: FactCandidate
) -> tuple[RawLog, ...]:
    selected_log_ids = {
        context.item_to_log_id[item_id] for item_id in candidate.evidence_item_ids
    }
    return tuple(
        log
        for log_id, log in context.effective_log_by_id.items()
        if log_id in selected_log_ids
    )


def _governing_record(
    context: LineageContext, candidate: FactCandidate
) -> RawLog:
    return max(_effective_logs_for(context, candidate), key=_log_order)


def _reference_diagnostic(
    *, fact_index: int, field: str, item_index: int | None, kind: str
) -> dict[str, object]:
    location: tuple[object, ...] = ("facts", fact_index, field)
    if item_index is not None:
        location = (*location, item_index)
    return {"loc": location, "type": kind}


def _enrich_for(context: LineageContext) -> Callable[[dict[str, Any]], dict[str, Any]]:
    selectable_ids = frozenset(context.item_to_log_id)

    def enrich(decoded: dict[str, Any]) -> dict[str, Any]:
        try:
            candidate_output = FactExtractorOutput.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
            )
        except ValidationError as error:
            raise ContractValidationError(
                validation_diagnostics(FACT_EXTRACTOR_CONTRACT, error.errors())
            ) from None

        errors: list[dict[str, object]] = []
        for fact_index, fact in enumerate(candidate_output.facts):
            for item_index, item_id in enumerate(fact.evidence_item_ids):
                if item_id not in selectable_ids:
                    errors.append(
                        _reference_diagnostic(
                            fact_index=fact_index,
                            field="evidence_item_ids",
                            item_index=item_index,
                            kind="out_of_context_evidence",
                        )
                    )
            if any(item_id not in selectable_ids for item_id in fact.evidence_item_ids):
                continue

            selected_effective_logs = _effective_logs_for(context, fact)
            if not selected_effective_logs:
                errors.append(
                    _reference_diagnostic(
                        fact_index=fact_index,
                        field="evidence_item_ids",
                        item_index=None,
                        kind="descriptor_only_selection",
                    )
                )
                continue

            governing = max(selected_effective_logs, key=_log_order)
            if fact.occurred is not None:
                if not interval_contains(
                    occurred_interval(governing.occurred),
                    occurred_interval(fact.occurred),
                ):
                    errors.append(
                        _reference_diagnostic(
                            fact_index=fact_index,
                            field="occurred",
                            item_index=None,
                            kind="temporal_widening",
                        )
                    )
                if confidence_exceeds(
                    fact.occurred.confidence, governing.occurred.confidence
                ):
                    errors.append(
                        _reference_diagnostic(
                            fact_index=fact_index,
                            field="occurred",
                            item_index=None,
                            kind="temporal_confidence_raise",
                        )
                    )
                if not any(
                    placement_supports(fact.occurred, log.occurred)
                    for log in selected_effective_logs
                ):
                    errors.append(
                        _reference_diagnostic(
                            fact_index=fact_index,
                            field="occurred",
                            item_index=None,
                            kind="temporal_unsupported_placement",
                        )
                    )

            selected_log_ids = {
                context.item_to_log_id[item_id]
                for item_id in fact.evidence_item_ids
            }
            has_non_manual = any(
                context.item_to_strength[item_id] != "manual_claim"
                for item_id in fact.evidence_item_ids
            )
            ceiling = (
                "high"
                if len(selected_log_ids) >= 2 and has_non_manual
                else "medium"
            )
            if confidence_exceeds(fact.confidence, ceiling):
                errors.append(
                    _reference_diagnostic(
                        fact_index=fact_index,
                        field="confidence",
                        item_index=None,
                        kind="confidence_above_ceiling",
                    )
                )

        if errors:
            raise ContractValidationError(
                validation_diagnostics(FACT_EXTRACTOR_CONTRACT, errors)
            )
        return decoded

    return enrich


def _resolve_for(
    context: LineageContext,
    *,
    generation_id: str,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> Callable[[BaseModel], object]:
    def resolve(validated: BaseModel) -> object:
        output = cast(FactExtractorOutput, validated)
        facts: list[ExperienceFact] = []
        for candidate in output.facts:
            governing = _governing_record(context, candidate)
            selected_log_ids = sorted(
                {
                    context.item_to_log_id[item_id]
                    for item_id in candidate.evidence_item_ids
                },
                key=_id_key,
            )
            occurred = (
                governing.occurred
                if candidate.occurred is None
                else candidate.occurred
            )
            facts.append(
                ExperienceFact(
                    id=id_factory("fact"),
                    created_at=clock(),
                    superseded_at=None,
                    claim=candidate.claim,
                    claim_kind=candidate.claim_kind,
                    project=governing.project,
                    role=candidate.role,
                    company=candidate.company,
                    context=candidate.context,
                    ownership_level=candidate.ownership_level,
                    action=candidate.action,
                    object=candidate.object,
                    outcome=candidate.outcome,
                    skills=candidate.skills,
                    technologies=candidate.technologies,
                    themes=candidate.themes,
                    occurred=occurred,
                    source_log_ids=selected_log_ids,
                    evidence_item_ids=sorted(candidate.evidence_item_ids, key=_id_key),
                    confidence=candidate.confidence,
                    metadata={},
                )
            )
        return _ResolvedLineage(
            context=context,
            generation_id=generation_id,
            facts=tuple(facts),
            warnings=tuple(output.warnings),
        )

    return resolve


def _current_fact_ids_for_lineage(
    connection: sqlite3.Connection, member_ids: tuple[str, ...]
) -> tuple[str, ...]:
    if not member_ids:
        return ()
    placeholders = ",".join("?" for _ in member_ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT ef.id
        FROM experience_facts AS ef
        JOIN fact_sources AS fs ON fs.fact_id = ef.id
        JOIN evidence_items AS ei ON ei.id = fs.evidence_item_id
        WHERE ef.superseded_at IS NULL
          AND ei.raw_log_id IN ({placeholders})
        """,
        member_ids,
    ).fetchall()
    return tuple(sorted((row[0] for row in rows), key=_id_key))


def run_fact_extraction(
    workspace: Path,
    *,
    log_id: str | None,
    selection: LLMSelection,
    budgets: CallBudgets,
    runner: ContractRunner,
    id_factory: Callable[[str], str] = new_id,
    parent_run_id: str | None = None,
    reconcile: bool = True,
    connection: sqlite3.Connection | None = None,
    clock: Callable[[], datetime] | None = None,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    cli_version: str = "test-double",
    capability_check: Callable[[], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] | None = None,
    resolved_credentials: Iterable[bytes] = (),
) -> Stage3Result:
    """Run one complete Stage 3 replacement over the selected lineages."""

    now = clock or (lambda: datetime.now(timezone.utc))
    # §8.1: a §13.13 lifecycle holds one writer authority across its whole
    # Stage 3-5 flow and passes the held connection; a direct command still
    # acquires its own.
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, timeout_ms=timeout_ms, reconcile=reconcile)
    )
    with held as connection:
        contexts = plan_lineages(connection, log_id=log_id)
        run_id = id_factory("run")
        generation_ids = tuple(id_factory("gen") for _ in contexts)
        planned = tuple(
            PlannedCall(
                input_payload=context.extractor_input(),
                input_ids=context.member_ids,
                enrich=_enrich_for(context),
                resolve=_resolve_for(
                    context,
                    generation_id=generation_id,
                    id_factory=id_factory,
                    clock=now,
                ),
            )
            for context, generation_id in zip(contexts, generation_ids)
        )
        superseded_ids: list[str] = []
        superseded_gap_ids: list[str] = []
        superseded_contradiction_ids: list[str] = []
        superseded_signal_ids: list[str] = []
        superseded_claim_ids: list[str] = []
        superseded_snapshot_ids: list[str] = []
        invalidated_views: list[InvalidatedView] = []
        superseded_generation_ids: set[str] = set()

        def commit(
            held: sqlite3.Connection, resolved: Sequence[object]
        ) -> Iterable[str]:
            swaps = tuple(cast(_ResolvedLineage, item) for item in resolved)
            swap_time = now()
            created_ids: list[str] = []
            for swap in swaps:
                previous = _current_fact_ids_for_lineage(
                    held, swap.context.member_ids
                )
                if previous:
                    # §14.14 rule 5: the replacement invalidates the prior
                    # facts' generations, and the envelope reports produced
                    # OR invalidated generation IDs — capture them before
                    # the supersession closes the rows.
                    placeholders = ",".join("?" for _ in previous)
                    superseded_generation_ids.update(
                        row[0]
                        for row in held.execute(
                            "SELECT DISTINCT generation_id FROM"
                            f" experience_facts WHERE id IN ({placeholders})",
                            previous,
                        )
                    )
                    mark_facts_superseded(held, previous, swap_time)
                    superseded_ids.extend(previous)
                for fact in swap.facts:
                    insert_experience_fact(
                        held,
                        fact,
                        produced_by_run_id=run_id,
                        generation_id=swap.generation_id,
                    )
                    created_ids.append(fact.id)
            if created_ids or superseded_ids:
                current_gaps = list_gap_questions(held)
                current_contradictions = list_contradictions(held)
                current_signals = list_self_signals(held)
                current_snapshots = list_assessment_snapshots(held)
                superseded_gap_ids.extend(gap.id for gap in current_gaps)
                superseded_contradiction_ids.extend(
                    contradiction.id for contradiction in current_contradictions
                )
                superseded_signal_ids.extend(signal.id for signal in current_signals)
                superseded_snapshot_ids.extend(item.id for item in current_snapshots)
                for snapshot in current_snapshots:
                    superseded_claim_ids.extend(
                        item.id for item in list_self_claims_for_snapshot(held, snapshot.id)
                    )
                    invalidated_views.append(
                        invalidated_view(
                            scope=snapshot.scope,
                            scope_target=snapshot.scope_target,
                            snapshot_id=snapshot.id,
                        )
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
                mark_gap_questions_superseded(
                    held, superseded_gap_ids, swap_time
                )
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
            return created_ids

        pending_stale_paths: tuple[str, ...] = ()
        try:
            outcome = run_complete_stage(
            workspace,
            connection,
            stage="13.3",
            contract=FACT_EXTRACTOR_CONTRACT,
            selection=selection,
            budgets=budgets,
            runner=runner,
            planned=planned,
            commit=commit,
            run_id=run_id,
            parent_run_id=parent_run_id,
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
        # §13 stale-export trigger class 1: business supersession is already
        # committed; cleanup failure is returned and never rolls it back.
        residual_paths = remove_assessment_sets(
            workspace, superseded_snapshot_ids
        )

    resolved_lineages = tuple(cast(_ResolvedLineage, item) for item in outcome.resolved)
    return Stage3Result(
        run_id=run_id,
        created=outcome.output_ids,
        superseded=tuple(superseded_ids),
        generation_ids=tuple(
            item.generation_id for item in resolved_lineages if item.facts
        ),
        superseded_generation_ids=tuple(
            sorted(superseded_generation_ids, key=_id_key)
        ),
        # §14.14 rule 5: envelope ID collections are ID-byte-ordered; the
        # listing helpers return creation-time order.
        superseded_gap_ids=tuple(sorted(superseded_gap_ids, key=_id_key)),
        superseded_contradiction_ids=tuple(
            sorted(superseded_contradiction_ids, key=_id_key)
        ),
        superseded_signal_ids=tuple(sorted(superseded_signal_ids, key=_id_key)),
        superseded_claim_ids=tuple(sorted(superseded_claim_ids, key=_id_key)),
        superseded_snapshot_ids=tuple(
            sorted(superseded_snapshot_ids, key=_id_key)
        ),
        invalidated_views=tuple(
            sorted(invalidated_views, key=lambda item: _id_key(item.snapshot_id))
        ),
        residual_paths=residual_paths,
        warnings=tuple(
            warning
            for item in resolved_lineages
            for warning in item.warnings
        ),
    )
