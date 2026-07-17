"""Deterministic offline runner implementing the production process seam."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import time
from typing import Callable, Iterable

from exp2res.llm.runner import (
    AttemptTelemetry,
    PreparedCall,
    RawResult,
    run_subprocess,
)


FakeResult = RawResult | bytes | Callable[[PreparedCall], RawResult]


class FakeContractRunner:
    """Return canned final-message bytes or process outcomes in call order."""

    def __init__(self, results: Iterable[FakeResult]) -> None:
        self._results = deque(results)
        self.calls: list[PreparedCall] = []

    def run_contract(self, call: PreparedCall) -> RawResult:
        self.calls.append(call)
        if not self._results:
            raise AssertionError("fake runner exhausted")
        result = self._results.popleft()
        if callable(result):
            return result(call)
        if isinstance(result, bytes):
            return RawResult(
                final_message_bytes=result,
                exit_code=0,
                duration_seconds=0.01,
                attempts=(AttemptTelemetry(1, 0, 0.01),),
            )
        return result


def assert_timeout_kills_process_group(tmp_path: Path) -> None:
    """Exercise the shared process-group deadline contract."""

    pid_path = tmp_path / "Vera Example child.pid"
    outcome = run_subprocess(
        [
            "/usr/bin/sh",
            "-c",
            f"sleep 30 & child=$!; echo $child > '{pid_path}'; wait",
        ],
        timeout_seconds=0.1,
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{child_pid}").exists()
