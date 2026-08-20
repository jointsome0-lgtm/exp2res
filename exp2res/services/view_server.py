"""§14.17's loopback socket transport in front of §30's projections: admission,
absolute phase deadlines on one injected clock, and interruption as state, never an exception."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
import json
import multiprocessing
from multiprocessing.connection import Connection, wait as wait_for_connections
from multiprocessing.context import BaseContext
import os
from pathlib import Path
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
from exp2res.services.view_http import (
    ParsedRequest,
    RequestParser,
    compose_response_parts,
)
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8731
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
MIN_PORT = 1024
MAX_PORT = 65535

GLOBAL_SELECTOR = "scope=global"

# §30 rule 10: fixed service constants, no flag/env/config representation.
MAX_CONNECTIONS = 32
LISTEN_BACKLOG = 32
# Progress lines held for a slow reporter before excess lines are dropped.
REPORT_QUEUE_LIMIT = 64
# Poll interval for the post-drain flush's two exits; not an allowance of its own.
_FLUSH_POLL_SECONDS = 0.05
_PROCESS_REAP_POLL_SECONDS = 0.05
_RESULT_LENGTH = struct.Struct("!Q")
_RESULT_METADATA_LIMIT = 4096

_SAFE_METHODS = (b"GET", b"HEAD")

# How serving ended; every value is §14.14 rule 6 cancellation to the §14.17 command.
ServeResult = Literal["drained", "expired", "interrupted"]

# One progress line per completed response: outcome class and closed route, nothing else (§14.17).
ReportLine = Callable[[str, str | None], None]
ReportItem = tuple[Callable[..., None], tuple[object, ...], bool]


def _check_bind(host: str, port: int) -> None:
    # §30 rule 1: names are never resolved; port 0 is refused because an
    # externally configured URL cannot name a port chosen at bind time (§14.17).
    if host not in LOOPBACK_HOSTS:
        raise ViewBindNotLoopbackError()
    if not isinstance(port, int) or isinstance(port, bool):
        raise ViewBindInvalidError()
    if port < MIN_PORT or port > MAX_PORT:
        raise ViewBindInvalidError()


@dataclass(frozen=True)
class BindAddress:
    """One literal loopback bind, unconstructable as anything else (§30 rule 1)."""

    host: str
    port: int

    def __post_init__(self) -> None:
        _check_bind(self.host, self.port)

    @property
    def authority(self) -> str:
        if ":" in self.host:
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.authority}"

    def url(self, route: str, selector: str) -> str:
        return f"{self.origin}{route}?{selector}"


def validate_bind(host: str, port: int) -> BindAddress:
    """§30 rule 1: refuse before a socket exists; `BindAddress` enforces the same."""

    _check_bind(host, port)
    return BindAddress(host=host, port=port)


def bound_urls(bind: BindAddress) -> tuple[str, ...]:
    """§14.17's two startup URLs in §30 rule 6's route order, derived from `views.ROUTES`."""

    return tuple(
        bind.url(route.decode("ascii"), GLOBAL_SELECTOR) for route in views.ROUTES
    )


@dataclass(frozen=True)
class Timeouts:
    """§14.17's absolute phase budgets in seconds; production derives them from §8.1 only."""

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
    """Stops an abandoned worker; deliberately outside the set `views.resolve` converts to outcomes."""


