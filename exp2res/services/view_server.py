"""§14.17's loopback socket transport in front of §30's projections.

This module owns only the transport half of serving: which bind is admitted,
§30 rule 10's connection admission, rule 9's parsing through `view_http`,
rule 7's ordered pre-state refusals — authority, method, declared body — and
§14.17's absolute receive, processing, emit, and drain deadlines. Every
state-dependent decision is `services.views.resolve`, run in a one-shot
worker thread the connection can abandon, so a connection thread never blocks
without a deadline.

Deadlines are absolute values on one injected monotonic clock and are never
paused, restarted, or extended; under an interruption drain every phase wait
becomes `min(phase, drain)`. The clock, budgets, and resolver are internal
constructor parameters for tests only — the public surface is fixed service
constants with no flag, environment, or configuration representation
(§30 rules 9–10).

Interruption is a state change, never an exception: `interrupt()` only sets
state, closes the listener, and — on the second call — shuts down registered
sockets, so a signal handler may call it at any instant and response
emission still runs on one deterministic path. `serve` then returns its
termination class to the §14.17 command, which owns the envelope.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
import socket
import sqlite3
import threading
import time
from typing import Callable, Iterator, Literal

from exp2res.errors import (
    ViewBindFailedError,
    ViewBindInvalidError,
    ViewBindNotLoopbackError,
)
from exp2res.services import views
from exp2res.services.view_http import ParsedRequest, RequestParser, compose_response
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LISTEN_BACKLOG",
    "LOOPBACK_HOSTS",
    "MAX_CONNECTIONS",
    "MAX_PORT",
    "MIN_PORT",
    "BindAddress",
    "ServeResult",
    "Timeouts",
    "ViewServer",
    "validate_bind",
]


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8731
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
MIN_PORT = 1024
MAX_PORT = 65535

# §30 rule 10: fixed service constants with no flag, environment, or
# configuration representation.
MAX_CONNECTIONS = 32
LISTEN_BACKLOG = 32

_SAFE_METHODS = (b"GET", b"HEAD")

# How serving ended: every value is §14.14 rule 6 cancellation to the §14.17
# command; the class records only whether the drain finished, its absolute
# deadline expired, or a second interruption forced the close.
ServeResult = Literal["drained", "expired", "interrupted"]

# One progress line per completed response: the outcome class and, when the
# request named one of the closed routes, that route — nothing else (§14.17).
# The callback runs on the connection thread after emission and must not
# block indefinitely: a stalled reporter stalls only its own connection
# thread, and the drain deadline will not wait for it.
ReportLine = Callable[[str, str | None], None]


@dataclass(frozen=True)
class BindAddress:
    """One validated literal loopback bind."""

    host: str
    port: int

    @property
    def authority(self) -> str:
        """The single HTTP authority this server answers to (§30 rule 1)."""

        if ":" in self.host:
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.authority}"

    def url(self, route: str, selector: str) -> str:
        return f"{self.origin}{route}?{selector}"


def validate_bind(host: str, port: int) -> BindAddress:
    """Admit only a literal loopback address and a fixed usable port.

    Refused before a socket exists (§30 rule 1). A name is never resolved: a
    name that resolves to loopback today is refused exactly like any other,
    because what it resolves to later is not Exp2Res's decision. Port 0 is
    refused because a URL configured outside Exp2Res cannot name a port
    chosen at bind time (§14.17).
    """

    if host not in LOOPBACK_HOSTS:
        raise ViewBindNotLoopbackError()
    if not isinstance(port, int) or isinstance(port, bool):
        raise ViewBindInvalidError()
    if port < MIN_PORT or port > MAX_PORT:
        raise ViewBindInvalidError()
    return BindAddress(host=host, port=port)


@dataclass(frozen=True)
class Timeouts:
    """§14.17's absolute phase budgets in seconds, on the injected clock.

    Internal: tests inject small values; production always derives from
    §8.1's one bounded contention timeout via `default_timeouts`, and no
    flag, environment value, or configuration reaches these numbers.
    """

    receive: float
    processing: float
    emit: float
    drain: float


def default_timeouts(busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> Timeouts:
    base = busy_timeout_ms / 1000.0
    return Timeouts(
        receive=base,
        processing=views.PROCESSING_TIMEOUT_FACTOR * base,
        emit=base,
        drain=base,
    )


class _AbandonedError(Exception):
    """Raised inside an abandoned worker to stop it before it reads.

    Deliberately outside the exception set `views.resolve` converts to an
    outcome: it unwinds through the resolver's transaction cleanup, reaches
    the worker's own catch-all, and the abandoned handle drops the result.
    """


class _WorkerHandle:
    """The lock-guarded rendezvous between one connection and its worker.

    The worker publishes its open SQLite connection here for the length of
    the read transaction and delivers its page through `deliver`; the
    connection thread alone owns the socket. After `abandon`, a late worker's
    delivery is dropped unread — it writes nothing and touches nothing — a
    registered running read is interrupted so the transaction's own
    `read_database` rollback path releases it inside the worker, and a
    registration arriving after the abandonment stops immediately — an
    interrupt alone would not reach statements that start later.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._abandoned = False
        self.done = threading.Event()
        self.page: views.ViewPage | None = None

    @contextmanager
    def register(self, connection: sqlite3.Connection) -> Iterator[None]:
        """`views.ConnectionRegistrar`: publish the read for cancellation."""

        with self._lock:
            abandoned = self._abandoned
            if not abandoned:
                self._connection = connection
        if abandoned:
            raise _AbandonedError()
        try:
            yield
        finally:
            with self._lock:
                self._connection = None

    def deliver(self, page: views.ViewPage) -> None:
        with self._lock:
            if self._abandoned:
                return
            self.page = page
        self.done.set()

    def abandon(self) -> None:
        with self._lock:
            self._abandoned = True
            connection = self._connection
        if connection is not None:
            # Safe from another thread: the aborted statement raises inside
            # the worker, whose read_database context rolls back and closes.
            # The worker may have closed it between the snapshot and here.
            with suppress(sqlite3.ProgrammingError):
                connection.interrupt()

    def wake(self) -> None:
        """Abandon and release a waiting connection thread (forced close)."""

        self.abandon()
        self.done.set()


