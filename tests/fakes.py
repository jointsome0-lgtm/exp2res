"""Deterministic offline runner implementing the production process seam."""

from __future__ import annotations

from collections import deque
from typing import Callable, Iterable

from exp2res.llm.runner import AttemptTelemetry, PreparedCall, RawResult


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
