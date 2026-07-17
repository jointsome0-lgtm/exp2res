"""Generic foreground runner for isolated native-schema CLI calls."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
from typing import BinaryIO, Protocol, Sequence


@dataclass(frozen=True)
class CallBudgets:
    """All local bounds needed before and during one logical invocation."""

    transport_attempt_cap: int
    backoff_lower_seconds: float
    backoff_upper_seconds: float
    invocation_deadline_seconds: float
    max_input_bytes: int
    input_token_budget: int
    output_token_budget: int
    planned_output_tokens: int
    model_context_tokens: int
    model_max_output_tokens: int
    per_run_call_ceiling: int
    planned_call_count: int = 1
    per_invocation_cost_ceiling: Decimal | None = None
    per_run_cost_ceiling: Decimal | None = None
    input_cost_per_million: Decimal | None = None
    output_cost_per_million: Decimal | None = None


@dataclass(frozen=True)
class PreparedCall:
    """Serialized contract material crossing the process seam."""

    contract_id: str
    serialized_input: bytes
    json_schema: bytes
    model_id: str
    fixed_instruction: str
    budgets: CallBudgets
    validation_errors: bytes | None = None
    validation_round: int = 0
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class AttemptTelemetry:
    attempt_index: int
    exit_code: int | None
    duration_seconds: float
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class RawResult:
    """Content bytes plus non-content process telemetry returned by a runner."""

    final_message_bytes: bytes | None
    exit_code: int | None
    duration_seconds: float
    attempts: tuple[AttemptTelemetry, ...]
    error_channel: bytes = b""
    timed_out: bool = False
    cancelled: bool = False
    api_error_status: int | None = None


class ContractRunner(Protocol):
    def run_contract(self, call: PreparedCall) -> RawResult:
        """Run one physical request attempt."""


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int | None
    duration_seconds: float
    error_channel: bytes
    timed_out: bool
    cancelled: bool


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    # The group is always SIGKILLed after the grace period: a leader that
    # exits first must not shield a slow or TERM-ignoring descendant from
    # the foreground deadline/cancellation guarantee.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()


def run_subprocess(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    stdout_descriptor: int | BinaryIO | None = None,
) -> ProcessOutcome:
    """Run one closed-stdin process group and kill the whole group on stop."""

    if timeout_seconds <= 0:
        return ProcessOutcome(None, 0.0, b"", True, False)
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=(
            subprocess.DEVNULL
            if stdout_descriptor is None
            else stdout_descriptor
        ),
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    cancelled = False
    error_channel = b""
    try:
        _, error_channel = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        _, error_channel = process.communicate()
    except KeyboardInterrupt:
        cancelled = True
        _terminate_process_group(process)
        _, error_channel = process.communicate()
    duration = time.monotonic() - started
    return ProcessOutcome(
        None if timed_out or cancelled else process.returncode,
        duration,
        error_channel[-65_536:],
        timed_out,
        cancelled,
    )


def _write_private(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short isolated-workspace write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def _read_output(path: Path) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16_777_216:
                return None
            chunks: list[bytes] = []
            remaining = 16_777_217
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            output = b"".join(chunks)
            return output if len(output) <= 16_777_216 else None
        finally:
            os.close(descriptor)
    except OSError:
        return None


def run_contract(runner: ContractRunner, call: PreparedCall) -> RawResult:
    """Stable functional spelling of the runner seam."""

    return runner.run_contract(call)
