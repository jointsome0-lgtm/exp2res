"""Codex capability declaration, failure mapping, and §15.1 orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import random
import re
import shutil
import stat
import subprocess
import time
from typing import Callable, Iterable, Pattern
import uuid

from pydantic import BaseModel

from exp2res.errors import LLMCancelledError, LLMInvocationError
from exp2res.storage.telemetry import (
    create_llm_call,
    create_processing_run,
    finish_llm_call,
    finish_processing_run,
    increment_call_retry,
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
from .preflight import CODEX_TOKEN_PATTERNS, estimate_tokens, preflight_call
from .runner import CallBudgets, ContractRunner, PreparedCall, RawResult
from .sandbox import discover_bwrap, probe_isolation


RUNNER_ID = "codex-cli"
REQUIRED_FLAGS = frozenset(
    {
        "--output-schema",
        "--output-last-message",
        "--ephemeral",
        "--ignore-user-config",
        "-C",
        "--skip-git-repo-check",
    }
)
@dataclass(frozen=True)
class CLITestRange:
    minimum: tuple[int, int, int]
    maximum_exclusive: tuple[int, int, int]
    supported_flags: frozenset[str]


@dataclass(frozen=True)
class CodexCapabilityDeclaration:
    required_flags: frozenset[str]
    tested_ranges: tuple[CLITestRange, ...]
    token_patterns: tuple[Pattern[bytes], ...]
    timeout_supported: bool = True
    cancellation_supported: bool = True


DEFAULT_DECLARATION = CodexCapabilityDeclaration(
    required_flags=REQUIRED_FLAGS,
    tested_ranges=(
        CLITestRange(
            minimum=(0, 144, 0),
            maximum_exclusive=(0, 145, 0),
            supported_flags=REQUIRED_FLAGS,
        ),
    ),
    token_patterns=CODEX_TOKEN_PATTERNS,
)


@dataclass(frozen=True)
class AdapterRuntime:
    codex_binary: Path
    bwrap_binary: Path
    codex_home: Path
    cli_version: str


@dataclass(frozen=True)
class InvocationResult:
    output: BaseModel
    output_bytes: bytes
    run_id: str
    call_index: int


def parse_codex_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", value)
    if match is None:
        raise LLMInvocationError("capability_mismatch")
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def validate_cli_declaration(
    version_output: str,
    declaration: CodexCapabilityDeclaration = DEFAULT_DECLARATION,
) -> str:
    """Validate local CLI capability from version plus the tested-range table."""

    if (
        not declaration.timeout_supported
        or not declaration.cancellation_supported
        or not declaration.token_patterns
    ):
        raise LLMInvocationError("capability_mismatch")
    version = parse_codex_version(version_output)
    for tested in declaration.tested_ranges:
        if tested.minimum <= version < tested.maximum_exclusive:
            if not declaration.required_flags.issubset(tested.supported_flags):
                raise LLMInvocationError("capability_mismatch")
            return ".".join(str(item) for item in version)
    raise LLMInvocationError("capability_mismatch")


def _resolve_executable(value: str | Path | None, fallback: str) -> Path:
    candidate = str(value) if value is not None else shutil.which(fallback)
    if candidate is None:
        raise LLMInvocationError("capability_mismatch")
    try:
        resolved = Path(candidate).resolve(strict=True)
    except OSError:
        raise LLMInvocationError("capability_mismatch") from None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise LLMInvocationError("capability_mismatch")
    return resolved


def _resolve_codex_binary(value: str | Path | None) -> Path:
    """Resolve an npm launcher to its platform-native executable fail-closed."""

    launcher = _resolve_executable(value, "codex")
    try:
        with launcher.open("rb") as stream:
            header = stream.read(4)
    except OSError:
        raise LLMInvocationError("capability_mismatch") from None
    if header == b"\x7fELF" or header in {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    }:
        return launcher
    if launcher.name != "codex.js":
        raise LLMInvocationError("capability_mismatch")
    target = {
        ("Linux", "x86_64"): ("codex-linux-x64", "x86_64-unknown-linux-musl"),
        ("Linux", "aarch64"): ("codex-linux-arm64", "aarch64-unknown-linux-musl"),
        ("Darwin", "x86_64"): ("codex-darwin-x64", "x86_64-apple-darwin"),
        ("Darwin", "arm64"): ("codex-darwin-arm64", "aarch64-apple-darwin"),
    }.get((platform.system(), platform.machine()))
    if target is None:
        raise LLMInvocationError("capability_mismatch")
    package_name, target_triple = target
    package_root = launcher.parent.parent
    candidates = (
        package_root
        / "node_modules"
        / "@openai"
        / package_name
        / "vendor"
        / target_triple
        / "bin"
        / "codex",
        package_root / "vendor" / target_triple / "bin" / "codex",
    )
    for candidate in candidates:
        try:
            native = candidate.resolve(strict=True)
        except OSError:
            continue
        if native.is_file() and os.access(native, os.X_OK):
            return native
    raise LLMInvocationError("capability_mismatch")


def _preflight_auth(codex_home: Path) -> None:
    auth = codex_home / "auth.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(auth, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
                raise OSError("invalid auth file")
        finally:
            os.close(descriptor)
        if auth.is_symlink():
            raise OSError("auth file must not be a symlink")
    except OSError:
        raise LLMInvocationError("transport_auth_failed") from None


def preflight_adapter(
    *,
    repository_root: Path,
    codex_home: Path,
    codex_binary: str | Path | None = None,
    bwrap_binary: str | Path | None = None,
    declaration: CodexCapabilityDeclaration = DEFAULT_DECLARATION,
    user_rules_path: Path | None = None,
) -> AdapterRuntime:
    """Fail closed on either the local CLI half or wrapper/canary half."""

    codex = _resolve_codex_binary(codex_binary)
    try:
        version_process = subprocess.run(
            [str(codex), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LLMInvocationError("capability_mismatch") from None
    if version_process.returncode != 0:
        raise LLMInvocationError("capability_mismatch")
    version = validate_cli_declaration(version_process.stdout, declaration)
    bwrap = discover_bwrap(None if bwrap_binary is None else Path(bwrap_binary))
    if bwrap is None:
        raise LLMInvocationError("capability_mismatch")
    canary = probe_isolation(
        repository_root=repository_root,
        bwrap_binary=bwrap,
        user_rules_path=user_rules_path,
    )
    if not canary.available or not canary.effective:
        raise LLMInvocationError("capability_mismatch")
    _preflight_auth(codex_home)
    return AdapterRuntime(codex, bwrap, codex_home, version)


def _transaction(workspace: Path, operation: Callable[[object], None]) -> None:
    with writer_database(workspace) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _failure_from_result(result: RawResult) -> tuple[str | None, bool]:
    if result.cancelled:
        return "cancelled", False
    if result.timed_out:
        return "transport_timeout", True
    if result.exit_code == 0 and result.final_message_bytes is not None:
        return None, False
    channel = result.error_channel.lower()
    if any(
        marker in channel
        for marker in (
            b"429",
            b"rate limit",
            b"rate_limit",
            b"too many requests",
            b"usage limit",
            b"quota exceeded",
        )
    ):
        return "transport_rate_limited", True
    if any(
        marker in channel
        for marker in (
            b"401",
            b"403",
            b"unauthorized",
            b"authentication",
            b"not logged in",
            b"login required",
            b"invalid api key",
        )
    ):
        return "transport_auth_failed", False
    if any(marker in channel for marker in (b"lost response", b"ambiguous delivery")):
        return "transport_lost_response", True
    retryable = any(
        marker in channel
        for marker in (b"connection", b"tls", b"overload", b"http 5", b" 500")
    )
    return "transport_provider_error", retryable


def invoke_contract(
    *,
    workspace: Path,
    runner: ContractRunner,
    contract: ContractDefinition,
    serialized_input: bytes,
    model_id: str,
    budgets: CallBudgets,
    run_id: str,
    stage: str,
    call_index: int = 1,
    cli_version: str = "test-double",
    input_ids: Iterable[str] = (),
    output_ids: Iterable[str] = (),
    enrich: Callable[[dict[str, object]], dict[str, object]] | None = None,
    persist_validated: Callable[[BaseModel], Iterable[str]] | None = None,
    capability_check: Callable[[], None] | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] = CODEX_TOKEN_PATTERNS,
    resolved_credentials: Iterable[bytes] = (),
) -> InvocationResult:
    """Run, validate once plus one diagnostic-only retry, and persist telemetry."""

    now = clock or (lambda: datetime.now(timezone.utc))
    random_jitter = jitter or random.SystemRandom().uniform
    registered_patterns = tuple(token_patterns)
    credential_values = tuple(resolved_credentials)
    schema = schema_bytes(contract)
    policy_hash = prompt_policy_hash(contract)
    prepared = PreparedCall(
        contract_id=contract.contract_id,
        serialized_input=serialized_input,
        json_schema=schema,
        model_id=model_id,
        fixed_instruction=runner_instruction(contract),
        budgets=budgets,
    )
    provider_request_id = f"req_{uuid.uuid4().hex}"
    started_at = now()
    initial_metadata = {
        "cli_version": cli_version,
        "contract_id": contract.contract_id,
        "runner_id": RUNNER_ID,
        "schema_hash": hashlib.sha256(schema).hexdigest(),
    }

    def create_rows(connection: object) -> None:
        create_processing_run(
            connection,  # type: ignore[arg-type]
            run_id=run_id,
            stage=stage,
            started_at=started_at,
            provider=RUNNER_ID,
            model=model_id,
            prompt_policy_hash=policy_hash,
            input_ids=input_ids,
            metadata=initial_metadata,
        )
        create_llm_call(
            connection,  # type: ignore[arg-type]
            run_id=run_id,
            call_index=call_index,
            started_at=started_at,
            input_bytes=serialized_input,
            provider_request_id=provider_request_id,
        )

    _transaction(workspace, create_rows)
    total_duration = 0.0
    last_exit_code: int | None = None

    def terminal_metadata() -> dict[str, str]:
        return {
            **initial_metadata,
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

        _transaction(workspace, finish)

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
            except KeyboardInterrupt:
                fail("cancelled")
                raise LLMCancelledError() from None
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
            code, retryable = _failure_from_result(result)
            if code is None:
                break
            if code == "cancelled":
                fail(code)
                raise LLMCancelledError() from None
            if retryable and physical_attempt < budgets.transport_attempt_cap:
                _transaction(
                    workspace,
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
                workspace,
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
        committed_output_ids = output_ids
        if persist_validated is not None:
            try:
                committed_output_ids = tuple(persist_validated(validated))
            except Exception:
                def fail_business(connection: object) -> None:
                    finish_llm_call(
                        connection,  # type: ignore[arg-type]
                        run_id=run_id,
                        call_index=call_index,
                        finished_at=now(),
                        status="completed",
                        output_bytes=output,
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

                _transaction(workspace, fail_business)
                raise LLMInvocationError("business_commit_failed") from None

        def complete(connection: object) -> None:
            completion_tokens = estimate_tokens(output)
            finish_llm_call(
                connection,  # type: ignore[arg-type]
                run_id=run_id,
                call_index=call_index,
                finished_at=now(),
                status="completed",
                output_bytes=output,
                prompt_tokens=estimate_tokens(serialized_input),
                completion_tokens=completion_tokens,
            )
            finish_processing_run(
                connection,  # type: ignore[arg-type]
                run_id=run_id,
                finished_at=now(),
                status="completed",
                output_ids=committed_output_ids,
                metadata=terminal_metadata(),
            )

        _transaction(workspace, complete)
        return InvocationResult(validated, output, run_id, call_index)
    raise AssertionError("unreachable validation loop")
