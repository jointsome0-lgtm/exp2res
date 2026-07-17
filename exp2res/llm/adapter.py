"""Provider-neutral §15.1 invocation orchestration and telemetry."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import random
import sqlite3
import time
from typing import Callable, Iterable, Pattern
import uuid

from pydantic import BaseModel

from exp2res.domain.canonical import canonical_model_hash
from exp2res.errors import LLMCancelledError, LLMInvocationError
from exp2res.storage.telemetry import (
    create_llm_call,
    create_processing_run,
    finish_llm_call,
    finish_processing_run,
    increment_call_retry,
    merge_run_metadata,
    require_running_run,
)
from exp2res.storage.workspace import writer_database

from .contracts import (
    ContractDefinition,
    ContractValidationError,
    ServiceEnrichmentError,
    prompt_policy_hash,
    runner_instruction,
    schema_bytes,
    validate_output,
)
from .preflight import estimate_tokens, preflight_call
from .registry import LLMSelection, registration_for
from .runner import CallBudgets, ContractRunner, PreparedCall, RawResult


SANDBOX_MECHANISM = "bwrap"


@dataclass(frozen=True)
class InvocationResult:
    output: BaseModel
    output_bytes: bytes
    run_id: str
    call_index: int


def logical_correlation() -> str:
    """Return adapter-owned logical metadata, not a provider request ID."""

    return f"logical_{uuid.uuid4().hex}"


def _transaction(
    connection: sqlite3.Connection, operation: Callable[[object], None]
) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        operation(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def invoke_contract(
    *,
    workspace: Path,
    runner: ContractRunner,
    contract: ContractDefinition,
    input_payload: BaseModel,
    selection: LLMSelection,
    budgets: CallBudgets,
    run_id: str,
    stage: str,
    call_index: int = 1,
    finish_run: bool = True,
    cli_version: str = "test-double",
    input_ids: Iterable[str] = (),
    output_ids: Iterable[str] = (),
    enrich: Callable[[dict[str, object]], dict[str, object]] | None = None,
    persist_validated: (
        Callable[[BaseModel, sqlite3.Connection], Iterable[str]] | None
    ) = None,
    capability_check: Callable[[], None] | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] | None = None,
    resolved_credentials: Iterable[bytes] = (),
    connection: sqlite3.Connection | None = None,
) -> InvocationResult:
    """Run, validate once plus one diagnostic-only retry, and persist telemetry.

    The invocation holds business-writer authority door-to-door: either the
    caller passes its held writer `connection` (a multi-call stage keeps one
    across its planned invocations and owns rule 8 reconciliation at its own
    entry), or this function acquires the workspace writer seam for the whole
    invocation, so a nonterminal telemetry row visible to any other lock
    holder always belongs to a dead writer (§8.1, §15.10 rule 8).
    """

    if connection is None:
        # Reconcile only before this run's row exists; a later call index
        # must never sweep the run it belongs to.
        with writer_database(workspace, reconcile=(call_index == 1)) as held:
            return invoke_contract(
                workspace=workspace,
                runner=runner,
                contract=contract,
                input_payload=input_payload,
                selection=selection,
                budgets=budgets,
                run_id=run_id,
                stage=stage,
                call_index=call_index,
                finish_run=finish_run,
                cli_version=cli_version,
                input_ids=input_ids,
                output_ids=output_ids,
                enrich=enrich,
                persist_validated=persist_validated,
                capability_check=capability_check,
                clock=clock,
                monotonic=monotonic,
                sleeper=sleeper,
                jitter=jitter,
                token_patterns=token_patterns,
                resolved_credentials=resolved_credentials,
                connection=held,
            )

    writer = connection
    registration = registration_for(selection)
    # §11's datetime normalization governs hash bytes only: the provider
    # sees the declared typed input with its offsets preserved, while the
    # §12.15 hashes stay canonical for exact-recomputation identity.
    serialized_input = input_payload.model_dump_json().encode("utf-8")
    input_hash = canonical_model_hash(input_payload)
    now = clock or (lambda: datetime.now(timezone.utc))
    random_jitter = jitter or random.SystemRandom().uniform
    registered_patterns = tuple(
        registration.declaration.token_patterns
        if token_patterns is None
        else token_patterns
    )
    credential_values = tuple(resolved_credentials)
    schema = schema_bytes(contract)
    policy_hash = prompt_policy_hash(contract)
    prepared = PreparedCall(
        contract_id=contract.contract_id,
        serialized_input=serialized_input,
        json_schema=schema,
        model_id=selection.model,
        fixed_instruction=runner_instruction(contract),
        budgets=budgets,
    )
    provider_request_id = logical_correlation()
    started_at = now()
    initial_metadata = {
        "cli_version": cli_version,
        "contract_id": contract.contract_id,
        "adapter_id": selection.adapter,
        "runner_protocol_version": str(
            registration.declaration.runner_protocol_version
        ),
        "sandbox_mechanism": SANDBOX_MECHANISM,
        "schema_hash": hashlib.sha256(schema).hexdigest(),
    }

    def create_rows(connection: object) -> None:
        if call_index == 1:
            create_processing_run(
                connection,  # type: ignore[arg-type]
                run_id=run_id,
                stage=stage,
                started_at=started_at,
                provider=selection.adapter,
                model=selection.model,
                prompt_policy_hash=policy_hash,
                input_ids=input_ids,
                metadata=initial_metadata,
            )
        else:
            # §12.15: every call in a run shares one execution configuration;
            # a later index appends to the existing running run, never a
            # second processing_runs row.
            require_running_run(
                connection,  # type: ignore[arg-type]
                run_id=run_id,
                provider=selection.adapter,
                model=selection.model,
                prompt_policy_hash=policy_hash,
            )
        create_llm_call(
            connection,  # type: ignore[arg-type]
            run_id=run_id,
            call_index=call_index,
            started_at=started_at,
            input_hash=input_hash,
            provider_request_id=provider_request_id,
        )

    _transaction(writer, create_rows)
    total_duration = 0.0
    last_exit_code: int | None = None

    def terminal_metadata() -> dict[str, str]:
        # §15.12 rule 9: by terminal time a materialized runner knows the
        # probed runtime version, which is the runner identity; the probe
        # is non-materializing, so a run whose adapter build failed keeps
        # its durable rows (§24.46) and the caller-supplied placeholder.
        version_probe = getattr(runner, "runtime_version", None)
        probed = version_probe() if callable(version_probe) else None
        return {
            **initial_metadata,
            **(
                {"cli_version": probed}
                if isinstance(probed, str) and probed
                else {}
            ),
            f"call_{call_index}_duration_ms": str(
                max(0, round(total_duration * 1000))
            ),
            f"call_{call_index}_exit_code": (
                "none" if last_exit_code is None else str(last_exit_code)
            ),
        }

    def fail(code: str) -> None:
        def finish(connection: object) -> None:
            finish_llm_call(
                connection,  # type: ignore[arg-type]
                run_id=run_id,
                call_index=call_index,
                finished_at=now(),
                status="failed",
                failure_code=code,
            )
            finish_processing_run(
                connection,  # type: ignore[arg-type]
                run_id=run_id,
                finished_at=now(),
                status="failed",
                failure_code=code,
                metadata=terminal_metadata(),
            )

        _transaction(writer, finish)

    def run_rounds() -> InvocationResult:
        nonlocal total_duration, last_exit_code, prepared

        try:
            if capability_check is not None:
                capability_check()
            preflight_call(
                prepared,
                token_patterns=registered_patterns,
                resolved_credentials=credential_values,
            )
        except LLMInvocationError as error:
            fail(error.failure_code)
            if error.failure_code == "cancelled":
                raise LLMCancelledError() from None
            raise

        deadline = monotonic() + budgets.invocation_deadline_seconds
        validation_round = 0
        while validation_round < 2:
            physical_attempt = 0
            result: RawResult | None = None
            while physical_attempt < budgets.transport_attempt_cap:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    fail("transport_timeout")
                    raise LLMInvocationError("transport_timeout")
                attempt_call = replace(prepared, timeout_seconds=remaining)
                try:
                    preflight_call(
                        attempt_call,
                        token_patterns=registered_patterns,
                        resolved_credentials=credential_values,
                    )
                    result = runner.run_contract(attempt_call)
                except LLMInvocationError as error:
                    fail(error.failure_code)
                    raise
                except OSError:
                    result = RawResult(
                        None, None, 0.0, (), b"connection failure", False, False
                    )
                physical_attempt += 1
                total_duration += result.duration_seconds
                last_exit_code = result.exit_code
                code, retryable = registration.classify_failure(result)
                if code is None:
                    break
                if code == "cancelled":
                    fail(code)
                    raise LLMCancelledError() from None
                if retryable and physical_attempt < budgets.transport_attempt_cap:
                    _transaction(
                        writer,
                        lambda connection: increment_call_retry(
                            connection,  # type: ignore[arg-type]
                            run_id=run_id,
                            call_index=call_index,
                            retry_kind="transport",
                        ),
                    )
                    delay = random_jitter(
                        budgets.backoff_lower_seconds, budgets.backoff_upper_seconds
                    )
                    if monotonic() + delay >= deadline:
                        fail("transport_timeout")
                        raise LLMInvocationError("transport_timeout")
                    sleeper(delay)
                    continue
                fail(code)
                raise LLMInvocationError(code)

            if result is None or result.final_message_bytes is None:
                fail("transport_provider_error")
                raise LLMInvocationError("transport_provider_error")
            try:
                validated = validate_output(
                    contract,
                    result.final_message_bytes,
                    enrich=enrich,  # type: ignore[arg-type]
                )
            except ContractValidationError as error:
                if validation_round == 1:
                    fail("response_validation_failed")
                    raise LLMInvocationError("response_validation_failed") from None
                _transaction(
                    writer,
                    lambda connection: increment_call_retry(
                        connection,  # type: ignore[arg-type]
                        run_id=run_id,
                        call_index=call_index,
                        retry_kind="schema",
                    ),
                )
                validation_round = 1
                prepared = replace(
                    prepared,
                    validation_errors=error.diagnostics,
                    validation_round=1,
                )
                continue
            except ServiceEnrichmentError:
                fail("deterministic_enrichment_failed")
                raise LLMInvocationError("deterministic_enrichment_failed") from None

            output = result.final_message_bytes
            output_hash = canonical_model_hash(validated)
            committed_output_ids = list(output_ids)

            def complete(connection: object) -> None:
                # §15.10 rule 7: validated business persistence and this call's
                # terminal telemetry commit or roll back as one unit, so a later
                # failed row can never leave an earlier row durable.
                if persist_validated is not None:
                    committed_output_ids[:] = persist_validated(
                        validated,
                        connection,  # type: ignore[arg-type]
                    )
                finish_llm_call(
                    connection,  # type: ignore[arg-type]
                    run_id=run_id,
                    call_index=call_index,
                    finished_at=now(),
                    status="completed",
                    output_hash=output_hash,
                    prompt_tokens=estimate_tokens(serialized_input),
                    completion_tokens=estimate_tokens(output),
                )
                if finish_run:
                    finish_processing_run(
                        connection,  # type: ignore[arg-type]
                        run_id=run_id,
                        finished_at=now(),
                        status="completed",
                        output_ids=committed_output_ids,
                        metadata=terminal_metadata(),
                    )
                else:
                    # §15.10 rule 7: a multi-call stage finishes its one run only
                    # after every planned invocation validates; this call records
                    # its telemetry and leaves the run running.
                    merge_run_metadata(
                        connection,  # type: ignore[arg-type]
                        run_id=run_id,
                        metadata=terminal_metadata(),
                    )

            try:
                _transaction(writer, complete)
            except Exception:
                def fail_business(connection: object) -> None:
                    finish_llm_call(
                        connection,  # type: ignore[arg-type]
                        run_id=run_id,
                        call_index=call_index,
                        finished_at=now(),
                        status="completed",
                        output_hash=output_hash,
                        prompt_tokens=estimate_tokens(serialized_input),
                        completion_tokens=estimate_tokens(output),
                    )
                    finish_processing_run(
                        connection,  # type: ignore[arg-type]
                        run_id=run_id,
                        finished_at=now(),
                        status="failed",
                        failure_code="business_commit_failed",
                        metadata=terminal_metadata(),
                    )

                _transaction(writer, fail_business)
                raise LLMInvocationError("business_commit_failed") from None
            return InvocationResult(validated, output, run_id, call_index)
        raise AssertionError("unreachable validation loop")

    try:
        return run_rounds()
    except KeyboardInterrupt:
        # §15.10 rule 8: an owner interrupt anywhere in the foreground
        # invocation — transport, backoff, validation, or the business
        # commit phase — records cancelled terminal rows before exit.
        try:
            fail("cancelled")
        except Exception:
            # Best effort under a second interrupt or already-terminal
            # rows; next-writer reconciliation remains the backstop.
            pass
        raise LLMCancelledError() from None
