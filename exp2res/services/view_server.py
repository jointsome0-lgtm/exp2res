"""§14.17's loopback socket transport in front of §30's projections.

This module owns only the transport half of serving: which bind is admitted,
§30 rule 10's connection admission, rule 9's parsing through `view_http`,
rule 7's ordered pre-state refusals — authority, method, declared body — and
§14.17's absolute receive, processing, emit, and drain deadlines. Every
state-dependent decision is `services.views.resolve`, run in a one-shot child
process the connection can terminate, so a connection thread never blocks
without a deadline and expired filesystem work cannot outlive its slot.

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
import multiprocessing
from multiprocessing.connection import Connection, wait as wait_for_connections
from multiprocessing.context import BaseContext
import os
from pathlib import Path
import pickle
import queue
import signal
import socket
import sqlite3
import struct
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
    "GLOBAL_SELECTOR",
    "LISTEN_BACKLOG",
    "LOOPBACK_HOSTS",
    "MAX_CONNECTIONS",
    "MAX_PORT",
    "MIN_PORT",
    "BindAddress",
    "ServeResult",
    "Timeouts",
    "ViewServer",
    "bound_urls",
    "validate_bind",
]


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8731
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
MIN_PORT = 1024
MAX_PORT = 65535

GLOBAL_SELECTOR = "scope=global"

# §30 rule 10: fixed service constants with no flag, environment, or
# configuration representation.
MAX_CONNECTIONS = 32
LISTEN_BACKLOG = 32
# Progress lines held for a slow reporter before excess lines are dropped;
# diagnostics never get to block or accumulate request-side threads.
REPORT_QUEUE_LIMIT = 64
# How often the post-drain flush rechecks its two exits — the deadline and a
# second interruption. Not an allowance of its own: it only bounds how long
# either one goes unnoticed.
_FLUSH_POLL_SECONDS = 0.05
_PROCESS_REAP_POLL_SECONDS = 0.05
_RESULT_LENGTH = struct.Struct("!Q")

_SAFE_METHODS = (b"GET", b"HEAD")

# How serving ended: every value is §14.14 rule 6 cancellation to the §14.17
# command; the class records only whether the drain finished, its absolute
# deadline expired, or a second interruption forced the close.
ServeResult = Literal["drained", "expired", "interrupted"]

# One progress line per completed response: the outcome class and, when the
# request named one of the closed routes, that route — nothing else (§14.17).
# The callback runs on the connection thread only after the terminal close
# and slot release, so a stalled reporter holds no socket, slot, or drain
# time — only its own finished daemon thread.
ReportLine = Callable[[str, str | None], None]
ReportItem = tuple[Callable[..., None], tuple[object, ...], bool]


def _check_bind(host: str, port: int) -> None:
    """§30 rule 1's admissible bind, decided before anything is derived.

    A name is never resolved: a name that resolves to loopback today is
    refused exactly like any other, because what it resolves to later is not
    Exp2Res's decision. Port 0 is refused because a URL configured outside
    Exp2Res cannot name a port chosen at bind time (§14.17).
    """

    if host not in LOOPBACK_HOSTS:
        raise ViewBindNotLoopbackError()
    if not isinstance(port, int) or isinstance(port, bool):
        raise ViewBindInvalidError()
    if port < MIN_PORT or port > MAX_PORT:
        raise ViewBindInvalidError()


@dataclass(frozen=True)
class BindAddress:
    """One literal loopback bind, unconstructable as anything else.

    The check lives here rather than only in `validate_bind` so that no code
    path can hold an inadmissible bind at all: this is an exported value, and
    §30 rule 1's refusal must not depend on which entry point built it.
    """

    host: str
    port: int

    def __post_init__(self) -> None:
        _check_bind(self.host, self.port)

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

    Refused before a socket exists (§30 rule 1). This is the named entry
    point the §14.17 command uses on its parsed flags; the same refusal is
    enforced by `BindAddress` itself, so an inadmissible bind cannot be
    reached by constructing one directly instead.
    """

    _check_bind(host, port)
    return BindAddress(host=host, port=port)


