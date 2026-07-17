"""Reusable complete-stage orchestration for §15.10 multi-call stages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence

from pydantic import BaseModel

from exp2res.errors import LLMCancelledError, LLMInvocationError
from exp2res.llm.adapter import invoke_contract
from exp2res.llm.contracts import ContractDefinition, prompt_policy_hash
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.storage.telemetry import create_processing_run, finish_processing_run


@dataclass(frozen=True)
class PlannedCall:
    input_payload: BaseModel
    input_ids: tuple[str, ...]
    enrich: Callable[[dict[str, Any]], dict[str, Any]] | None
    resolve: Callable[[BaseModel], object]


@dataclass(frozen=True)
class StageOutcome:
    run_id: str
    output_ids: tuple[str, ...]
    resolved: tuple[object, ...]


def _transaction(
    connection: sqlite3.Connection, operation: Callable[[], object]
) -> object:
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = operation()
        connection.commit()
        return result
    except BaseException:
        connection.rollback()
        raise


def _finish_if_running(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    finished_at: datetime,
    failure_code: str,
) -> None:
    row = connection.execute(
        "SELECT status FROM processing_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None or row[0] in {"completed", "failed"}:
        return
    finish_processing_run(
        connection,
        run_id=run_id,
        finished_at=finished_at,
        status="failed",
        failure_code=failure_code,
    )


def run_complete_stage(
    workspace: Path,
    connection: sqlite3.Connection,
    *,
    stage: str,
    contract: ContractDefinition,
    selection: LLMSelection,
    budgets: CallBudgets,
    runner: ContractRunner,
    planned: Sequence[PlannedCall],
    commit: Callable[[sqlite3.Connection, Sequence[object]], Iterable[str]],
    run_id: str,
    clock: Callable[[], datetime] | None = None,
    cli_version: str = "test-double",
    capability_check: Callable[[], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] | None = None,
    resolved_credentials: Iterable[bytes] = (),
) -> StageOutcome:
    """Validate every planned call before one caller-defined business swap.

    The caller must hold ``writer_database(..., reconcile=True)`` for the
    lifetime of this call. This module owns the single final transaction that
    couples the business replacement to the run's completed transition.
    """

    now = clock or (lambda: datetime.now(timezone.utc))
    adjusted_budgets = replace(budgets, planned_call_count=len(planned))
    input_ids = tuple(
        sorted(
            {item for call in planned for item in call.input_ids},
            key=lambda value: value.encode("utf-8"),
        )
    )

    if not planned:

        def complete_empty() -> None:
            started_at = now()
            create_processing_run(
                connection,
                run_id=run_id,
                stage=stage,
                started_at=started_at,
                provider=selection.adapter,
                model=selection.model,
                prompt_policy_hash=prompt_policy_hash(contract),
                input_ids=(),
                metadata={
                    "adapter_id": selection.adapter,
                    "contract_id": contract.contract_id,
                },
            )
            finish_processing_run(
                connection,
                run_id=run_id,
                finished_at=now(),
                status="completed",
                output_ids=(),
            )

        try:
            _transaction(connection, complete_empty)
        except KeyboardInterrupt:
            try:
                _transaction(
                    connection,
                    lambda: _finish_if_running(
                        connection,
                        run_id=run_id,
                        finished_at=now(),
                        failure_code="cancelled",
                    ),
                )
            except Exception:
                pass
            raise LLMCancelledError() from None
        return StageOutcome(run_id, (), ())

    resolved: list[object] = []
    try:
        for call_index, call in enumerate(planned, start=1):
            invocation = invoke_contract(
                workspace=workspace,
                runner=runner,
                contract=contract,
                input_payload=call.input_payload,
                selection=selection,
                budgets=adjusted_budgets,
                run_id=run_id,
                stage=stage,
                call_index=call_index,
                finish_run=False,
                cli_version=cli_version,
                input_ids=input_ids if call_index == 1 else (),
                enrich=call.enrich,
                persist_validated=None,
                capability_check=capability_check,
                clock=now,
                monotonic=monotonic,
                sleeper=sleeper,
                jitter=jitter,
                token_patterns=token_patterns,
                resolved_credentials=resolved_credentials,
                connection=connection,
            )
            try:
                resolved.append(call.resolve(invocation.output))
            except KeyboardInterrupt:
                raise
            except Exception:
                _transaction(
                    connection,
                    lambda: _finish_if_running(
                        connection,
                        run_id=run_id,
                        finished_at=now(),
                        failure_code="deterministic_enrichment_failed",
                    ),
                )
                raise LLMInvocationError("deterministic_enrichment_failed") from None

        try:

            def commit_complete() -> tuple[str, ...]:
                output_ids = tuple(commit(connection, tuple(resolved)))
                finish_processing_run(
                    connection,
                    run_id=run_id,
                    finished_at=now(),
                    status="completed",
                    output_ids=output_ids,
                )
                return output_ids

            output_ids = _transaction(connection, commit_complete)
            assert isinstance(output_ids, tuple)
        except KeyboardInterrupt:
            raise
        except Exception:
            _transaction(
                connection,
                lambda: _finish_if_running(
                    connection,
                    run_id=run_id,
                    finished_at=now(),
                    failure_code="business_commit_failed",
                ),
            )
            raise LLMInvocationError("business_commit_failed") from None
    except KeyboardInterrupt:
        try:
            _transaction(
                connection,
                lambda: _finish_if_running(
                    connection,
                    run_id=run_id,
                    finished_at=now(),
                    failure_code="cancelled",
                ),
            )
        except Exception:
            pass
        raise LLMCancelledError() from None

    return StageOutcome(run_id, output_ids, tuple(resolved))