class ViewServer:
    """One bound loopback listener serving §30's closed route set.

    The accept loop runs on the calling thread; each admitted connection gets
    a daemon thread that serves exactly one request. A §30 rule 10 slot is
    taken immediately after `accept` and before any thread, buffer, or read
    exists; when none is free the socket is closed unread with no response.
    """

    def __init__(
        self,
        workspace: Path,
        bind: BindAddress,
        *,
        report: ReportLine | None = None,
        _clock: Callable[[], float] = time.monotonic,
        _timeouts: Timeouts | None = None,
        _resolver: Callable[..., views.ViewPage] = views.resolve,
        _busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self._workspace = workspace
        self.bind_address = bind
        self._report = report
        self._clock = _clock
        self._timeouts = _timeouts if _timeouts is not None else default_timeouts()
        self._resolver = _resolver
        self._busy_timeout_ms = _busy_timeout_ms
        self._authority = bind.authority.encode("ascii")
        self._origin = bind.origin.encode("ascii")
        self._slots = threading.Semaphore(MAX_CONNECTIONS)
        # Reentrant because `interrupt()` may run in a signal handler on the
        # accept-loop thread while that thread already holds the lock; a
        # plain lock would deadlock the first interruption instead of
        # starting the drain.
        self._state_lock = threading.RLock()
        self._sockets: set[socket.socket] = set()
        self._handles: set[_WorkerHandle] = set()
        self._threads: list[threading.Thread] = []
        self._draining = threading.Event()
        self._immediate = threading.Event()
        self._drain_deadline: float | None = None
        self._listener: socket.socket | None = None

    def open(self) -> None:
        """Bind exactly the validated address, or fail without another try."""

        family = socket.AF_INET6 if ":" in self.bind_address.host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        try:
            if family == socket.AF_INET6:
                # The bind names exactly one literal loopback address, so an
                # IPv6 socket must not also accept IPv4 traffic (§30 rule 1).
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.bind((self.bind_address.host, self.bind_address.port))
            listener.listen(LISTEN_BACKLOG)
        except OSError as error:
            listener.close()
            raise ViewBindFailedError() from error
        self._listener = listener

    def interrupt(self) -> None:
        """First call drains; a second forces the close. Never raises.

        Sets state and closes sockets only, so a signal handler may run it at
        any instant without breaking the one deterministic emission path. The
        drain deadline is one absolute instant covering every remaining
        phase; the listener close is what unblocks a waiting `accept`.
        """

        if self._draining.is_set():
            if not self._immediate.is_set():
                self._immediate.set()
                self._force_close()
            return
        with self._state_lock:
            self._drain_deadline = self._clock() + self._timeouts.drain
        self._draining.set()
        listener = self._listener
        if listener is not None:
            # Shutdown before close: closing alone does not wake an accept
            # blocked in another thread, while shutdown makes it raise at
            # once. The suppressed errors are the already-closed cases.
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                listener.close()

    def serve(self) -> ServeResult:
        """Accept until interrupted, then drain; returns the termination class."""

        if self._listener is None:
            self.open()
        listener = self._listener
        assert listener is not None
        try:
            while not self._draining.is_set():
                try:
                    connection, _peer = listener.accept()
                except OSError:
                    if self._draining.is_set():
                        break
                    raise
                if not self._slots.acquire(blocking=False):
                    # §30 rule 10: closed unread — no buffer, no thread, no
                    # HTTP response — so it is not a complete request and
                    # reaches no state.
                    connection.close()
                    continue
                admitted_at = self._clock()
                with self._state_lock:
                    self._sockets.add(connection)
                thread = threading.Thread(
                    target=self._serve_connection,
                    args=(connection, admitted_at),
                    daemon=True,
                )
                self._threads = [t for t in self._threads if t.is_alive()]
                self._threads.append(thread)
                thread.start()
        finally:
            with suppress(OSError):
                listener.close()
        return self._drain()

    def _drain(self) -> ServeResult:
        """Join connections until the one absolute drain deadline, then close.

        An abandoned worker is never joined: it is a daemon thread whose slot
        and transaction were already released, and waiting on it would hand a
        wedged call the drain budget.
        """

        with self._state_lock:
            deadline = self._drain_deadline
        assert deadline is not None
        expired = False
        for thread in self._threads:
            if self._immediate.is_set():
                break
            remaining = deadline - self._clock()
            if remaining > 0:
                thread.join(remaining)
            if thread.is_alive():
                expired = True
                break
        if expired and not self._immediate.is_set():
            self._force_close()
        if self._immediate.is_set():
            return "interrupted"
        if expired:
            # §14.17: expiry closes the unfinished connections and returns to
            # the envelope without waiting further. The forced-closed daemon
            # threads finish their released cleanup on their own; joining
            # them here would hand a stalled emission or reporter the time
            # the deadline already refused.
            return "expired"
        return "drained"

    def _force_close(self) -> None:
        """Release every blocked phase: wake waits, shut down sockets."""

        with self._state_lock:
            handles = list(self._handles)
            sockets = list(self._sockets)
        for handle in handles:
            handle.wake()
        for connection in sockets:
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)

    def _phase_deadline(self, deadline: float) -> float:
        """§14.17: under a drain, every phase wait is `min(phase, drain)`."""

        if not self._draining.is_set():
            return deadline
        with self._state_lock:
            drain = self._drain_deadline
        return deadline if drain is None else min(deadline, drain)

    def _serve_connection(self, connection: socket.socket, admitted_at: float) -> None:
        try:
            try:
                parser = self._receive(connection, admitted_at)
                if parser is None:
                    # Receive expiry, peer close, or forced close: no
                    # complete request was admitted, so there is nothing to
                    # answer (§30 rule 7).
                    return
                # §14.17: the absolute processing deadline begins the moment
                # receiving ends and spans every remaining check.
                deadline = self._clock() + self._timeouts.processing
                page = self._decide(parser, deadline)
                if page is None:
                    return
                head = not parser.malformed and parser.request.method == b"HEAD"
                self._emit(connection, page, head=head)
                if self._report is not None:
                    route = None
                    if not parser.malformed and parser.request.path in views.ROUTES:
                        route = parser.request.path.decode("ascii")
                    self._report(page.outcome, route)
            except Exception:
                # A per-connection failure never prints a traceback or peer
                # detail: §30 rule 6 keeps request bytes out of diagnostics.
                pass
        finally:
            with self._state_lock:
                self._sockets.discard(connection)
            with suppress(OSError):
                connection.close()
            self._slots.release()

    def _receive(
        self, connection: socket.socket, admitted_at: float
    ) -> RequestParser | None:
        """Read one bounded envelope under the absolute receive deadline.

        The deadline began when the admission slot was acquired; another byte
        never pauses, restarts, or replaces it. Each read asks the parser's
        budget, so at most one octet beyond an applicable cap is ever read.
        `None` closes the connection with no response bytes.
        """

        parser = RequestParser()
        deadline = admitted_at + self._timeouts.receive
        while not parser.done:
            if self._immediate.is_set():
                return None
            remaining = self._phase_deadline(deadline) - self._clock()
            if remaining <= 0:
                return None
            try:
                connection.settimeout(remaining)
                chunk = connection.recv(parser.receive_budget)
            except OSError:
                return None
            if not chunk:
                return None
            parser.feed(chunk)
        if self._phase_deadline(deadline) - self._clock() <= 0:
            # The absolute deadline is the boundary even when the final read
            # was already in flight at expiry: a request completed late is a
            # receive expiry, not an admitted request.
            return None
        return parser

    def _decide(
        self, parser: RequestParser, deadline: float
    ) -> views.ViewPage | None:
        """Run §30 rule 7's ordered pre-state refusals, then the resolver.

        Transport parsing already decided `malformed_request`; here the order
        is authority — the parsed `Host`, then the declared origin — then
        method, then the declared body, all before any state is read. Route,
        selector, and state belong to `views.resolve` in the worker.
        """

        if parser.malformed:
            return self._within(views.malformed_request_page(), deadline)
        request = parser.request
        assert request is not None
        if request.host != self._authority:
            return self._within(views.authority_not_bound_page(), deadline)
        if request.origin is not None and request.origin != self._origin:
            return self._within(views.authority_not_bound_page(), deadline)
        if request.method not in _SAFE_METHODS:
            return self._within(views.method_not_allowed_page(), deadline)
        if request.framing == "declared_body":
            return self._within(views.malformed_request_page(), deadline)
        return self._resolve_abandonable(request, deadline)

    def _within(self, page: views.ViewPage, deadline: float) -> views.ViewPage:
        """The processing deadline is an outer boundary over every check.

        A row not fully composed when the budget expires is not the outcome,
        so an expired deadline turns any pre-state refusal into the fixed
        timeout page; the resolver path applies the same rule inside
        `views.resolve`.
        """

        if self._clock() < deadline:
            return page
        return views.processing_timeout_page()

    def _resolve_abandonable(
        self, request: ParsedRequest, deadline: float
    ) -> views.ViewPage | None:
        """Resolve in a one-shot worker the connection thread can abandon.

        The wait is bounded by the processing deadline (drained: also the
        drain deadline) and by a forced-close wake. On expiry the worker is
        abandoned — its registered SQLite read interrupted, its late result
        dropped — and the fixed `processing_timeout` outcome is composed
        under the emit deadline; the slot is released by this connection's
        ordinary `finally`, never held by the expired work.
        """

        handle = _WorkerHandle()
        with self._state_lock:
            self._handles.add(handle)
        try:
            if self._immediate.is_set():
                # A forced close that snapshotted the handle set before this
                # registration set the flag first, so checking it here closes
                # the wake race.
                return None
            worker = threading.Thread(
                target=self._run_resolver, args=(request, deadline, handle), daemon=True
            )
            worker.start()
            finished = handle.done.wait(
                max(0.0, self._phase_deadline(deadline) - self._clock())
            )
            if self._immediate.is_set():
                handle.abandon()
                return None
            if self._draining.is_set():
                with self._state_lock:
                    drain = self._drain_deadline
                if drain is not None and self._clock() >= drain:
                    # Drain expiry closes without a response; the forced
                    # close, not this thread, may already have woken us.
                    handle.abandon()
                    return None
            if not finished or handle.page is None:
                handle.abandon()
                return views.processing_timeout_page()
            return handle.page
        finally:
            with self._state_lock:
                self._handles.discard(handle)

    def _run_resolver(
        self, request: ParsedRequest, deadline: float, handle: _WorkerHandle
    ) -> None:
        try:
            page = self._resolver(
                self._workspace,
                request.path,
                request.query,
                deadline=deadline,
                register_connection=handle.register,
                busy_timeout_ms=self._busy_timeout_ms,
            )
        except BaseException:
            # `resolve` fails closed itself; anything escaping it — including
            # the interrupt-aborted read of an abandoned worker — still
            # yields one page, which an abandoned handle drops unread.
            page = views.internal_error_page()
        handle.deliver(page)

    def _emit(
        self, connection: socket.socket, page: views.ViewPage, *, head: bool
    ) -> None:
        """Send one response under the absolute emit deadline.

        The deadline starts before the first byte, after the outcome was
        composed and its read transaction closed. Expiry or a transport
        failure closes without retry — a partial response is acceptable and
        never changes the computed outcome (§14.17).
        """

        deadline = self._clock() + self._timeouts.emit
        response = memoryview(compose_response(page, head=head))
        while response:
            if self._immediate.is_set():
                return
            remaining = self._phase_deadline(deadline) - self._clock()
            if remaining <= 0:
                return
            try:
                connection.settimeout(remaining)
                sent = connection.send(response)
            except OSError:
                return
            response = response[sent:]