def bound_urls(bind: BindAddress) -> tuple[str, ...]:
    """Exactly §14.17's two usable startup URLs, in §30 rule 6's route order.

    Derived from `views.ROUTES` rather than restated, so the closed route set
    and the order the command reports stay one definition. Each URL carries
    the explicit identity selector §30 requires: no selectorless base route,
    template, snapshot selector, project selector, trailing path, fragment,
    or extra parameter is ever advertised.
    """

    return tuple(
        bind.url(route.decode("ascii"), GLOBAL_SELECTOR) for route in views.ROUTES
    )


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
        """`views.ConnectionRegistrar`: publish the read for cancellation.

        The progress handler is what makes abandonment cover the gap an
        `interrupt()` alone leaves: an interrupt aborts only a statement
        already running, while the handler also aborts a statement the
        abandoned worker starts later inside the still-open transaction.
        """

        with self._lock:
            abandoned = self._abandoned
            if not abandoned:
                self._connection = connection
        if abandoned:
            raise _AbandonedError()
        # n=1 so even a statement short enough to finish within any larger
        # interval is checked — and aborted — at its first VM instruction.
        connection.set_progress_handler(self._abort_when_abandoned, 1)
        try:
            yield
        finally:
            connection.set_progress_handler(None, 0)
            with self._lock:
                self._connection = None

    def _abort_when_abandoned(self) -> int:
        # Runs on the worker thread between SQLite VM instructions; a plain
        # flag read is atomic and a nonzero return aborts the statement.
        return 1 if self._abandoned else 0

    def deliver(self, page: views.ViewPage) -> None:
        with self._lock:
            if self._abandoned:
                return
            self.page = page
            # Completion is published inside the lock, with the page itself.
            # The connection thread classifies a timeout from this event, so
            # a page assigned while the event still looked unset would be
            # discarded as expired even though it was composed in budget.
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


