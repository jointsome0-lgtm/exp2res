"""Deterministic offline runner implementing the production process seam."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
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


def raise_interrupt(*_arguments, **_keywords) -> None:
    """Stand in for a SIGINT landing at the patched call."""

    raise KeyboardInterrupt()


class CannedRows:
    """A cursor stand-in returning fixed rows."""

    def __init__(self, *rows: tuple) -> None:
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class ProxiedConnection:
    """Forward to a real connection; let a test intercept statements and the commit return.

    `on_statement(sql)` runs before each `execute`; it may raise, or return a
    cursor stand-in that replaces the statement. `after_commit()` runs once the
    real commit has returned — the §14.14 rule 6 "interrupt as `commit()`
    returns" point.
    """

    def __init__(
        self,
        connection,
        *,
        on_statement: Callable[[str], object | None] | None = None,
        after_commit: Callable[[], None] | None = None,
    ) -> None:
        self._connection = connection
        self._on_statement = on_statement
        self._after_commit = after_commit

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def execute(self, sql: str, *arguments):
        if self._on_statement is not None:
            replacement = self._on_statement(sql)
            if replacement is not None:
                return replacement
        return self._connection.execute(sql, *arguments)

    def commit(self) -> None:
        self._connection.commit()
        if self._after_commit is not None:
            self._after_commit()


def proxy_writer(
    monkeypatch,
    module,
    *,
    on_statement: Callable[[str], object | None] | None = None,
    after_commit: Callable[[], None] | None = None,
    after_teardown: Callable[[], None] | None = None,
) -> None:
    """Replace `module.writer_database` with one yielding a `ProxiedConnection`.

    `after_teardown()` runs once the real writer has released its lock and
    connection — the teardown point of the cancellation boundary.
    """

    real_writer = module.writer_database

    @contextmanager
    def wrapped(target: Path, **keywords):
        with real_writer(target, **keywords) as connection:
            yield ProxiedConnection(
                connection, on_statement=on_statement, after_commit=after_commit
            )
        if after_teardown is not None:
            after_teardown()

    monkeypatch.setattr(module, "writer_database", wrapped)
