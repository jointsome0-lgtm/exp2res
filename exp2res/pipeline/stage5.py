"""§13.5 complete-generation self-signal extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence, cast

from pydantic import BaseModel, ValidationError

from exp2res.domain.calibration import signal_confidence_cap
from exp2res.domain.models import EvidenceItem, ExperienceFact, SelfSignal
from exp2res.domain.results import InvalidatedView, invalidated_view
from exp2res.domain.temporal import confidence_exceeds
from exp2res.errors import IntegrityFailureError
from exp2res.exports.managed import remove_assessment_sets
from exp2res.llm.contracts import (
    ContractValidationError,
    ContractWarning,
    validation_diagnostics,
)
from exp2res.llm.fact_extractor import DisplacedSupportDescriptor
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.llm.signal_extractor import (
    SIGNAL_EXTRACTOR_CONTRACT,
    SignalExtractorInput,
    SignalExtractorOutput,
)
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    insert_self_signal,
    list_assessment_snapshots,
    list_contradictions,
    list_experience_facts,
    list_self_signals,
    list_self_claims_for_snapshot,
    mark_assessment_snapshots_superseded,
    mark_self_signals_superseded,
    mark_self_claims_superseded,
)
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, writer_database

from .orchestration import PlannedCall, run_complete_stage
from .evidence_context import project_evidence_context


@dataclass(frozen=True)
class Stage5Result:
    run_id: str
    created_signal_ids: tuple[str, ...]
    superseded_signal_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    superseded_snapshot_ids: tuple[str, ...]
    invalidated_views: tuple[InvalidatedView, ...]
    residual_paths: tuple[str, ...]
    generation_id: str | None
    superseded_generation_ids: tuple[str, ...]
    warnings: tuple[ContractWarning, ...]
    current_signals: tuple[SelfSignal, ...]


@dataclass(frozen=True)
class _ResolvedSignals:
    signals: tuple[SelfSignal, ...]
    warnings: tuple[ContractWarning, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _diagnostic(
    *, collection: str, index: int, field: str, kind: str
) -> dict[str, object]:
    return {"loc": (collection, index, field), "type": kind}


def _enrich_for(
    input_payload: SignalExtractorInput,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    fact_by_id = {fact.id: fact for fact in input_payload.facts}

    def enrich(decoded: dict[str, Any]) -> dict[str, Any]:
        try:
            output = SignalExtractorOutput.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
            )
        except ValidationError as error:
            raise ContractValidationError(
                validation_diagnostics(SIGNAL_EXTRACTOR_CONTRACT, error.errors())
            ) from None

        errors: list[dict[str, object]] = []
        for index, candidate in enumerate(output.signals):
            missing = False
            for field in ("supporting_fact_ids", "counter_fact_ids"):
                for member_index, fact_id in enumerate(getattr(candidate, field)):
                    if fact_id not in fact_by_id:
                        errors.append(
                            {
                                "loc": ("signals", index, field, member_index),
                                "type": "out_of_context_target",
                            }
                        )
                        missing = True
            if missing:
                continue
            supporting = [fact_by_id[item] for item in candidate.supporting_fact_ids]
            cap = signal_confidence_cap(
                supporting_confidences=(fact.confidence for fact in supporting),
                distinct_source_log_count=len(
                    {
                        source_log_id
                        for fact in supporting
                        for source_log_id in fact.source_log_ids
                    }
                ),
                has_counter_facts=bool(candidate.counter_fact_ids),
            )
            if confidence_exceeds(candidate.confidence, cap):
                errors.append(
                    _diagnostic(
                        collection="signals",
                        index=index,
                        field="confidence",
                        kind="confidence_above_cap",
                    )
                )

        if errors:
            raise ContractValidationError(
                validation_diagnostics(SIGNAL_EXTRACTOR_CONTRACT, errors)
            )
        return decoded

    return enrich


def _resolve_for(
    *,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> Callable[[BaseModel], object]:
    def resolve(validated: BaseModel) -> object:
        output = cast(SignalExtractorOutput, validated)
        signals = tuple(
            SelfSignal(
                id=id_factory("signal"),
                created_at=clock(),
                superseded_at=None,
                signal_type=candidate.signal_type,
                statement=candidate.statement,
                supporting_fact_ids=sorted(
                    candidate.supporting_fact_ids, key=_id_key
                ),
                counter_fact_ids=sorted(candidate.counter_fact_ids, key=_id_key),
                confidence=candidate.confidence,
                metadata={},
            )
            for candidate in output.signals
        )
        return _ResolvedSignals(signals, tuple(output.warnings))

    return resolve


def _evidence_context(
    connection: sqlite3.Connection, facts: Sequence[ExperienceFact]
) -> tuple[EvidenceItem | DisplacedSupportDescriptor, ...]:
    return project_evidence_context(
        connection, facts, missing_diagnostic="signal_evidence_missing"
    )


def run_signal_generation(
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
) -> Stage5Result:
    """Run the one-call complete Stage 5 candidate and atomic replacement."""

    now = clock or (lambda: datetime.now(timezone.utc))
    with writer_database(
        workspace, timeout_ms=timeout_ms, reconcile=True
    ) as connection:
        facts = tuple(
            sorted(list_experience_facts(connection), key=lambda fact: _id_key(fact.id))
        )
        evidence_items = _evidence_context(connection, facts)
        contradictions = tuple(
            sorted(
                list_contradictions(connection),
                key=lambda contradiction: _id_key(contradiction.id),
            )
        )
        input_payload = SignalExtractorInput(
            facts=list(facts),
            evidence_items=list(evidence_items),
            contradictions=list(contradictions),
        )
        run_id = id_factory("run")
        input_ids = tuple(
            sorted(
                {
                    *(fact.id for fact in facts),
                    *(item.id for item in evidence_items),
                    *(item.id for item in contradictions),
                },
                key=_id_key,
            )
        )
        planned = (
            ()
            if not facts and not contradictions
            else (
                PlannedCall(
                    input_payload=input_payload,
                    input_ids=input_ids,
                    enrich=_enrich_for(input_payload),
                    resolve=_resolve_for(id_factory=id_factory, clock=now),
                ),
            )
        )

        created_signal_ids: tuple[str, ...] = ()
        superseded_signal_ids: tuple[str, ...] = ()
        superseded_claim_ids: tuple[str, ...] = ()
        superseded_snapshot_ids: tuple[str, ...] = ()
        invalidated_views: tuple[InvalidatedView, ...] = ()
        generation_id: str | None = None
        superseded_generation_ids: set[str] = set()

        def commit(
            held: sqlite3.Connection, resolved: Sequence[object]
        ) -> Iterable[str]:
            nonlocal created_signal_ids, superseded_signal_ids, generation_id
            nonlocal superseded_claim_ids, superseded_snapshot_ids, invalidated_views

            candidate = cast(_ResolvedSignals, resolved[0])
            current = list_self_signals(held)
            superseded_signal_ids = tuple(signal.id for signal in current)
            if superseded_signal_ids:
                placeholders = ",".join("?" for _ in superseded_signal_ids)
                superseded_generation_ids.update(
                    row[0]
                    for row in held.execute(
                        "SELECT DISTINCT generation_id FROM self_signals "
                        f"WHERE id IN ({placeholders})",
                        superseded_signal_ids,
                    )
                )
            swap_time = now()
            mark_self_signals_superseded(held, superseded_signal_ids, swap_time)
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
            mark_self_claims_superseded(held, superseded_claim_ids, swap_time)
            mark_assessment_snapshots_superseded(
                held, superseded_snapshot_ids, swap_time
            )
            generation_id = id_factory("gen")
            for signal in candidate.signals:
                insert_self_signal(
                    held,
                    signal,
                    produced_by_run_id=run_id,
                    generation_id=generation_id,
                )
            # §13.5: claim/snapshot/branch/bullet supersession joins this
            # transaction when those upper-layer tables land (Stage 6+).
            created_signal_ids = tuple(
                sorted((signal.id for signal in candidate.signals), key=_id_key)
            )
            return created_signal_ids

        outcome = run_complete_stage(
            workspace,
            connection,
            stage="13.5",
            contract=SIGNAL_EXTRACTOR_CONTRACT,
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
        residual_paths = remove_assessment_sets(
            workspace, superseded_snapshot_ids
        )
        current_signals = tuple(
            sorted(list_self_signals(connection), key=lambda signal: _id_key(signal.id))
        )

    resolved = tuple(cast(_ResolvedSignals, item) for item in outcome.resolved)
    return Stage5Result(
        run_id=run_id,
        created_signal_ids=created_signal_ids,
        superseded_signal_ids=tuple(
            sorted(superseded_signal_ids, key=_id_key)
        ),
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
        current_signals=current_signals,
    )