class _ProcessHandle:
    def __init__(
        self,
        process,
        *,
        starting: bool = False,
        cancelled=None,
        start_allowed=None,
    ) -> None:
        self.process = process
        self._cancel_event = cancelled
        self._start_allowed = start_allowed
        self._lock = threading.Lock()
        self._start_done = threading.Event()
        self._started = not starting
        self._start_failed = False
        self._cancelled = False
        if not starting:
            self._start_done.set()

    def complete_start(self, *, failed: bool) -> None:
        with self._lock:
            self._started = not failed
            self._start_failed = failed
            cancelled = self._cancelled
        if failed or cancelled:
            if self._cancel_event is not None:
                self._cancel_event.set()
        elif self._start_allowed is not None:
            self._start_allowed.set()
        if cancelled and not failed:
            self._kill()
        self._start_done.set()

    def wait_started(self, timeout: float) -> bool:
        return self._start_done.wait(timeout)

    @property
    def start_failed(self) -> bool:
        with self._lock:
            return self._start_failed

    @property
    def start_pending(self) -> bool:
        return not self._start_done.is_set()

    def wake(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        with self._lock:
            self._cancelled = True
            started = self._started
        if started:
            self._kill()

    def _kill(self) -> None:
        with suppress(AssertionError, OSError, ValueError):
            if self.process.is_alive():
                self.process.kill()

    def finish(self) -> bool:
        self.wake()
        if not self._start_done.is_set():
            return False
        with self._lock:
            started = self._started
        if not started:
            with suppress(AttributeError, ValueError):
                self.process.close()
            return True
        try:
            self.process.join(timeout=0)
            finished = not self.process.is_alive()
        except (AssertionError, OSError, ValueError):
            return False
        if finished:
            with suppress(ValueError):
                self.process.close()
        return finished


class _AdmissionLease:
    def __init__(self, semaphore: threading.Semaphore) -> None:
        self._semaphore = semaphore
        self._lock = threading.Lock()
        self._connection_done = False
        self._process_done = True
        self._released = False

    def defer(self) -> None:
        with self._lock:
            self._process_done = False

    def release(self) -> None:
        with self._lock:
            self._connection_done = True
            release = self._release_if_ready()
        if release:
            self._semaphore.release()

    def release_deferred(self) -> None:
        with self._lock:
            self._process_done = True
            release = self._release_if_ready()
        if release:
            self._semaphore.release()

    def _release_if_ready(self) -> bool:
        if self._released or not self._connection_done or not self._process_done:
            return False
        self._released = True
        return True


def _write_process_result(sender: Connection, page: views.ViewPage) -> None:
    payload = pickle.dumps(page, protocol=pickle.HIGHEST_PROTOCOL)
    frame = _RESULT_LENGTH.pack(len(payload)) + payload
    offset = 0
    while offset < len(frame):
        offset += os.write(sender.fileno(), frame[offset:])


def _run_resolver_process(
    sender: Connection,
    cancelled,
    start_allowed,
    resolver: Callable[..., views.ViewPage],
    workspace: Path,
    request: ParsedRequest,
    deadline: float,
    busy_timeout_ms: int,
) -> None:
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        with suppress(AttributeError):
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
        while not start_allowed.wait(_PROCESS_REAP_POLL_SECONDS):
            if cancelled.is_set():
                return
        if cancelled.is_set():
            return
        try:
            page = resolver(
                workspace,
                request.path,
                request.query,
                deadline=deadline,
                busy_timeout_ms=busy_timeout_ms,
            )
        except BaseException:
            page = views.internal_error_page()
        with suppress(BrokenPipeError, EOFError, OSError):
            _write_process_result(sender, page)
    finally:
        sender.close()


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
        _isolate_resolver: bool | None = None,
        _process_context: BaseContext | None = None,
        _busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self._workspace = workspace
        self.bind_address = bind
        self._report = report
        self._clock = _clock
        self._timeouts = _timeouts if _timeouts is not None else default_timeouts()
        self._resolver = _resolver
        self._isolate_resolver = (
            _resolver is views.resolve if _isolate_resolver is None else _isolate_resolver
        )
        self._process_context = (
            multiprocessing.get_context("spawn")
            if _process_context is None
            else _process_context
        )
        self._busy_timeout_ms = _busy_timeout_ms
        self._authority = bind.authority.encode("ascii")
        self._origin = bind.origin.encode("ascii")
        self._slots = threading.Semaphore(MAX_CONNECTIONS)
        self._process_start_slots = threading.Semaphore(MAX_CONNECTIONS)
        # Reentrant because `interrupt()` may run in a signal handler on the
        # accept-loop thread while that thread already holds the lock; a
        # plain lock would deadlock the first interruption instead of
        # starting the drain.
        self._state_lock = threading.RLock()
        self._sockets: set[socket.socket] = set()
        self._handles: set[_WorkerHandle | _ProcessHandle] = set()
        # Admitted connections not yet terminally closed. The drain waits on
        # this count, never on connection threads, so a stalled reporter
        # cannot consume the drain after its connection has been released.
        self._active = 0
        self._idle = threading.Condition(self._state_lock)
        self._draining = threading.Event()
        self._immediate = threading.Event()
        self._drain_deadline: float | None = None
        self._listener: socket.socket | None = None
        # One dedicated reporter thread behind a bounded queue: a blocked
        # stderr can then stall only this one thread and drop excess progress
        # lines, never accumulate a blocked thread per completed connection.
        self._report_queue: queue.Queue[ReportItem | None] = queue.Queue(
            maxsize=REPORT_QUEUE_LIMIT + 3
        )
        self._report_slots = threading.Semaphore(REPORT_QUEUE_LIMIT)
        self._report_thread: threading.Thread | None = None
        self._reap_queue: queue.Queue[
            tuple[_ProcessHandle, _AdmissionLease | None]
        ] = queue.Queue()
        self._reap_stop = threading.Event()
        self._reap_thread: threading.Thread | None = None

    def advertise(self, announce: Callable[[str], None]) -> None:
        """Queue §14.17's two startup URLs while the listener is usable."""

        completions: list[threading.Event] = []
        failed = threading.Event()
        failures: list[BaseException] = []
        with self._state_lock:
            if self._listener is None or self._draining.is_set():
                return
            if not self._start_reporter():
                return
            for url in bound_urls(self.bind_address):
                if self._draining.is_set():
                    return
                complete = threading.Event()
                completions.append(complete)
                self._report_queue.put_nowait(
                    (
                        self._announce_if_live,
                        (announce, url, complete, failed, failures),
                        False,
                    )
                )
        for complete in completions:
            while not complete.wait(_FLUSH_POLL_SECONDS):
                if self._draining.is_set() or self._immediate.is_set():
                    return
            if failures:
                self._abort_advertisement()
                raise failures[0]

    def _announce_if_live(
        self,
        announce: Callable[[str], None],
        url: str,
        complete: threading.Event,
        failed: threading.Event,
        failures: list[BaseException],
    ) -> None:
        try:
            with self._state_lock:
                listener = self._listener
                live = (
                    listener is not None
                    and listener.fileno() >= 0
                    and not self._draining.is_set()
                )
            if live and not failed.is_set():
                try:
                    announce(url)
                except BaseException as error:
                    failures.append(error)
                    failed.set()
        finally:
            complete.set()

    def _abort_advertisement(self) -> None:
        with self._state_lock:
            listener = self._listener
            self._listener = None
        if listener is not None:
            with suppress(OSError):
                listener.close()
        self._stop_reporter()

    def open(self) -> None:
        """Bind exactly the validated address, or fail without another try.

        The address needs no check here: `BindAddress` cannot hold an
        inadmissible bind, so §30 rule 1's refusal has already happened
        before any instance reaches this method.
        """

        if self._draining.is_set():
            # `interrupt` is callable at any instant, and `open` is a public
            # step a caller may take separately from `serve`. §14.14 rule 6's
            # cancellation outranks a bind that has not been attempted, so an
            # interruption that arrived first is never overtaken by a bind
            # refusal for a socket this call would only now create.
            return
        family = socket.AF_INET6 if ":" in self.bind_address.host else socket.AF_INET
        try:
            # Creation is inside the conversion: a disabled address family or
            # an exhausted descriptor table is the same operating-system
            # refusal of the requested bind as a failing `bind` itself.
            listener = socket.socket(family, socket.SOCK_STREAM)
        except OSError as error:
            raise ViewBindFailedError() from error
        try:
            if family == socket.AF_INET6:
                # The bind names exactly one literal loopback address, so an
                # IPv6 socket must not also accept IPv4 traffic (§30 rule 1).
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            # §30 rule 1 forbids port 0 and any fallback port, so a restart
            # has exactly one address to take and no way to route around a
            # refusal. Every served connection closes from this side, leaving
            # the accepted socket in `TIME_WAIT`, and that is enough to make a
            # plain rebind of the same address fail — a normal stop/start
            # cycle would lose the view until the kernel timeout expires.
            # Address reuse is what makes the restart deterministic; it never
            # relaxes the bind itself, because a live listener still holds the
            # address exclusively. Accepted sockets inherit the option, so
            # setting it here also covers the `TIME_WAIT` entries this run
            # leaves behind for the next one.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.bind_address.host, self.bind_address.port))
            listener.listen(LISTEN_BACKLOG)
        except OSError as error:
            listener.close()
            raise ViewBindFailedError() from error
        with self._state_lock:
            # Publication and the drain check are one atomic step. An
            # interruption that arrived while this socket was still local
            # found no listener to close, and `serve` would return through the
            # early drain without reaching the close in its own `finally` —
            # leaving the port held for the life of an embedding process.
            # Whichever side takes the lock first owns the close.
            if not self._draining.is_set():
                self._listener = listener
                return
        listener.close()

    def interrupt(self) -> None:
        """First call drains; a second forces the close. Never raises.

        Sets state and closes sockets only, so a signal handler may run it at
        any instant without breaking the one deterministic emission path. The
        drain deadline is one absolute instant covering every remaining
        phase; the listener close is what unblocks a waiting `accept`.
        """

        # Read before the lock: §14.17 starts the absolute drain deadline at
        # the interruption instant, so waiting behind a connection thread that
        # holds the state lock has to spend the allowance rather than extend
        # it. The sample is kept only by the call that wins the first-
        # interruption classification below.
        interrupted_at = self._clock()
        with self._state_lock:
            # Classification and the deadline write are one atomic step, so
            # two near-simultaneous interruptions cannot both take the first
            # path or replace the recorded drain deadline with a later one.
            second = self._draining.is_set()
            if not second:
                self._drain_deadline = interrupted_at + self._timeouts.drain
                self._draining.set()
        if second:
            if not self._immediate.is_set():
                self._immediate.set()
                self._force_close()
            return
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
        """Accept until interrupted, then drain; returns the termination class.

        Every exit retires the reporter — drained, expired, forced, or a
        failure that never reached a drain. A process that serves once and
        exits would not notice; an embedding one would otherwise accumulate a
        blocked thread and a live callback per run.
        """

        try:
            return self._serve()
        finally:
            self._stop_reporter()
            self._stop_process_reaper()

    def _start_reporter(self) -> bool:
        if self._report_thread is not None:
            return True
        reporter = threading.Thread(
            target=self._report_loop, name="view-report", daemon=True
        )
        try:
            reporter.start()
        except RuntimeError:
            with self._state_lock:
                listener = self._listener
                self._listener = None
            if listener is not None:
                with suppress(OSError):
                    listener.close()
            if self._draining.is_set():
                return False
            raise
        self._report_thread = reporter
        return True

    def _serve(self) -> ServeResult:
        if self._listener is None:
            if self._draining.is_set():
                # `interrupt` is callable at any instant, including before
                # this bind is attempted. §14.14 rule 6's cancellation has
                # precedence over a bind that never ran, so an interruption
                # that arrives first is never overtaken by a bind refusal.
                return self._drain()
            self.open()
        listener = self._listener
        assert listener is not None
        if self._draining.is_set():
            # `open` is a public step, so an interruption can close an
            # already-bound listener before `serve` reaches this point. The
            # cancellation result has precedence and no request can now
            # produce a progress line; starting the optional reporter would
            # create new work whose queue can only remain empty.
            return self._drain()
        if (
            self._report is not None
            and self._report_thread is None
            and not self._start_reporter()
        ):
            return self._drain()
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
                # §14.17 starts the receive deadline when §30 rule 10 acquires
                # the admission slot, so the anchor is read after a successful
                # acquisition and not before the attempt. Nothing is waited for
                # in between — the acquisition is non-blocking — so reading it
                # earlier would charge the connection for time before the event
                # the deadline is defined to begin at.
                admitted_at = self._clock()
                with self._state_lock:
                    # Atomic with both interruption steps. `accept` can return
                    # a socket the kernel queued in the backlog after
                    # `interrupt` closed the listener, so the drain boundary
                    # has to be part of the admission decision and not only
                    # the loop condition: §14.17's first interruption stops
                    # accepting connections at that instant, and a connection
                    # admitted afterwards would also make the drain wait on
                    # work the deadline never covered. The draining flag
                    # subsumes the immediate one — a forced close only ever
                    # follows a drain — so either this socket registers before
                    # the sweep's snapshot and gets swept, or the interruption
                    # is already visible here and the socket is closed unread.
                    admitted = not self._draining.is_set()
                    if admitted:
                        self._sockets.add(connection)
                        self._active += 1
                if not admitted:
                    self._slots.release()
                    with suppress(OSError):
                        connection.close()
                    continue
                try:
                    threading.Thread(
                        target=self._serve_connection,
                        args=(connection, admitted_at),
                        daemon=True,
                    ).start()
                except RuntimeError:
                    # The operating system refused the connection thread, so
                    # nothing will ever run this connection's own release. It
                    # happens here instead: without it the socket stays live
                    # and registered, the §30 rule 10 slot stays taken, and
                    # the drain count stays raised forever. No thread exists
                    # to compose a response on and no request byte was read,
                    # so the peer gets rule 10's unread close, and serving
                    # continues — a refused thread is transient.
                    self._release_admission(connection)
        except BaseException:
            # Serving ends without a drain — an `accept` the operating system
            # refused for its own reasons, descriptor exhaustion among them.
            # The requests already admitted must not outlive the call that
            # owns them: they are released here, before the failure reaches
            # §14.17's envelope, so no socket, worker, or read transaction
            # keeps running behind a command that has already reported.
            self._force_close()
            raise
        finally:
            with suppress(OSError):
                listener.close()
        return self._drain()

    def _drain(self) -> ServeResult:
        """Await open requests until the one absolute drain deadline, then close.

        The wait is on the count of admitted requests still holding their
        slot, never on connection threads: a finished request's stalled
        reporter and an abandoned worker each hold no slot, and joining
        either thread would hand a wedged call the drain budget §14.17
        reserves for unfinished request work.
        """

        with self._state_lock:
            deadline = self._drain_deadline
        assert deadline is not None
        expired = False
        with self._idle:
            while self._active and not self._immediate.is_set():
                remaining = deadline - self._clock()
                if remaining <= 0:
                    expired = True
                    break
                self._idle.wait(remaining)
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
        with self._idle:
            self._idle.notify_all()

    def _phase_deadline(self, deadline: float) -> float:
        """§14.17: under a drain, every phase wait is `min(phase, drain)`."""

        if not self._draining.is_set():
            return deadline
        with self._state_lock:
            drain = self._drain_deadline
        return deadline if drain is None else min(deadline, drain)

    def _drain_expired(self) -> bool:
        """Has a first interruption's absolute drain deadline already passed?

        Expiry does not set `_immediate` — `_drain` closes the unfinished
        connections and returns its class — so this is the only way a
        connection thread can tell that its remaining time is gone.
        """

        if not self._draining.is_set():
            return False
        with self._state_lock:
            drain = self._drain_deadline
        return drain is not None and self._clock() >= drain

    def _serve_connection(self, connection: socket.socket, admitted_at: float) -> None:
        line: tuple[str, str | None] | None = None
        lease = _AdmissionLease(self._slots)
        try:
            try:
                received = self._receive(connection, admitted_at)
                if received is None:
                    # Receive expiry, peer close, or forced close: no
                    # complete request was admitted, so there is nothing to
                    # answer (§30 rule 7).
                    return
                parser, completed_at = received
                # §14.17: the absolute processing deadline begins the moment
                # the terminating empty header line made the request complete
                # — the timestamp `_receive` captured, so a scheduling stall
                # after completion spends the budget rather than extending it.
                deadline = completed_at + self._timeouts.processing
                page = self._decide(parser, deadline, lease)
                if page is None:
                    return
                head = not parser.malformed and parser.request.method == b"HEAD"
                page = self._emit(connection, page, deadline, head=head)
                route = None
                if not parser.malformed and parser.request.path in views.ROUTES:
                    route = parser.request.path.decode("ascii")
                line = (page.outcome, route)
            except Exception:
                # A per-connection failure never prints a traceback or peer
                # detail: §30 rule 6 keeps request bytes out of diagnostics.
                pass
        finally:
            self._enqueue_report(line)
            self._release_admission(connection, lease)

    def _enqueue_report(self, line: tuple[str, str | None] | None) -> None:
        """Hand one completed request's line to the reporter, or drop it.

        Callers must reach this before releasing the connection's admission
        slot. That release is what lets a waiting drain return, and a drain
        that returns first retires the reporter ahead of the line. Doing it
        first costs the connection nothing it was protected from: this is one
        non-blocking put, so a stalled reporter still holds no socket, slot,
        or drain time, and the reporter thread remains the sole caller of the
        callback.

        A refused acquisition means all `REPORT_QUEUE_LIMIT` data slots are
        held by lines a stalled reporter has not taken yet; the line is
        dropped rather than waited on. The queue holds one slot more than the
        semaphore ever grants, and that reserve belongs to the stop sentinel
        alone, so saturation can never cost the reporter its termination
        signal. `_report_loop` returns a permit when it takes a line rather
        than when it finishes writing it, so a slow stream costs capacity
        only while lines are undelivered.
        """

        if line is None or self._report is None:
            return
        if not self._report_slots.acquire(blocking=False):
            return
        self._report_queue.put_nowait((self._report, line, True))

    def _release_admission(
        self, connection: socket.socket, lease: _AdmissionLease | None = None
    ) -> None:
        """Give back everything one admission took, in the one safe order.

        Close before deregistering: a forced close arriving in between must
        still find a socket that is already closed or closing, never a live
        peer the shutdown sweep cannot reach. The drain count drops last, so
        a drain never returns while this connection still holds a slot.
        """

        with suppress(OSError):
            connection.close()
        with self._state_lock:
            self._sockets.discard(connection)
        if lease is None:
            self._slots.release()
        else:
            lease.release()
        with self._idle:
            self._active -= 1
            self._idle.notify_all()

    def _report_loop(self) -> None:
        while True:
            item = self._report_queue.get()
            if item is None:
                return
            callback, arguments, releases_slot = item
            if releases_slot:
                self._report_slots.release()
            with suppress(Exception):
                callback(*arguments)

    def _stop_reporter(self) -> None:
        """Retire the reporter once serving is over, whatever it reported.

        Ordering is what makes this safe rather than timing. Every completed
        request queues its line before releasing the slot the drain waits on,
        so by the time this runs each one is already ahead of the sentinel in
        one FIFO queue: the thread delivers them all and then returns on its
        own. The put needs no failure path because `_enqueue_report` never
        spends the queue's last slot — that reserve exists for exactly this
        signal, so a saturated queue cannot swallow it.

        The wait is whatever the one absolute drain deadline has left, and
        never a second past it: §14.17 lets nothing extend that bound, and a
        second interruption performs the close immediately, so it leaves none
        at all. Without any wait a line queued just before the interruption
        would race the caller's return, and the sink it was bound to — a
        `CliRunner` stderr, a process about to exit — would be gone before
        the reporter reached it. A returning thread has delivered every line
        ahead of the sentinel; a wedged one keeps its own losses when the
        deadline arrives, a daemon thread holding no socket, slot, or
        transaction, and a sink wedged that long could not have carried the
        envelope either.
        """

        with self._state_lock:
            reporter = self._report_thread
            self._report_thread = None
        if reporter is None:
            return
        self._await_producers()
        self._report_queue.put_nowait(None)
        self._flush(reporter)

    def _await_producers(self) -> None:
        """Let every admitted request queue its line ahead of the sentinel.

        A drain ends with none outstanding, so this is for the path that has
        no drain: an `accept` the operating system refused force-closes and
        unwinds, and a connection that had already emitted its response but
        not yet queued its line would put it behind the sentinel, where no
        reporter is left to take it. Bounded by the same §8.1 timeout the
        drain uses — a force-closed request has only its own release left to
        run — and skipped outright once the second interruption has demanded
        the immediate close, which outranks any diagnostic.
        """

        if self._immediate.is_set():
            return
        deadline = self._clock() + self._timeouts.drain
        with self._idle:
            while self._active and not self._immediate.is_set():
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return
                self._idle.wait(remaining)

    def _flush(self, reporter: threading.Thread) -> None:
        """Wait for the sentinel to come back, inside the drain's own bound.

        Sliced rather than one long join so the second interruption is not
        merely recorded: it forces the close, and a wait that could not see it
        would hold the envelope for the rest of the deadline instead.
        """

        with self._state_lock:
            deadline = self._drain_deadline
        if deadline is None:
            return
        while reporter.is_alive():
            remaining = deadline - self._clock()
            if remaining <= 0 or self._immediate.is_set():
                return
            reporter.join(min(remaining, _FLUSH_POLL_SECONDS))

    def _receive(
        self, connection: socket.socket, admitted_at: float
    ) -> tuple[RequestParser, float] | None:
        """Read one bounded envelope under the absolute receive deadline.

        The deadline began when the admission slot was acquired; another byte
        never pauses, restarts, or replaces it. Each read asks the parser's
        budget, so at most one octet beyond an applicable cap is ever read.
        Returns the parser with the clock reading taken the moment parsing
        completed — the §14.17 processing-deadline anchor. `None` closes the
        connection with no response bytes.
        """

        parser = RequestParser()
        receive_deadline = admitted_at + self._timeouts.receive
        # The last three consumed octets, so a header terminator split across
        # reads is still found before the bytes behind it are consumed.
        tail = b""
        while not parser.done:
            if self._immediate.is_set():
                return None
            remaining = self._phase_deadline(receive_deadline) - self._clock()
            if remaining <= 0:
                return None
            try:
                connection.settimeout(remaining)
                # Peek first and consume only through the header terminator:
                # §30 rule 2 refuses a declared body without reading its
                # bytes, so octets a peer coalesced behind the terminating
                # empty line must stay in the kernel buffer, never drained.
                peeked = connection.recv(parser.receive_budget, socket.MSG_PEEK)
                if not peeked:
                    return None
                terminator = (tail + peeked).find(b"\r\n\r\n")
                if terminator >= 0:
                    take = terminator + 4 - len(tail)
                else:
                    take = len(peeked)
                chunk = connection.recv(take)
            except OSError:
                return None
            if not chunk:
                return None
            parser.feed(chunk)
            tail = (tail + chunk)[-3:]
        completed_at = self._clock()
        if self._phase_deadline(receive_deadline) - completed_at <= 0:
            # The absolute deadline is the boundary even when the final read
            # was already in flight at expiry: a request completed late is a
            # receive expiry, not an admitted request.
            return None
        return parser, completed_at

    def _decide(
        self,
        parser: RequestParser,
        deadline: float,
        lease: _AdmissionLease | None = None,
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
        return self._resolve_abandonable(request, deadline, lease)

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
        self,
        request: ParsedRequest,
        deadline: float,
        lease: _AdmissionLease | None = None,
    ) -> views.ViewPage | None:
        if self._isolate_resolver:
            return self._resolve_in_process(request, deadline, lease)
        return self._resolve_in_thread(request, deadline)

    def _resolve_in_process(
        self,
        request: ParsedRequest,
        deadline: float,
        lease: _AdmissionLease | None,
    ) -> views.ViewPage | None:
        if self._immediate.is_set() or self._drain_expired():
            return None
        if self._phase_deadline(deadline) - self._clock() <= 0:
            return views.processing_timeout_page()
        if not self._ensure_process_reaper():
            return self._within(views.internal_error_page(), deadline)
        try:
            receiver, sender = self._process_context.Pipe(duplex=False)
        except Exception:
            return self._within(views.internal_error_page(), deadline)
        try:
            cancelled = self._process_context.Event()
            start_allowed = self._process_context.Event()
            process = self._process_context.Process(
                target=_run_resolver_process,
                args=(
                    sender,
                    cancelled,
                    start_allowed,
                    self._resolver,
                    self._workspace,
                    request,
                    deadline,
                    self._busy_timeout_ms,
                ),
                daemon=True,
            )
        except Exception:
            receiver.close()
            sender.close()
            return self._within(views.internal_error_page(), deadline)
        handle = _ProcessHandle(
            process,
            starting=True,
            cancelled=cancelled,
            start_allowed=start_allowed,
        )
        with self._state_lock:
            self._handles.add(handle)
            forced = self._immediate.is_set()
        try:
            try:
                if not self._begin_process_start(handle, sender):
                    return self._within(views.internal_error_page(), deadline)
                if forced or self._drain_expired():
                    handle.wake()
                    return None
                remaining = self._phase_deadline(deadline) - self._clock()
                if remaining <= 0 or not handle.wait_started(remaining):
                    handle.wake()
                    return views.processing_timeout_page()
                if self._immediate.is_set() or self._drain_expired():
                    handle.wake()
                    return None
                if handle.start_failed:
                    return self._within(views.internal_error_page(), deadline)
                return self._receive_process_result(receiver, process, deadline)
            finally:
                if not handle.finish():
                    deferred_lease = None
                    if lease is not None and not handle.start_pending:
                        lease.defer()
                        deferred_lease = lease
                    self._reap_process(handle, deferred_lease)
                with self._state_lock:
                    self._handles.discard(handle)
        finally:
            receiver.close()

    def _begin_process_start(
        self, handle: _ProcessHandle, sender: Connection
    ) -> bool:
        if not self._process_start_slots.acquire(blocking=False):
            sender.close()
            handle.complete_start(failed=True)
            return False

        def start() -> None:
            failed = False
            try:
                self._start_resolver_process(handle.process)
            except BaseException:
                failed = True
            finally:
                sender.close()
                handle.complete_start(failed=failed)
                self._process_start_slots.release()

        starter = threading.Thread(
            target=start, name="view-resolver-start", daemon=True
        )
        try:
            starter.start()
        except RuntimeError:
            sender.close()
            handle.complete_start(failed=True)
            self._process_start_slots.release()
            return False
        return True

    def _start_resolver_process(self, process) -> None:
        try:
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK, {signal.SIGINT}
            )
        except AttributeError:
            process.start()
            return
        try:
            process.start()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def _receive_process_result(
        self, receiver: Connection, process, deadline: float
    ) -> views.ViewPage | None:
        buffer = bytearray()
        payload_size: int | None = None
        process_done = False
        eof = False
        os.set_blocking(receiver.fileno(), False)
        while True:
            if self._immediate.is_set():
                return None
            if self._drain_expired():
                return None
            if process_done and eof:
                break

            remaining = self._phase_deadline(deadline) - self._clock()
            if remaining <= 0:
                return views.processing_timeout_page()
            ready = wait_for_connections(
                (receiver, process.sentinel), remaining
            )
            process_done = process_done or process.sentinel in ready
            if receiver in ready:
                while True:
                    try:
                        chunk = os.read(receiver.fileno(), 65536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        eof = True
                        break
                    buffer.extend(chunk)
                    if payload_size is None and len(buffer) >= _RESULT_LENGTH.size:
                        payload_size = _RESULT_LENGTH.unpack_from(buffer)[0]
                    if payload_size is not None:
                        framed_size = _RESULT_LENGTH.size + payload_size
                        if len(buffer) >= framed_size:
                            if len(buffer) != framed_size:
                                return self._within(
                                    views.internal_error_page(), deadline
                                )
                            eof = True
                            break

        if payload_size is None or len(buffer) != _RESULT_LENGTH.size + payload_size:
            return self._within(views.internal_error_page(), deadline)
        try:
            page = pickle.loads(buffer[_RESULT_LENGTH.size :])
        except Exception:
            return self._within(views.internal_error_page(), deadline)
        if not isinstance(page, views.ViewPage):
            return self._within(views.internal_error_page(), deadline)
        return self._within(page, deadline)

    def _reap_process(
        self, handle: _ProcessHandle, lease: _AdmissionLease | None
    ) -> None:
        self._reap_queue.put_nowait((handle, lease))

    def _ensure_process_reaper(self) -> bool:
        with self._state_lock:
            if self._reap_thread is not None:
                return self._reap_thread.is_alive()
            if self._reap_stop.is_set():
                return False
            reaper = threading.Thread(
                target=self._process_reap_loop,
                name="view-resolver-reap",
                daemon=True,
            )
            try:
                reaper.start()
            except RuntimeError:
                return False
            self._reap_thread = reaper
        return True

    def _process_reap_loop(self) -> None:
        pending: list[tuple[_ProcessHandle, _AdmissionLease | None]] = []
        while True:
            try:
                pending.append(
                    self._reap_queue.get(timeout=_PROCESS_REAP_POLL_SECONDS)
                )
            except queue.Empty:
                pass
            retained = []
            for handle, lease in pending:
                if handle.finish():
                    if lease is not None:
                        lease.release_deferred()
                else:
                    retained.append((handle, lease))
            pending = retained
            if self._reap_stop.is_set() and not pending:
                with self._state_lock:
                    if self._active == 0 and self._reap_queue.empty():
                        return

    def _stop_process_reaper(self) -> None:
        self._reap_stop.set()

    def _resolve_in_thread(
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
            if self._drain_expired():
                # Drain expiry closes without a response, and `serve` may
                # already have returned its cancellation class: a connection
                # thread descheduled between complete receipt and this point
                # must not open a read that §14.17 has refused. Expiry does
                # not set `_immediate`, so the flag above cannot see it, and
                # the zero-length wait below would only abandon work already
                # started. §14.17 forbids leaving expired work holding a
                # transaction or request resource — the cheapest way to keep
                # that is never to create it.
                return None
            worker = threading.Thread(
                target=self._run_resolver, args=(request, deadline, handle), daemon=True
            )
            try:
                worker.start()
            except RuntimeError:
                # The process cannot create another thread. §30 rule 7 still
                # owes this complete admitted request exactly one outcome, and
                # this connection's own thread is alive to emit it, so the
                # unexpected local failure becomes the fixed `internal_error`
                # page rather than a close with no response.
                return self._within(views.internal_error_page(), deadline)
            finished = handle.done.wait(
                max(0.0, self._phase_deadline(deadline) - self._clock())
            )
            if self._immediate.is_set():
                handle.abandon()
                return None
            if self._drain_expired():
                # Drain expiry closes without a response; the forced close,
                # not this thread, may already have woken us.
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
        self,
        connection: socket.socket,
        page: views.ViewPage,
        processing_deadline: float,
        *,
        head: bool,
    ) -> views.ViewPage:
        """Compose inside the processing budget, then send under the emit one.

        §14.17 places response composition inside processing: serialization
        that outlives the ordinary processing deadline turns the outcome into
        the fixed `processing_timeout` page, which is what gets the ordinary
        emit allowance. An expiring drain is not that deadline — it shortens
        every send below but never rewrites a composed outcome. The emit
        deadline starts before the first byte, after the
        outcome was composed. Expiry or a transport failure closes without
        retry — a partial response is acceptable and never changes the
        computed outcome. Returns the page actually emitted.
        """

        payload = compose_response(page, head=head)
        if self._clock() >= processing_deadline:
            # The ordinary processing deadline alone, never `min(phase,
            # drain)`: §30 rule 7 lets drain expiry truncate delivery after
            # the outcome is composed, but neither creates a second outcome
            # nor emits an alternate response, so a drain that expires during
            # serialization closes this connection with the page it already
            # computed rather than relabelling it a timeout.
            page = views.processing_timeout_page()
            payload = compose_response(page, head=head)
        deadline = self._clock() + self._timeouts.emit
        response = memoryview(payload)
        while response:
            if self._immediate.is_set():
                return page
            remaining = self._phase_deadline(deadline) - self._clock()
            if remaining <= 0:
                return page
            try:
                connection.settimeout(remaining)
                sent = connection.send(response)
            except OSError:
                return page
            response = response[sent:]
        return page
