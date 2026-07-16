"""§13.3 fact extraction over complete correction lineages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence, cast

from pydantic import BaseModel, ValidationError

from exp2res.domain.models import ExperienceFact, RawLog
from exp2res.domain.temporal import (
    confidence_exceeds,
    interval_contains,
    occurred_interval,
    placement_supports,
)
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
from exp2res.llm.preflight import CODEX_TOKEN_PATTERNS
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    insert_experience_fact,
    mark_facts_superseded,
)
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, writer_database

from .lineage import LineageContext, plan_lineages
from .orchestration import PlannedCall, run_complete_stage


@dataclass(frozen=True)
class Stage3Result:
    run_id: str
    created: tuple[str, ...]
    superseded: tuple[str, ...]
    generation_ids: tuple[str, ...]
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
    clock: Callable[[], datetime] | None = None,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    cli_version: str = "test-double",
    capability_check: Callable[[], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] = CODEX_TOKEN_PATTERNS,
    resolved_credentials: Iterable[bytes] = (),
) -> Stage3Result:
    """Run one complete Stage 3 replacement over the selected lineages."""

    now = clock or (lambda: datetime.now(timezone.utc))
    with writer_database(
        workspace, timeout_ms=timeout_ms, reconcile=True
    ) as connection:
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
            return created_ids

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
            clock=now,
            cli_version=cli_version,
            capability_check=capability_check,
            monotonic=monotonic,
            sleeper=sleeper,
            jitter=jitter,
            token_patterns=token_patterns,
            resolved_credentials=resolved_credentials,
        )

    resolved_lineages = tuple(cast(_ResolvedLineage, item) for item in outcome.resolved)
    return Stage3Result(
        run_id=run_id,
        created=outcome.output_ids,
        superseded=tuple(superseded_ids),
        generation_ids=tuple(
            item.generation_id for item in resolved_lineages if item.facts
        ),
        warnings=tuple(
            warning
            for item in resolved_lineages
            for warning in item.warnings
        ),
    )