class _WorkerHandle:
    """Lock-guarded rendezvous between one connection and its worker; after `abandon`
    a late delivery is dropped, a running read interrupted, a later registration stopped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._abandoned = False
        self.done = threading.Event()
        self.page: views.ViewPage | None = None

    @contextmanager
    def register(self, connection: sqlite3.Connection) -> Iterator[None]:
        # `views.ConnectionRegistrar`. The progress handler also aborts statements
        # started after abandonment, which `interrupt()` alone cannot reach.
        with self._lock:
            abandoned = self._abandoned
            if not abandoned:
                self._connection = connection
        if abandoned:
            raise _AbandonedError()
        # n=1: checked at the first VM instruction, so short statements are caught too.
        connection.set_progress_handler(self._abort_when_abandoned, 1)
        try:
            yield
        finally:
            connection.set_progress_handler(None, 0)
            with self._lock:
                self._connection = None

    def _abort_when_abandoned(self) -> int:
        return 1 if self._abandoned else 0

    def deliver(self, page: views.ViewPage) -> None:
        with self._lock:
            if self._abandoned:
                return
            self.page = page
            # Set inside the lock: a page seen before the event would be
            # misclassified as expired.
            self.done.set()

    def abandon(self) -> None:
        with self._lock:
            self._abandoned = True
            connection = self._connection
        if connection is not None:
            # The worker may have closed it between the snapshot and here.
            with suppress(sqlite3.ProgrammingError):
                connection.interrupt()

    def wake(self) -> None:
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
    metadata = json.dumps(
        {
            "body_length": len(page.body),
            "content_type": page.content_type,
            "outcome": page.outcome,
            "published_member": page.published_member,
            "status": page.status,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    for part in (_RESULT_LENGTH.pack(len(metadata)), metadata, page.body):
        remaining = memoryview(part)
        while remaining:
            remaining = remaining[os.write(sender.fileno(), remaining) :]


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
            page = views.standard_page("internal_error")
        with suppress(BrokenPipeError, EOFError, OSError):
            _write_process_result(sender, page)
    finally:
        sender.close()


class ViewServer:
    """One bound loopback listener serving §30's closed route set, one daemon thread per admitted request."""

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
        # Reentrant: `interrupt()` may run in a signal handler on a thread
        # already holding the lock.
        self._state_lock = threading.RLock()
        self._sockets: set[socket.socket] = set()
        self._handles: set[_WorkerHandle | _ProcessHandle] = set()
        # Admitted connections not yet closed; the drain waits on this count,
        # never on threads.
        self._active = 0
        self._idle = threading.Condition(self._state_lock)
        self._draining = threading.Event()
        self._immediate = threading.Event()
        self._drain_deadline: float | None = None
        self._listener: socket.socket | None = None
        # One reporter thread behind a bounded queue: a blocked stderr stalls
        # only it and drops lines.
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
        """Bind exactly the validated address, or fail without another try."""

        if self._draining.is_set():
            # §14.14 rule 6: an earlier interruption outranks a bind not yet attempted.
            return
        family = socket.AF_INET6 if ":" in self.bind_address.host else socket.AF_INET
        try:
            listener = socket.socket(family, socket.SOCK_STREAM)
        except OSError as error:
            raise ViewBindFailedError() from error
        try:
            if family == socket.AF_INET6:
                # §30 rule 1: one literal address, so no IPv4 on the IPv6 socket.
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            # §30 rule 1 allows no fallback port, and this side's closes leave
            # `TIME_WAIT` entries, so a restart needs address reuse to rebind.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.bind_address.host, self.bind_address.port))
            listener.listen(LISTEN_BACKLOG)
        except OSError as error:
            listener.close()
            raise ViewBindFailedError() from error
        with self._state_lock:
            # Publication and the drain check are atomic: whichever side takes
            # the lock first owns the close, so no port is left held.
            if not self._draining.is_set():
                self._listener = listener
                return
        listener.close()

    def interrupt(self) -> None:
        """First call drains; a second forces the close. Never raises; signal-handler safe."""

        # Read before the lock: §14.17's drain deadline starts at the
        # interruption instant, lock waits included.
        interrupted_at = self._clock()
        with self._state_lock:
            # Classification and deadline write are atomic against a second interruption.
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
            # Shutdown before close: close alone does not wake a blocked accept.
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                listener.close()

    def serve(self) -> ServeResult:
        """Accept until interrupted, then drain; every exit retires the reporter."""

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
                # §14.14 rule 6: cancellation outranks a bind that never ran.
                return self._drain()
            self.open()
        listener = self._listener
        assert listener is not None
        if self._draining.is_set():
            # `open` is public, so an interruption may precede `serve`; no
            # reporter is started for an empty queue.
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
                    # §30 rule 10: closed unread, so it reaches no state.
                    connection.close()
                    continue
                # §14.17: the receive deadline starts at slot acquisition.
                admitted_at = self._clock()
                with self._state_lock:
                    # `accept` can return a backlog socket after `interrupt`
                    # closed the listener, so admission itself must check the
                    # drain boundary, atomically with the sweep's snapshot.
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
                    # No thread will run this connection's release; the peer
                    # gets §30 rule 10's unread close and serving continues.
                    self._release_admission(connection)
        except BaseException:
            # No drain on a failed `accept`: admitted requests must not outlive
            # the call before the failure reaches §14.17's envelope.
            self._force_close()
            raise
        finally:
            with suppress(OSError):
                listener.close()
        return self._drain()

    def _drain(self) -> ServeResult:
        """Await open requests until the absolute drain deadline, then close.

        Waits on the admitted count, never on threads: a wedged reporter or
        abandoned worker must not spend §14.17's drain budget.
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
            # §14.17: expiry returns without joining the force-closed threads.
            return "expired"
        return "drained"

    def _force_close(self) -> None:
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
        # §14.17: under a drain, every phase wait is `min(phase, drain)`.
        if not self._draining.is_set():
            return deadline
        with self._state_lock:
            drain = self._drain_deadline
        return deadline if drain is None else min(deadline, drain)

    def _drain_expired(self) -> bool:
        # Expiry does not set `_immediate`, so this is a connection thread's only signal.
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
                    # §30 rule 7: no complete request, nothing to answer.
                    return
                parser, completed_at = received
                # §14.17: the processing deadline starts at request completion.
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
                # §30 rule 6: no traceback or peer detail in diagnostics.
                pass
        finally:
            self._enqueue_report(line)
            self._release_admission(connection, lease)

    def _enqueue_report(self, line: tuple[str, str | None] | None) -> None:
        """Non-blocking hand-off of one line to the reporter, or drop it.

        Must run before the admission slot is released (a returning drain
        retires the reporter). The queue's extra slot is reserved for the
        stop sentinel, so saturation never swallows it.
        """

        if line is None or self._report is None:
            return
        if not self._report_slots.acquire(blocking=False):
            return
        self._report_queue.put_nowait((self._report, line, True))

    def _release_admission(
        self, connection: socket.socket, lease: _AdmissionLease | None = None
    ) -> None:
        # Order matters: close before deregistering (a concurrent forced close
        # must never miss a live peer); the drain count drops last.
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
        """Retire the reporter: every queued line precedes the sentinel, and the
        flush waits only what the §14.17 drain deadline has left."""

        with self._state_lock:
            reporter = self._report_thread
            self._report_thread = None
        if reporter is None:
            return
        self._await_producers()
        self._report_queue.put_nowait(None)
        self._flush(reporter)

    def _await_producers(self) -> None:
        # For the no-drain path (failed `accept`): let admitted requests queue
        # their lines ahead of the sentinel, bounded by the §8.1 timeout.
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
        # Sliced join so a second interruption is seen, not merely recorded.
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
        """Read one bounded envelope under the absolute receive deadline; `None` closes without bytes."""

        parser = RequestParser()
        receive_deadline = admitted_at + self._timeouts.receive
        # Last three octets, so a terminator split across reads is still found.
        tail = b""
        while not parser.done:
            if self._immediate.is_set():
                return None
            remaining = self._phase_deadline(receive_deadline) - self._clock()
            if remaining <= 0:
                return None
            try:
                connection.settimeout(remaining)
                # §30 rule 2: a declared body is refused unread, so consume
                # only through the header terminator.
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
            # A request completed late is a receive expiry, not admitted.
            return None
        return parser, completed_at

    def _decide(
        self,
        parser: RequestParser,
        deadline: float,
        lease: _AdmissionLease | None = None,
    ) -> views.ViewPage | None:
        """§30 rule 7's ordered pre-state refusals — authority, method, body — then the resolver."""

        if parser.malformed:
            return self._within(views.standard_page("malformed_request"), deadline)
        request = parser.request
        assert request is not None
        if request.host != self._authority:
            return self._within(views.standard_page("authority_not_bound"), deadline)
        if request.origin is not None and request.origin != self._origin:
            return self._within(views.standard_page("authority_not_bound"), deadline)
        if request.method not in _SAFE_METHODS:
            return self._within(views.standard_page("method_not_allowed"), deadline)
        if request.framing == "declared_body":
            return self._within(views.standard_page("malformed_request"), deadline)
        return self._resolve_abandonable(request, deadline, lease)

    def _within(self, page: views.ViewPage, deadline: float) -> views.ViewPage:
        # The processing deadline bounds every check, pre-state refusals included.
        if self._clock() < deadline:
            return page
        return views.standard_page("processing_timeout")

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
            return views.standard_page("processing_timeout")
        if not self._ensure_process_reaper():
            return self._within(views.standard_page("internal_error"), deadline)
        try:
            receiver, sender = self._process_context.Pipe(duplex=False)
        except Exception:
            return self._within(views.standard_page("internal_error"), deadline)
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
            return self._within(views.standard_page("internal_error"), deadline)
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
                    return self._within(views.standard_page("internal_error"), deadline)
                if forced or self._drain_expired():
                    handle.wake()
                    return None
                remaining = self._phase_deadline(deadline) - self._clock()
                if remaining <= 0 or not handle.wait_started(remaining):
                    handle.wake()
                    return views.standard_page("processing_timeout")
                if self._immediate.is_set() or self._drain_expired():
                    handle.wake()
                    return None
                if handle.start_failed:
                    return self._within(views.standard_page("internal_error"), deadline)
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
        metadata_size: int | None = None
        metadata: dict[str, object] | None = None
        body_offset: int | None = None
        body_size: int | None = None
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
                return views.standard_page("processing_timeout")
            ready = wait_for_connections(
                (receiver, process.sentinel), remaining
            )
            process_done = process_done or process.sentinel in ready
            if receiver in ready:
                while True:
                    if self._immediate.is_set() or self._drain_expired():
                        return None
                    if self._clock() >= deadline:
                        return views.standard_page("processing_timeout")
                    try:
                        chunk = os.read(receiver.fileno(), 65536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        eof = True
                        break
                    buffer.extend(chunk)
                    if self._immediate.is_set() or self._drain_expired():
                        return None
                    if self._clock() >= deadline:
                        return views.standard_page("processing_timeout")
                    if metadata_size is None and len(buffer) >= _RESULT_LENGTH.size:
                        metadata_size = _RESULT_LENGTH.unpack_from(buffer)[0]
                        if metadata_size > _RESULT_METADATA_LIMIT:
                            return self._within(
                                views.standard_page("internal_error"), deadline
                            )
                    if (
                        metadata_size is not None
                        and metadata is None
                        and len(buffer) >= _RESULT_LENGTH.size + metadata_size
                    ):
                        metadata_end = _RESULT_LENGTH.size + metadata_size
                        try:
                            decoded = json.loads(
                                buffer[_RESULT_LENGTH.size : metadata_end]
                            )
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            return self._within(
                                views.standard_page("internal_error"), deadline
                            )
                        if not isinstance(decoded, dict):
                            return self._within(
                                views.standard_page("internal_error"), deadline
                            )
                        candidate_body_size = decoded.get("body_length")
                        if (
                            not isinstance(candidate_body_size, int)
                            or isinstance(candidate_body_size, bool)
                            or candidate_body_size < 0
                        ):
                            return self._within(
                                views.standard_page("internal_error"), deadline
                            )
                        metadata = decoded
                        body_offset = metadata_end
                        body_size = candidate_body_size
                    if body_offset is not None and body_size is not None:
                        framed_size = body_offset + body_size
                        if len(buffer) >= framed_size:
                            if len(buffer) != framed_size:
                                return self._within(
                                    views.standard_page("internal_error"), deadline
                                )
                            eof = True
                            break

        if (
            metadata is None
            or body_offset is None
            or body_size is None
            or len(buffer) != body_offset + body_size
        ):
            return self._within(views.standard_page("internal_error"), deadline)
        outcome = metadata.get("outcome")
        status = metadata.get("status")
        published_member = metadata.get("published_member")
        content_type = metadata.get("content_type")
        if (
            not isinstance(outcome, str)
            or not isinstance(status, int)
            or isinstance(status, bool)
            or not isinstance(published_member, bool)
            or not isinstance(content_type, str)
        ):
            return self._within(views.standard_page("internal_error"), deadline)
        page = views.ViewPage(
            outcome=outcome,
            status=status,
            body=memoryview(buffer)[body_offset:].toreadonly(),
            published_member=published_member,
            content_type=content_type,
        )
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
        """Resolve in a one-shot worker the connection thread can abandon on expiry."""

        handle = _WorkerHandle()
        with self._state_lock:
            self._handles.add(handle)
        try:
            if self._immediate.is_set():
                # Closes the wake race with a forced close that snapshotted
                # the handle set before this registration.
                return None
            if self._drain_expired():
                # §14.17: never open a read the drain has already refused.
                return None
            worker = threading.Thread(
                target=self._run_resolver, args=(request, deadline, handle), daemon=True
            )
            try:
                worker.start()
            except RuntimeError:
                # §30 rule 7 still owes one outcome; this thread can emit it.
                return self._within(views.standard_page("internal_error"), deadline)
            finished = handle.done.wait(
                max(0.0, self._phase_deadline(deadline) - self._clock())
            )
            if self._immediate.is_set():
                handle.abandon()
                return None
            if self._drain_expired():
                handle.abandon()
                return None
            if not finished or handle.page is None:
                handle.abandon()
                return views.standard_page("processing_timeout")
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
            # Anything escaping `resolve` still yields one page.
            page = views.standard_page("internal_error")
        handle.deliver(page)

    def _emit(
        self,
        connection: socket.socket,
        page: views.ViewPage,
        processing_deadline: float,
        *,
        head: bool,
    ) -> views.ViewPage:
        """Compose inside the processing budget, then send under the emit one; returns the page emitted."""

        header, body = compose_response_parts(page, head=head)
        if self._clock() >= processing_deadline:
            # §14.17: composition is inside processing. The ordinary deadline
            # only, never `min(phase, drain)` — §30 rule 7 lets a drain
            # truncate delivery but never relabel a composed outcome.
            page = views.standard_page("processing_timeout")
            header, body = compose_response_parts(page, head=head)
        deadline = self._clock() + self._timeouts.emit
        for part in (header, body):
            response = memoryview(part)
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
