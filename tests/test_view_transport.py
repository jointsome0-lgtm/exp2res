"""Socket-level §14.17/§30 transport tests over real loopback connections.

Every request here is invented Vera Example traffic against a stub resolver
or a freshly initialized synthetic workspace. Determinism is event-driven:
expiry tests block a phase on an Event or an unfinished request and assert
the observable release, never a calibrated sleep.
"""

from __future__ import annotations

from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import sqlite3
import threading
import time
from typing import Callable, Iterator

import pytest

from exp2res.errors import (
    ViewBindFailedError,
    ViewBindInvalidError,
    ViewBindNotLoopbackError,
)
from exp2res.services import views
from exp2res.services import view_server
from exp2res.services.view_http import ParsedRequest, RequestParser
from exp2res.services.view_server import (
    _AbandonedError,
    _AdmissionLease,
    _ProcessHandle,
    _WorkerHandle,
    BindAddress,
    MAX_CONNECTIONS,
    Timeouts,
    ViewServer,
    validate_bind,
)
from exp2res.storage.workspace import read_database, writer_database


pytestmark = [pytest.mark.lifecycle]

GENEROUS = Timeouts(receive=30.0, processing=30.0, emit=30.0, drain=30.0)

MARKER_PAGE = views.ViewPage(
    outcome="served",
    status=200,
    body=b"<!doctype html><p>Vera Example marker page</p>",
)


class IsolatedResolverProbe:
    def __init__(self, context) -> None:
        self.entered = context.Event()
        self.calls = context.Value("i", 0)
        self.pid = context.Value("i", 0)

    def __call__(self, workspace, route, query, **_kwargs):
        with self.calls.get_lock():
            call = self.calls.value
            self.calls.value += 1
        if call == 0:
            self.pid.value = os.getpid()
            self.entered.set()
            time.sleep(30.0)
        return MARKER_PAGE


class SignalResolverProbe:
    def __init__(self, context) -> None:
        self.entered = context.Event()
        self.pid = context.Value("i", 0)

    def __call__(self, workspace, route, query, **_kwargs):
        self.pid.value = os.getpid()
        self.entered.set()
        time.sleep(0.5)
        return MARKER_PAGE


class BootstrapSignalResolverProbe:
    def __init__(self, context) -> None:
        self.entered = context.Event()
        self.pid = context.Value("i", 0)

    def __getstate__(self):
        return self.entered, self.pid

    def __setstate__(self, state) -> None:
        self.entered, self.pid = state
        self.pid.value = os.getpid()
        self.entered.set()
        time.sleep(0.5)

    def __call__(self, workspace, route, query, **_kwargs):
        return MARKER_PAGE


def free_bind(host: str = "127.0.0.1") -> BindAddress:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return BindAddress(host=host, port=probe.getsockname()[1])


def page_resolver(page: views.ViewPage) -> Callable[..., views.ViewPage]:
    def resolver(workspace, route, query, **_kwargs):
        return page

    return resolver


class Rig:
    def __init__(self, server: ViewServer, bind: BindAddress) -> None:
        self.server = server
        self.bind = bind
        self.result: str | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.result = self.server.serve()

    def connect(self, timeout: float = 10.0) -> socket.socket:
        return socket.create_connection((self.bind.host, self.bind.port), timeout)

    def request_bytes(
        self,
        target: bytes = b"/mirror?scope=global",
        method: bytes = b"GET",
        host: bytes | None = None,
        extra: tuple[bytes, ...] = (),
    ) -> bytes:
        host_value = self.bind.authority.encode("ascii") if host is None else host
        return b"\r\n".join(
            (
                method + b" " + target + b" HTTP/1.1",
                b"Host: " + host_value,
                *extra,
                b"",
                b"",
            )
        )

    def exchange(self, payload: bytes, timeout: float = 10.0) -> bytes:
        with self.connect(timeout) as client:
            client.sendall(payload)
            return read_to_close(client)


def read_to_close(client: socket.socket) -> bytes:
    # A close with unread request bytes arrives as a reset: for these
    # assertions that is the same observation as a clean zero-byte close.
    chunks = []
    while True:
        try:
            chunk = client.recv(65536)
        except ConnectionResetError:
            return b"".join(chunks)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


@contextmanager
def running_server(
    workspace: Path,
    *,
    host: str = "127.0.0.1",
    resolver: Callable[..., views.ViewPage] | None = None,
    timeouts: Timeouts = GENEROUS,
    report=None,
    isolate_resolver: bool | None = None,
    process_context=None,
) -> Iterator[Rig]:
    bind = free_bind(host)
    server = ViewServer(
        workspace,
        bind,
        report=report,
        _resolver=resolver if resolver is not None else page_resolver(MARKER_PAGE),
        _timeouts=timeouts,
        _isolate_resolver=isolate_resolver,
        _process_context=process_context,
    )
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()
    try:
        yield rig
    finally:
        server.interrupt()
        server.interrupt()
        rig.thread.join(15.0)
        assert not rig.thread.is_alive()


def outcome_of(response: bytes) -> bytes:
    for line in response.split(b"\r\n"):
        if line.lower().startswith(b"exp2res-view-outcome:"):
            return line.split(b":", 1)[1].strip()
    return b""


def status_of(response: bytes) -> int:
    return int(response.split(b" ", 2)[1])


def reporter_threads() -> set[threading.Thread]:
    """Every live reporter thread in the process, by identity.

    Reporters retire asynchronously, so an earlier test's thread may still be
    enumerated here and may die at any moment afterwards. Comparing sets of
    thread objects rather than counts keeps a neighbour's exit from moving
    this server's baseline in either direction.
    """

    return {
        thread for thread in threading.enumerate() if thread.name == "view-report"
    }


# --- bind validation -------------------------------------------------------


@pytest.mark.parametrize("host", ["localhost", "0.0.0.0", "127.0.0.2", "::", ""])
def test_validate_bind_refuses_every_non_literal_loopback_host(host):
    with pytest.raises(ViewBindNotLoopbackError):
        validate_bind(host, 8731)


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, -1, True])
def test_validate_bind_refuses_unusable_ports(port):
    with pytest.raises(ViewBindInvalidError):
        validate_bind("127.0.0.1", port)


def test_validate_bind_admits_both_loopback_literals():
    ipv4 = validate_bind("127.0.0.1", 8731)
    assert ipv4.authority == "127.0.0.1:8731"
    assert ipv4.origin == "http://127.0.0.1:8731"
    assert ipv4.url("/mirror", "scope=global") == (
        "http://127.0.0.1:8731/mirror?scope=global"
    )
    ipv6 = validate_bind("::1", 8731)
    assert ipv6.authority == "[::1]:8731"
    assert ipv6.origin == "http://[::1]:8731"


def test_occupied_bind_fails_closed_without_another_try(tmp_path):
    bind = free_bind()
    first = ViewServer(tmp_path, bind)
    first.open()
    try:
        with pytest.raises(ViewBindFailedError):
            ViewServer(tmp_path, bind).open()
    finally:
        first.interrupt()
        first.interrupt()
        first.serve()


def test_a_restart_can_take_the_same_address_right_after_serving(tmp_path):
    """A stop/start cycle is not blocked by the connection it just served.

    §30 rule 1 pins the restart to one literal address with no port 0 and no
    fallback, and every served connection closes from the server's side — so
    without address reuse the socket left in `TIME_WAIT` would refuse the
    next `open` until the kernel timeout expired.
    """

    with running_server(tmp_path) as rig:
        assert outcome_of(rig.exchange(rig.request_bytes())) == b"served"
        bind = rig.bind
    restarted = ViewServer(tmp_path, bind)
    restarted.open()
    restarted.interrupt()
    restarted.interrupt()
    restarted.serve()


@pytest.mark.parametrize(
    ("host", "port", "error"),
    [
        ("0.0.0.0", 8731, ViewBindNotLoopbackError),
        ("localhost", 8731, ViewBindNotLoopbackError),
        ("127.0.0.2", 8731, ViewBindNotLoopbackError),
        ("127.0.0.1", 0, ViewBindInvalidError),
        ("127.0.0.1", 80, ViewBindInvalidError),
    ],
)
def test_an_inadmissible_bind_cannot_be_constructed_at_all(
    tmp_path, host, port, error
):
    """§30 rule 1 refuses a non-loopback bind before a socket exists, and that
    refusal cannot depend on which entry point built the value: `BindAddress`
    is exported, and one built directly must never reach `bind` and expose
    owner-only views off the loopback interface. Nothing downstream — the
    authority and origin encodings included — ever sees an invalid host."""

    with pytest.raises(error):
        BindAddress(host=host, port=port)


def test_a_non_ascii_host_is_the_stable_loopback_refusal(tmp_path):
    # Not a `UnicodeEncodeError` from deriving the authority: the bind is
    # refused for what it is, with the stable §14.17 diagnostic.
    with pytest.raises(ViewBindNotLoopbackError):
        BindAddress(host="é", port=8731)


def test_an_interruption_before_open_takes_precedence_over_the_bind(tmp_path):
    """`open` is a public step a caller may take separately from `serve`, and
    `interrupt` is callable at any instant. An interruption that arrives
    first is not overtaken by a bind refusal for a socket never created."""

    occupied = free_bind()
    holder = ViewServer(tmp_path, occupied)
    holder.open()
    try:
        server = ViewServer(tmp_path, occupied, _timeouts=GENEROUS)
        server.interrupt()
        # The port is taken, so attempting the bind would raise instead.
        server.open()
        assert server._listener is None
        assert server.serve() == "drained"
    finally:
        holder.interrupt()
        holder.interrupt()
        holder.serve()


def test_socket_creation_failure_fails_closed_as_bind_failed(tmp_path, monkeypatch):
    """A refused socket creation — disabled family, exhausted descriptors —
    is the same operating-system bind refusal as a failing `bind` call."""

    server = ViewServer(tmp_path, free_bind())

    def refuse(*_args, **_kwargs):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(socket, "socket", refuse)
    with pytest.raises(ViewBindFailedError):
        server.open()


class AcceptFailsOnceServing:
    """The listener, but the accept after the first one is an OS refusal.

    The refusal waits for the admitted request to reach the resolver, so the
    failure is observed with request work provably in flight.
    """

    def __init__(self, listener: socket.socket, serving: threading.Event) -> None:
        self._listener = listener
        self._serving = serving
        self._accepted = False

    def accept(self):
        if self._accepted:
            assert self._serving.wait(10.0)
            raise OSError(24, "Too many open files")
        self._accepted = True
        return self._listener.accept()

    def __getattr__(self, name):
        return getattr(self._listener, name)


def test_an_unexpected_accept_failure_releases_the_admitted_work(tmp_path):
    """An `accept` the operating system refuses ends serving with no drain.
    The requests already admitted must not outlive the failing call: they are
    released before it propagates, so no socket, worker, or read transaction
    keeps running behind a command that has already reported."""

    entered = threading.Event()
    release = threading.Event()

    def resolver(workspace, route, query, **_kwargs):
        entered.set()
        release.wait(30.0)
        return MARKER_PAGE

    bind = free_bind()
    server = ViewServer(tmp_path, bind, _resolver=resolver, _timeouts=GENEROUS)
    server.open()
    server._listener = AcceptFailsOnceServing(server._listener, entered)
    rig = Rig(server, bind)
    failure: list[BaseException] = []

    def run() -> None:
        try:
            server.serve()
        except BaseException as error:
            failure.append(error)

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    try:
        client = rig.connect()
        client.sendall(rig.request_bytes())
        assert entered.wait(10.0)
        # Released by the failing accept, never by a drain, and while the
        # resolver is still blocked: no response, and no waiting on it.
        assert read_to_close(client) == b""
        client.close()
        runner.join(15.0)
        assert not runner.is_alive()
        assert failure and isinstance(failure[0], OSError)
    finally:
        release.set()
        runner.join(15.0)


def test_an_accept_failure_still_reports_the_request_that_completed(tmp_path):
    """The path with no drain still lets its producers reach the queue.

    A connection that had already emitted its response when `accept` failed
    would otherwise put its line behind a sentinel the unwinding call queued
    first, where no reporter is left to take it — and that request completed.
    """

    emitted = threading.Event()
    unwinding = threading.Event()
    delivered: list[tuple[str, str | None]] = []

    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        report=lambda outcome, route: delivered.append((outcome, route)),
        _resolver=page_resolver(MARKER_PAGE),
        _timeouts=GENEROUS,
    )
    server.open()
    server._listener = AcceptFailsOnceServing(server._listener, emitted)

    real_enqueue = server._enqueue_report
    real_force_close = server._force_close

    def enqueue_once_unwinding(line):
        emitted.set()
        assert unwinding.wait(10.0)
        # Give the unwinding call every chance to queue its sentinel first:
        # a reporter that retires here is one this line can never reach.
        for thread in reporter_threads():
            thread.join(0.5)
        real_enqueue(line)

    def force_close():
        unwinding.set()
        real_force_close()

    server._enqueue_report = enqueue_once_unwinding
    server._force_close = force_close

    rig = Rig(server, bind)
    failure: list[BaseException] = []

    def run() -> None:
        try:
            server.serve()
        except BaseException as error:
            failure.append(error)

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    assert status_of(rig.exchange(rig.request_bytes())) == 200
    runner.join(15.0)

    assert not runner.is_alive()
    assert failure and isinstance(failure[0], OSError)
    assert delivered == [("served", "/mirror")]


def test_an_interruption_before_the_bind_takes_precedence_over_it(tmp_path):
    """`interrupt` is callable at any instant, including before `serve` binds.
    §14.14 rule 6's cancellation is not overtaken by a bind refusal for a
    socket that was never created."""

    occupied = free_bind()
    holder = ViewServer(tmp_path, occupied)
    holder.open()
    try:
        server = ViewServer(tmp_path, occupied, _timeouts=GENEROUS)
        server.interrupt()
        # The port is taken, so attempting the bind would raise instead.
        assert server.serve() == "drained"
        assert server._listener is None
    finally:
        holder.interrupt()
        holder.interrupt()
        holder.serve()


def test_an_interruption_after_open_starts_no_reporter(tmp_path, monkeypatch):
    """Cancellation visible before `serve` starts wins without new work.

    `open` is a public step, so the first interruption can close an already
    bound listener before `serve` reaches its optional reporter startup. That
    cancelled server has no request to report and must return through its
    drain instead of creating a daemon whose queue can never receive a line.
    """

    starts: list[threading.Thread] = []
    server = ViewServer(
        tmp_path,
        free_bind(),
        report=lambda outcome, route: None,
        _timeouts=GENEROUS,
    )
    server.open()
    server.interrupt()

    def record_start(thread):
        starts.append(thread)

    monkeypatch.setattr(threading.Thread, "start", record_start)
    assert server.serve() == "drained"
    assert starts == []
    assert server._report_thread is None


def test_an_interruption_during_reporter_start_stops_the_unused_reporter(
    tmp_path, monkeypatch
):
    """A reporter started as cancellation becomes visible exits cleanly.

    The interruption can land after `serve` checks the drain flag but inside
    `Thread.start`. No request can then be admitted, so the post-start path
    must wake the otherwise-empty reporter instead of leaving its daemon
    blocked on the queue forever.
    """

    server = ViewServer(
        tmp_path,
        free_bind(),
        report=lambda outcome, route: None,
        _timeouts=GENEROUS,
    )
    server.open()
    real_start = threading.Thread.start

    def interrupt_then_start(thread):
        server.interrupt()
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", interrupt_then_start)
    reporters: list[threading.Thread] = []
    real_thread_init = threading.Thread.__init__

    def record(self, *args, **kwargs):
        real_thread_init(self, *args, **kwargs)
        if kwargs.get("name") == "view-report":
            reporters.append(self)

    monkeypatch.setattr(threading.Thread, "__init__", record)
    assert server.serve() == "drained"
    # `serve` releases its handle on the reporter, so the thread is captured
    # as it is constructed rather than read back off the server afterwards.
    assert len(reporters) == 1
    reporters[0].join(10.0)
    assert not reporters[0].is_alive()


def test_an_interruption_between_listen_and_publication_closes_the_socket(
    tmp_path, monkeypatch
):
    """The interruption that found no listener still frees the port.

    `interrupt` closes whatever `open` has published, and `serve` closes what
    it accepted through. A socket interrupted in the window between the two
    belongs to neither, and an embedding process would hold the address for
    its whole life while the command reported an orderly cancellation.
    """

    bind = free_bind()
    server = ViewServer(tmp_path, bind, _timeouts=GENEROUS)
    original = socket.socket.listen
    listeners: list[socket.socket] = []

    def listen(self, backlog):
        original(self, backlog)
        listeners.append(self)
        server.interrupt()

    monkeypatch.setattr(socket.socket, "listen", listen)
    server.open()
    monkeypatch.undo()

    announced: list[str] = []
    server.advertise(announced.append)

    assert announced == []
    assert len(listeners) == 1
    assert listeners[0].fileno() == -1
    assert server.serve() == "drained"


def test_a_refused_reporter_start_does_not_override_cancellation(
    tmp_path, monkeypatch
):
    """Cancellation that wins during startup remains the serve result."""

    server = ViewServer(
        tmp_path,
        free_bind(),
        report=lambda outcome, route: None,
        _timeouts=GENEROUS,
    )
    server.open()

    def interrupt_then_refuse(thread):
        server.interrupt()
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", interrupt_then_refuse)
    assert server.serve() == "drained"
    assert server._report_thread is None


def test_a_reporter_thread_the_os_refuses_gives_the_port_back(
    tmp_path, monkeypatch
):
    """`serve` binds before it starts the reporter, so a refused reporter
    thread must not strand the listener: the port has to come free for the
    caller that sees the failure, and the empty reporter slot has to stay
    empty so a retry starts one instead of serving without progress output."""

    bind = free_bind()
    server = ViewServer(
        tmp_path, bind, report=lambda outcome, route: None, _timeouts=GENEROUS
    )

    def refuse_start(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse_start)
    with pytest.raises(RuntimeError):
        server.serve()
    monkeypatch.undo()

    assert server._report_thread is None
    # The strongest evidence the listener was closed: the port rebinds.
    rebound = ViewServer(tmp_path, bind)
    rebound.open()
    rebound.interrupt()
    rebound.interrupt()
    rebound.serve()


class TickingClock:
    """Distinct increasing readings, and a signal on the first one taken."""

    def __init__(self, *readings: float) -> None:
        self._readings = iter(readings)
        self._last = readings[-1]
        self.first_read = threading.Event()

    def __call__(self) -> float:
        value = next(self._readings, self._last)
        self.first_read.set()
        return value


def test_the_drain_deadline_is_anchored_at_the_interruption_instant(tmp_path):
    """§14.17 starts the absolute drain deadline at the interruption instant.
    Time spent waiting for a connection thread to release the state lock is
    inside that allowance, never added to it."""

    clock = TickingClock(100.0, 200.0, 300.0)
    server = ViewServer(
        tmp_path,
        free_bind(),
        _clock=clock,
        _timeouts=Timeouts(receive=1.0, processing=1.0, emit=1.0, drain=5.0),
    )
    interrupting = threading.Thread(target=server.interrupt, daemon=True)
    with server._state_lock:
        interrupting.start()
        # Observable only because the instant is sampled before the lock is
        # contended. Anchoring inside the lock leaves nothing to see here,
        # and the deadline then grows by however long this block lasts.
        assert clock.first_read.wait(10.0)
    interrupting.join(10.0)
    assert not interrupting.is_alive()
    assert server._drain_deadline == 105.0


class AdvancingClock:
    """A monotone reading the test moves by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._now = start

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds


class SlowSlots:
    """Admission slots whose acquisition costs observable time."""

    def __init__(self, slots, clock: AdvancingClock, cost: float) -> None:
        self._slots = slots
        self._clock = clock
        self._cost = cost

    def acquire(self, blocking: bool = True) -> bool:
        taken = self._slots.acquire(blocking=blocking)
        if taken:
            self._clock.advance(self._cost)
        return taken

    def release(self) -> None:
        self._slots.release()


def test_the_receive_deadline_is_anchored_at_the_slot_acquisition(tmp_path):
    """§14.17 starts the receive deadline when the slot is acquired.

    Time spent reaching that acquisition belongs to no connection's budget:
    anchoring before the attempt would hand the admitted connection a
    deadline that a delay it never caused had already partly — here wholly —
    spent, closing it before it could send the request its own allowance
    covers.
    """

    clock = AdvancingClock()
    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        _resolver=page_resolver(MARKER_PAGE),
        _clock=clock,
        _timeouts=Timeouts(receive=5.0, processing=30.0, emit=30.0, drain=30.0),
    )
    server._slots = SlowSlots(server._slots, clock, 10.0)
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()
    try:
        response = rig.exchange(rig.request_bytes())
    finally:
        server.interrupt()
        server.interrupt()
        rig.thread.join(15.0)
        assert not rig.thread.is_alive()
    assert outcome_of(response) == b"served"


def test_an_expired_drain_starts_no_new_resolver(tmp_path, monkeypatch):
    """Expiry closes the connection; it never opens new request work.

    A connection thread descheduled between complete receipt and worker
    startup can resume after the drain deadline passed and `serve` already
    returned its cancellation class. Expiry does not set the forced-close
    flag, so only the drain deadline itself can refuse this. §14.17 forbids
    leaving expired work holding a transaction or request resource, and the
    cheapest way to keep that is never to create it.

    Observed at thread construction rather than through the resolver: once a
    worker exists it is abandoned immediately, so whether its target got to
    run is a scheduling question and could not discriminate.
    """

    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        _resolver=page_resolver(MARKER_PAGE),
        _timeouts=Timeouts(receive=30.0, processing=30.0, emit=30.0, drain=0.0),
    )
    # The first interruption anchors a drain deadline that is already spent.
    server.interrupt()
    assert server._drain_expired()

    targets = []
    real_thread = threading.Thread

    def recording_thread(*args, **kwargs):
        targets.append(kwargs.get("target"))
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(view_server.threading, "Thread", recording_thread)
    request = ParsedRequest(
        method=b"GET",
        path=b"/mirror",
        query=b"scope=global",
        host=bind.authority.encode("ascii"),
        origin=None,
        framing="bodyless",
    )
    assert server._resolve_abandonable(request, server._clock() + 30.0) is None
    assert server._run_resolver not in targets


# --- served responses and the closed response headers ----------------------


def test_served_response_carries_the_closed_header_set_and_reports(tmp_path):
    lines: list[tuple[str, str | None]] = []
    reported = threading.Event()

    def reporter(outcome: str, route: str | None) -> None:
        lines.append((outcome, route))
        reported.set()

    with running_server(tmp_path, report=reporter) as rig:
        response = rig.exchange(rig.request_bytes())
        # Reporting is asynchronous behind the bounded queue; wait for the
        # dedicated reporter thread to run the line.
        assert reported.wait(10.0)
    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"Exp2Res-View-Outcome: served\r\n" in response
    assert b"Cache-Control: no-store\r\n" in response
    assert b"Content-Type: text/html; charset=utf-8\r\n" in response
    assert b"Connection: close\r\n" in response
    assert response.endswith(MARKER_PAGE.body)
    assert lines == [("served", "/mirror")]


def test_head_returns_get_headers_with_an_empty_body(tmp_path):
    with running_server(tmp_path) as rig:
        get = rig.exchange(rig.request_bytes())
        head = rig.exchange(rig.request_bytes(method=b"HEAD"))
    get_headers = get.split(b"\r\n\r\n", 1)[0]
    assert head == get_headers + b"\r\n\r\n"
    assert b"Content-Length: %d\r\n" % len(MARKER_PAGE.body) in head


def test_ipv6_loopback_bind_serves_its_exact_authority(tmp_path):
    with running_server(tmp_path, host="::1") as rig:
        assert rig.bind.authority == f"[::1]:{rig.bind.port}"
        served = rig.exchange(rig.request_bytes())
        refused = rig.exchange(
            rig.request_bytes(host=b"127.0.0.1:%d" % rig.bind.port)
        )
    assert status_of(served) == 200
    assert status_of(refused) == 421


# --- rule 1: authority and declared origin ---------------------------------


def test_authority_must_match_the_bound_literal_exactly(tmp_path):
    with running_server(tmp_path) as rig:
        port = rig.bind.port
        cases = [
            b"localhost:%d" % port,
            b"127.0.0.1",
            b"127.0.0.1:%d" % ((port % 60000) + 1025),
            b"[::1]:%d" % port,
            b"example.invalid:%d" % port,
        ]
        for host_value in cases:
            response = rig.exchange(rig.request_bytes(host=host_value))
            assert status_of(response) == 421, host_value
            assert outcome_of(response) == b"authority_not_bound"
        # Host OWS is trimmed for exactly rule 1's comparison, so a padded
        # value naming the bound literal is served, not refused.
        padded = rig.exchange(
            rig.request_bytes(host=b" " + rig.bind.authority.encode() + b"\t")
        )
        assert status_of(padded) == 200
        served = rig.exchange(rig.request_bytes())
    assert status_of(served) == 200


def test_declared_origin_is_closed_and_literal(tmp_path):
    with running_server(tmp_path) as rig:
        origin = rig.bind.origin.encode("ascii")
        passes = [
            (),  # absent passes
            (b"Origin: " + origin,),
            (b"Origin:   " + origin + b" \t",),  # OWS trimmed
        ]
        for extra in passes:
            assert status_of(rig.exchange(rig.request_bytes(extra=extra))) == 200
        refused = [
            b"Origin:",
            b"Origin: null",
            b"Origin: https://127.0.0.1:%d" % rig.bind.port,
            b"Origin: http://127.0.0.1",
            b"Origin: HTTP://127.0.0.1:%d" % rig.bind.port,
            b"Origin: " + origin + b"/",
            b"Origin: " + origin + b" " + origin,
        ]
        for line in refused:
            response = rig.exchange(rig.request_bytes(extra=(line,)))
            assert status_of(response) == 421, line
            assert outcome_of(response) == b"authority_not_bound"
        repeated = rig.exchange(
            rig.request_bytes(
                extra=(b"Origin: " + origin, b"origin: " + origin)
            )
        )
    assert status_of(repeated) == 400
    assert outcome_of(repeated) == b"malformed_request"


# --- rules 2 and 7: methods, framing, and the check order ------------------


def test_method_matrix_and_allow_header(tmp_path):
    with running_server(tmp_path) as rig:
        for method in (b"POST", b"OPTIONS", b"DELETE", b"PUT", b"TRACE"):
            response = rig.exchange(rig.request_bytes(method=method))
            assert status_of(response) == 405, method
            assert outcome_of(response) == b"method_not_allowed"
            assert b"Allow: GET, HEAD\r\n" in response


def test_framing_matrix_over_the_socket(tmp_path):
    with running_server(tmp_path) as rig:
        bodyless = rig.exchange(
            rig.request_bytes(extra=(b"Content-Length: 0",))
        )
        assert status_of(bodyless) == 200
        declared = rig.exchange(
            rig.request_bytes(extra=(b"Content-Length: 5",)) + b"hello"
        )
        assert status_of(declared) == 400
        assert outcome_of(declared) == b"malformed_request"
        chunked = rig.exchange(
            rig.request_bytes(extra=(b"Transfer-Encoding: chunked",))
        )
        assert status_of(chunked) == 400
        doubled = rig.exchange(
            rig.request_bytes(
                extra=(b"Content-Length: 5", b"Content-Length: 5")
            )
        )
        assert status_of(doubled) == 400


def test_coalesced_body_bytes_stay_unread_in_the_kernel_buffer(tmp_path):
    """§30 rule 2: a declared body's bytes are never read, even when the
    peer coalesces them with the terminating empty line in one segment."""

    server = ViewServer(
        tmp_path,
        free_bind(),
        _resolver=page_resolver(MARKER_PAGE),
        _timeouts=GENEROUS,
    )
    ours, theirs = socket.socketpair()
    with ours, theirs:
        theirs.sendall(
            b"POST /mirror HTTP/1.1\r\n"
            b"Host: 127.0.0.1:1\r\n"
            b"Content-Length: 5\r\n"
            b"\r\n"
            b"hello"
        )
        received = server._receive(ours, time.monotonic())
        assert received is not None
        parser, _completed_at = received
        assert parser.request is not None
        assert parser.request.framing == "declared_body"
        leftover = ours.recv(64, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        assert leftover == b"hello"


class ScriptedSocket:
    """A recv-only stand-in whose buffer refills one scripted chunk at a
    time, so a header terminator split across reads is exercised
    deterministically."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._buffer = bytearray()
        self._chunks = list(chunks)

    def settimeout(self, value: float) -> None:
        pass

    def recv(self, size: int, flags: int = 0) -> bytes:
        if not self._buffer and self._chunks:
            self._buffer.extend(self._chunks.pop(0))
        taken = bytes(self._buffer[:size])
        if not flags & socket.MSG_PEEK:
            del self._buffer[: len(taken)]
        return taken

    @property
    def leftover(self) -> bytes:
        return bytes(self._buffer) + b"".join(self._chunks)


def test_body_bytes_behind_a_split_terminator_stay_unread(tmp_path):
    server = ViewServer(
        tmp_path,
        free_bind(),
        _resolver=page_resolver(MARKER_PAGE),
        _timeouts=GENEROUS,
    )
    connection = ScriptedSocket(
        (
            b"POST /mirror HTTP/1.1\r\n"
            b"Host: 127.0.0.1:1\r\n"
            b"Content-Length: 5\r\n"
            b"\r",
            b"\nhello",
        )
    )
    received = server._receive(connection, time.monotonic())
    assert received is not None
    parser, _completed_at = received
    assert parser.request is not None
    assert parser.request.framing == "declared_body"
    assert connection.leftover == b"hello"


def test_refusals_run_in_rule_7_order(tmp_path):
    with running_server(tmp_path) as rig:
        # Transport parse failure decides before the authority check.
        parse_first = rig.exchange(
            rig.request_bytes(
                host=b"evil.invalid",
                extra=(b"Transfer-Encoding: chunked",),
            )
        )
        assert outcome_of(parse_first) == b"malformed_request"
        # Authority decides before the method.
        authority_first = rig.exchange(
            rig.request_bytes(method=b"POST", host=b"evil.invalid")
        )
        assert outcome_of(authority_first) == b"authority_not_bound"
        # The method decides before the declared body.
        method_first = rig.exchange(
            rig.request_bytes(method=b"POST", extra=(b"Content-Length: 5",))
            + b"hello"
        )
        assert outcome_of(method_first) == b"method_not_allowed"
        # An accepted method with a declared body is refused unread.
        body_refused = rig.exchange(
            rig.request_bytes(extra=(b"Content-Length: 5",)) + b"hello"
        )
        assert outcome_of(body_refused) == b"malformed_request"


def test_malformed_request_line_is_refused_with_400(tmp_path):
    with running_server(tmp_path) as rig:
        response = rig.exchange(b"GET /mirror HTTP/1.0\r\nHost: x\r\n\r\n")
    assert status_of(response) == 400
    assert outcome_of(response) == b"malformed_request"


# --- the real resolver behind the transport --------------------------------


def test_real_resolver_answers_route_selector_and_state(workspace):
    with running_server(workspace, resolver=views.resolve) as rig:
        missing = rig.exchange(rig.request_bytes(target=b"/nope?scope=global"))
        assert status_of(missing) == 404
        assert outcome_of(missing) == b"route_not_found"
        no_view = rig.exchange(rig.request_bytes())
        assert status_of(no_view) == 404
        assert outcome_of(no_view) == b"no_current_view"
        deferred = rig.exchange(
            rig.request_bytes(target=b"/mirror?scope=project")
        )
        assert status_of(deferred) == 400
        assert outcome_of(deferred) == b"invalid_selector"
        # §30 rule 6: a malformed escape in a selector value is a refusal of
        # the value, so it survives transport parsing and reaches the
        # selector rather than being pre-empted as `malformed_request`. Only
        # a socket-level check sees this: the resolver never runs the parser.
        bad_escape = rig.exchange(
            rig.request_bytes(target=b"/mirror?snapshot=snapshot%2")
        )
        assert status_of(bad_escape) == 400
        assert outcome_of(bad_escape) == b"invalid_selector"
        # The path's own escapes stay rule 9's, before any route matching.
        bad_path = rig.exchange(rig.request_bytes(target=b"/mirror%2"))
        assert status_of(bad_path) == 400
        assert outcome_of(bad_path) == b"malformed_request"


# --- rule 10: bounded connection admission ---------------------------------


def test_33rd_connection_is_closed_unread_with_zero_response_bytes(tmp_path):
    with running_server(tmp_path) as rig:
        held: list[socket.socket] = []
        try:
            for _ in range(MAX_CONNECTIONS):
                client = rig.connect()
                client.sendall(b"G")  # keep the connection mid-receive
                held.append(client)
            overflow = rig.exchange(rig.request_bytes(), timeout=10.0)
            assert overflow == b""
            # Releasing admitted connections frees slots for new requests.
            for client in held[:2]:
                client.close()
            del held[:2]
            for _ in range(200):
                response = rig.exchange(rig.request_bytes())
                if response:
                    break
            assert status_of(response) == 200
        finally:
            for client in held:
                client.close()


# --- §14.17 absolute deadlines ---------------------------------------------


def test_receive_expiry_closes_with_no_response_bytes(tmp_path):
    timeouts = Timeouts(receive=0.2, processing=30.0, emit=30.0, drain=30.0)
    with running_server(tmp_path, timeouts=timeouts) as rig:
        with rig.connect() as client:
            client.sendall(b"GET /mirror?scope=gl")  # never completes
            assert read_to_close(client) == b""


def test_emit_expiry_truncates_a_stalled_reader_without_relabelling_it(tmp_path):
    """A client that stops reading loses the rest of its response, nothing more.

    The emit allowance starts after the outcome is composed, so its expiry
    closes the connection with the page already computed: no retry, no
    second outcome, and no connection held past its budget. A body well past
    the socket buffers is what makes the send block at all.
    """

    page = views.ViewPage(
        outcome="served",
        status=200,
        body=b"<!doctype html><p>Vera Example</p>" + b"x" * (8 * 1024 * 1024),
    )
    timeouts = Timeouts(receive=30.0, processing=30.0, emit=0.2, drain=30.0)
    delivered: list[tuple[str, str | None]] = []
    with running_server(
        tmp_path,
        resolver=page_resolver(page),
        timeouts=timeouts,
        report=lambda outcome, route: delivered.append((outcome, route)),
    ) as rig:
        with rig.connect() as client:
            client.sendall(rig.request_bytes())
            time.sleep(1.0)
            received = read_to_close(client)

    assert 0 < len(received) < len(page.body)
    assert received.startswith(b"HTTP/1.1 200 ")
    assert delivered == [("served", "/mirror")]


def test_processing_expiry_abandons_the_worker_and_frees_the_slot(tmp_path):
    release = threading.Event()
    entered = threading.Event()
    calls: list[int] = []

    def resolver(workspace, route, query, **_kwargs):
        calls.append(len(calls))
        if len(calls) == 1:
            entered.set()
            release.wait(30.0)
        return MARKER_PAGE

    timeouts = Timeouts(receive=30.0, processing=0.2, emit=30.0, drain=30.0)
    try:
        with running_server(tmp_path, resolver=resolver, timeouts=timeouts) as rig:
            with rig.connect() as first:
                first.sendall(rig.request_bytes())
                timed_out = read_to_close(first)
            assert entered.is_set()
            assert status_of(timed_out) == 503
            assert outcome_of(timed_out) == b"processing_timeout"
            # The slot is free and serving continues while the abandoned
            # worker is still blocked inside the stub.
            assert not release.is_set()
            second = rig.exchange(rig.request_bytes())
            assert status_of(second) == 200
            # The late worker's result is dropped: releasing it changes
            # nothing observable and the server keeps serving.
            release.set()
            third = rig.exchange(rig.request_bytes())
            assert status_of(third) == 200
    finally:
        release.set()


def test_processing_expiry_terminates_isolated_work_before_reusing_the_slot(
    tmp_path,
):
    context = multiprocessing.get_context("spawn")
    resolver = IsolatedResolverProbe(context)

    timeouts = Timeouts(receive=30.0, processing=2.0, emit=30.0, drain=30.0)
    with running_server(
        tmp_path,
        resolver=resolver,
        timeouts=timeouts,
        isolate_resolver=True,
        process_context=context,
    ) as rig:
        response = rig.exchange(rig.request_bytes())
        assert resolver.entered.is_set()
        assert status_of(response) == 503
        assert outcome_of(response) == b"processing_timeout"

        worker_pid = resolver.pid.value
        assert worker_pid > 0
        limit = time.monotonic() + 5.0
        while time.monotonic() < limit:
            try:
                os.kill(worker_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("the expired resolver process was not reaped")

        assert status_of(rig.exchange(rig.request_bytes())) == 200


def test_resolver_child_ignores_terminal_sigint(tmp_path):
    context = multiprocessing.get_context("spawn")
    resolver = SignalResolverProbe(context)
    timeouts = Timeouts(receive=30.0, processing=5.0, emit=30.0, drain=30.0)

    with running_server(
        tmp_path,
        resolver=resolver,
        timeouts=timeouts,
        isolate_resolver=True,
        process_context=context,
    ) as rig:
        with rig.connect() as client:
            client.sendall(rig.request_bytes())
            assert resolver.entered.wait(10.0)
            os.kill(resolver.pid.value, signal.SIGINT)
            response = read_to_close(client)

    assert status_of(response) == 200
    assert outcome_of(response) == b"served"


def test_resolver_child_blocks_sigint_during_spawn_bootstrap(tmp_path):
    context = multiprocessing.get_context("spawn")
    resolver = BootstrapSignalResolverProbe(context)
    timeouts = Timeouts(receive=30.0, processing=5.0, emit=30.0, drain=30.0)

    with running_server(
        tmp_path,
        resolver=resolver,
        timeouts=timeouts,
        isolate_resolver=True,
        process_context=context,
    ) as rig:
        with rig.connect() as client:
            client.sendall(rig.request_bytes())
            assert resolver.entered.wait(10.0)
            os.kill(resolver.pid.value, signal.SIGINT)
            response = read_to_close(client)

    assert status_of(response) == 200
    assert outcome_of(response) == b"served"


def test_partial_process_result_transfer_obeys_the_processing_deadline(tmp_path):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    sentinel, sentinel_writer = os.pipe()
    release = threading.Event()

    class IncompleteProcess:
        @property
        def sentinel(self):
            return sentinel

    def write_partial_result() -> None:
        os.write(sender.fileno(), view_server._RESULT_LENGTH.pack(1024 * 1024))
        os.write(sender.fileno(), b"partial")
        release.wait(2.0)
        sender.close()

    writer = threading.Thread(target=write_partial_result, daemon=True)
    writer.start()
    server = ViewServer(tmp_path, free_bind(), _timeouts=GENEROUS)
    started = time.monotonic()
    try:
        page = server._receive_process_result(
            receiver, IncompleteProcess(), started + 0.2
        )
    finally:
        release.set()
        writer.join(10.0)
        receiver.close()
        sender.close()
        os.close(sentinel)
        os.close(sentinel_writer)

    assert page is not None and page.outcome == "processing_timeout"
    assert time.monotonic() - started < 1.0


def test_resolver_process_finish_never_waits_for_uninterruptible_work():
    class UninterruptibleProcess:
        def is_alive(self):
            return True

        def kill(self):
            pass

        def join(self, timeout=None):
            if timeout is None:
                time.sleep(4.0)

    handle = _ProcessHandle(UninterruptibleProcess())
    started = time.monotonic()

    assert handle.finish() is False
    assert time.monotonic() - started < 1.0


def test_deferred_admission_is_not_reused_until_the_process_is_reaped():
    slots = threading.Semaphore(0)
    lease = _AdmissionLease(slots)

    lease.defer()
    lease.release()
    assert not slots.acquire(blocking=False)

    lease.release_deferred()
    assert slots.acquire(blocking=False)


def test_reaping_before_connection_close_does_not_release_admission():
    slots = threading.Semaphore(0)
    lease = _AdmissionLease(slots)

    lease.defer()
    lease.release_deferred()
    assert not slots.acquire(blocking=False)

    lease.release()
    assert slots.acquire(blocking=False)


def test_processing_expiry_interrupts_a_wedged_sqlite_reader(workspace):
    reader_exited = threading.Event()

    def stuck_reader(workspace_path, route, query, *, register_connection, **_kwargs):
        try:
            with read_database(workspace_path) as connection, register_connection(
                connection
            ):
                connection.execute(
                    "WITH RECURSIVE spin(x) AS "
                    "(SELECT 1 UNION ALL SELECT x + 1 FROM spin) "
                    "SELECT count(*) FROM spin"
                ).fetchone()
        finally:
            reader_exited.set()
        return MARKER_PAGE

    timeouts = Timeouts(receive=30.0, processing=0.2, emit=30.0, drain=30.0)
    with running_server(workspace, resolver=stuck_reader, timeouts=timeouts) as rig:
        response = rig.exchange(rig.request_bytes())
        assert status_of(response) == 503
        assert outcome_of(response) == b"processing_timeout"
        # The interrupt aborted the infinite statement and the read
        # transaction unwound through read_database's rollback path.
        assert reader_exited.wait(10.0)
        # With the reader released, a writer acquires the workspace at once.
        with writer_database(workspace) as connection:
            connection.execute("SELECT 1").fetchone()


def test_abandonment_aborts_a_statement_started_after_the_interrupt(workspace):
    # The abandonment window an interrupt alone misses: the worker is
    # between statements when expiry fires, then starts another read inside
    # the still-open transaction. The registered progress handler must abort
    # that later statement rather than let the reader run on.
    mid_transaction = threading.Event()
    resume = threading.Event()
    reader_exited = threading.Event()
    completed: list[bool] = []

    def resolver(workspace_path, route, query, *, register_connection, **_kwargs):
        try:
            with read_database(workspace_path) as connection, register_connection(
                connection
            ):
                connection.execute("SELECT 1").fetchone()
                mid_transaction.set()
                resume.wait(30.0)
                connection.execute(
                    "SELECT count(*) FROM schema_meta"
                ).fetchall()
                completed.append(True)
        finally:
            reader_exited.set()
        return MARKER_PAGE

    timeouts = Timeouts(receive=30.0, processing=0.2, emit=30.0, drain=30.0)
    with running_server(workspace, resolver=resolver, timeouts=timeouts) as rig:
        response = rig.exchange(rig.request_bytes())
        assert status_of(response) == 503
        assert outcome_of(response) == b"processing_timeout"
        assert mid_transaction.is_set()
        # Only now does the abandoned worker try its next read.
        resume.set()
        assert reader_exited.wait(10.0)
        assert completed == []
        with writer_database(workspace) as connection:
            connection.execute("SELECT 1").fetchone()


def test_report_runs_after_the_terminal_close_and_slot_release(tmp_path):
    gate = threading.Event()
    seen: list[tuple[str, str | None]] = []

    def reporter(outcome, route):
        seen.append((outcome, route))
        gate.wait(30.0)

    try:
        with running_server(tmp_path, report=reporter) as rig:
            # The exchange completes — full response plus the terminal close
            # — while the reporter is still blocked, so reporting holds
            # neither the socket nor the slot.
            response = rig.exchange(rig.request_bytes())
            assert status_of(response) == 200
            deadline = time.monotonic() + 10.0
            while not seen and time.monotonic() < deadline:
                time.sleep(0.01)
            assert seen == [("served", "/mirror")]
            gate.set()
    finally:
        gate.set()


def test_registration_after_abandonment_stops_the_worker_immediately():
    # An interrupt reaches only running statements, so a worker that
    # registers its read after the abandonment must be stopped by the
    # registration itself rather than allowed to read on.
    handle = _WorkerHandle()
    handle.abandon()
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(_AbandonedError):
            with handle.register(connection):
                raise AssertionError("an abandoned worker must not reach here")
    finally:
        connection.close()


def test_expired_processing_deadline_outweighs_a_pre_state_refusal(tmp_path):
    # §14.17: the processing deadline is an outer boundary over every check,
    # so even an authority refusal composed after expiry is the fixed
    # timeout outcome.
    server = ViewServer(tmp_path, free_bind(), _timeouts=GENEROUS)
    parser = RequestParser()
    parser.feed(
        b"GET /mirror?scope=global HTTP/1.1\r\nHost: evil.invalid\r\n\r\n"
    )
    assert parser.done and not parser.malformed
    page = server._decide(parser, time.monotonic() - 1.0)
    assert page is not None and page.outcome == "processing_timeout"
    fresh = server._decide(parser, time.monotonic() + 30.0)
    assert fresh is not None and fresh.outcome == "authority_not_bound"


def test_a_resolver_process_that_cannot_start_is_the_fixed_internal_error(tmp_path):
    # §30 rule 7 owes every complete admitted request exactly one outcome. A
    # process the operating system refuses to create is an unexpected local
    # failure, and the connection's own thread is still alive to answer it,
    # so the request gets the fixed 500 rather than a close with no response.
    bind = free_bind()

    class RefusingProcess:
        def start(self):
            raise RuntimeError("can't start new process")

    class RefusingContext:
        def Pipe(self, *, duplex):
            return multiprocessing.get_context("spawn").Pipe(duplex=duplex)

        def Process(self, **_kwargs):
            return RefusingProcess()

    server = ViewServer(
        tmp_path, bind, _timeouts=GENEROUS, _process_context=RefusingContext()
    )
    parser = RequestParser()
    parser.feed(
        b"GET /mirror?scope=global HTTP/1.1\r\nHost: "
        + bind.authority.encode("ascii")
        + b"\r\n\r\n"
    )
    assert parser.done and not parser.malformed

    page = server._decide(parser, time.monotonic() + 30.0)
    assert page is not None
    assert page.outcome == "internal_error"
    assert page.status == 500


def test_processing_expiry_during_composition_composes_the_503(tmp_path):
    # §14.17 places response composition inside processing: a payload whose
    # serialization outlives the deadline is not the outcome — the fixed
    # timeout page is, and it gets the ordinary emit allowance.
    server = ViewServer(tmp_path, free_bind(), _timeouts=GENEROUS)
    ours, theirs = socket.socketpair()
    with ours, theirs:
        emitted = server._emit(
            ours, MARKER_PAGE, time.monotonic() - 1.0, head=False
        )
        ours.close()
        response = read_to_close(theirs)
    assert emitted.outcome == "processing_timeout"
    assert status_of(response) == 503
    assert outcome_of(response) == b"processing_timeout"

    server_drained = ViewServer(tmp_path, free_bind(), _timeouts=GENEROUS)
    # §30 rule 7: an expiring drain may truncate delivery after the outcome
    # is composed, but it never creates a second outcome. Only the ordinary
    # processing deadline — still far away here — can relabel a composed page.
    server_drained.interrupt()
    with server_drained._state_lock:
        server_drained._drain_deadline = time.monotonic() - 1.0
    ours, theirs = socket.socketpair()
    with ours, theirs:
        emitted = server_drained._emit(
            ours, MARKER_PAGE, time.monotonic() + 30.0, head=False
        )
        ours.close()
        response = read_to_close(theirs)
    assert emitted.outcome == "served"
    # Delivery is what the expired drain truncates: no bytes, same outcome.
    assert response == b""

    server_fresh = ViewServer(tmp_path, free_bind(), _timeouts=GENEROUS)
    ours, theirs = socket.socketpair()
    with ours, theirs:
        emitted = server_fresh._emit(
            ours, MARKER_PAGE, time.monotonic() + 30.0, head=False
        )
        ours.close()
        response = read_to_close(theirs)
    assert emitted.outcome == "served"
    assert status_of(response) == 200


# --- interruption drain and forced shutdown --------------------------------


def test_first_interrupt_drains_and_in_flight_requests_complete(tmp_path):
    release = threading.Event()
    entered = threading.Event()

    def resolver(workspace, route, query, **_kwargs):
        entered.set()
        release.wait(30.0)
        return MARKER_PAGE

    bind = free_bind()
    server = ViewServer(
        tmp_path, bind, _resolver=resolver, _timeouts=GENEROUS
    )
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()
    try:
        client = rig.connect()
        client.sendall(rig.request_bytes())
        assert entered.wait(10.0)
        server.interrupt()
        # The listener is closed: nothing new is accepted during the drain.
        with pytest.raises(OSError):
            rig.connect(timeout=2.0)
        # The in-flight request still completes inside the drain window.
        release.set()
        response = read_to_close(client)
        client.close()
        assert status_of(response) == 200
        rig.thread.join(15.0)
        assert not rig.thread.is_alive()
        assert rig.result == "drained"
    finally:
        release.set()
        server.interrupt()
        server.interrupt()
        rig.thread.join(15.0)


class ThreadRefusingConnections:
    """A `threading.Thread` stand-in the way an exhausted thread table looks.

    Only the per-connection thread is refused, so the accept loop keeps
    running and every other thread the test needs still starts.
    """

    def __init__(self, *args, target=None, **kwargs) -> None:
        self._refuse = target is not None and target.__name__ == "_serve_connection"
        self._thread = threading.Thread(*args, target=target, **kwargs)

    def start(self) -> None:
        if self._refuse:
            raise RuntimeError("can't start new thread")
        self._thread.start()

    def __getattr__(self, name):
        return getattr(self._thread, name)


def test_a_connection_thread_the_os_refuses_gives_back_its_admission(
    tmp_path, monkeypatch
):
    """§30 rule 10 holds a slot only for a connection that is being served.
    When the operating system refuses the connection thread there is nobody
    left to run that connection's release, so the accept loop performs it:
    the socket closes unread, the slot and the drain count come back, and
    serving continues."""

    timeouts = Timeouts(receive=30.0, processing=30.0, emit=30.0, drain=0.3)
    with running_server(tmp_path, timeouts=timeouts) as rig:
        monkeypatch.setattr(
            view_server.threading, "Thread", ThreadRefusingConnections
        )
        refused = rig.connect()
        refused.sendall(rig.request_bytes())
        # Rule 10's unread close: no thread ever existed to compose a reply.
        assert read_to_close(refused) == b""
        refused.close()
        monkeypatch.undo()

        # The listener survived the refusal and the slot came back.
        assert status_of(rig.exchange(rig.request_bytes())) == 200

        # The strongest evidence that the drain count came back too: a leaked
        # count would keep `_drain` waiting until its 0.3s deadline expired.
        rig.server.interrupt()
        rig.thread.join(10.0)
        assert not rig.thread.is_alive()
        assert rig.result == "drained"


class DrainAtAcceptClock:
    """Interrupts exactly at the serve thread's post-`accept` clock read.

    That read is the accept loop's first clock call, so firing there puts the
    first interruption in the one window the loop condition cannot see: the
    connection is already accepted, and the admission decision that follows
    is what has to refuse it.
    """

    def __init__(self) -> None:
        self.server: ViewServer | None = None
        self.serve_thread: threading.Thread | None = None
        self.fired = threading.Event()

    def __call__(self) -> float:
        now = time.monotonic()
        if (
            not self.fired.is_set()
            and self.serve_thread is not None
            and threading.current_thread() is self.serve_thread
        ):
            # Set before interrupting: `interrupt` reads this same clock.
            self.fired.set()
            assert self.server is not None
            self.server.interrupt()
        return now


def test_a_backlog_connection_accepted_at_the_drain_boundary_is_refused(tmp_path):
    """§14.17: the first interruption stops accepting connections at that
    instant. Closing the listener does not empty the kernel backlog, so
    `accept` can still hand back a queued socket after the drain began; that
    socket is closed unread and never becomes work the drain waits on."""

    clock = DrainAtAcceptClock()
    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        _resolver=page_resolver(MARKER_PAGE),
        _clock=clock,
        _timeouts=GENEROUS,
    )
    clock.server = server
    server.open()
    rig = Rig(server, bind)
    clock.serve_thread = rig.thread
    # Connected — and queued in the backlog — before the accept loop exists,
    # so the first `accept` returns it without waiting on anything.
    client = socket.create_connection((bind.host, bind.port), 10.0)
    try:
        client.sendall(rig.request_bytes())
        rig.thread.start()
        assert read_to_close(client) == b""
        rig.thread.join(15.0)
        assert not rig.thread.is_alive()
        assert clock.fired.is_set()
        assert rig.result == "drained"
    finally:
        client.close()
        server.interrupt()
        server.interrupt()
        rig.thread.join(15.0)


def test_a_stalled_reporter_never_outlives_the_drain_deadline(tmp_path):
    """§14.17's one absolute deadline bounds the reporter too.

    The drain itself never waits on a reporter: this one is blocked after its
    request closed and released everything, and the drain completes anyway.
    What follows is the flush of already-queued lines, and it ends at the
    same absolute deadline rather than adding an allowance of its own — so a
    writer that never returns cannot hold the class-9 envelope open.
    """

    gate = threading.Event()
    reported = threading.Event()

    def reporter(outcome, route):
        reported.set()
        gate.wait(30.0)

    timeouts = Timeouts(receive=30.0, processing=30.0, emit=30.0, drain=0.3)
    try:
        with running_server(tmp_path, report=reporter, timeouts=timeouts) as rig:
            response = rig.exchange(rig.request_bytes())
            assert status_of(response) == 200
            assert reported.wait(10.0)
            rig.server.interrupt()
            rig.thread.join(10.0)
            assert not rig.thread.is_alive()
            assert rig.result == "drained"
    finally:
        gate.set()


def test_blocked_reporter_never_accumulates_threads(tmp_path):
    """One dedicated reporter thread serves the bounded queue: completed
    requests keep succeeding behind a wedged reporter without leaving one
    blocked thread each."""

    gate = threading.Event()
    entered = threading.Event()

    def reporter(outcome, route):
        entered.set()
        gate.wait(30.0)

    before = reporter_threads()
    try:
        with running_server(tmp_path, report=reporter) as rig:
            for _ in range(8):
                assert status_of(rig.exchange(rig.request_bytes())) == 200
            assert entered.wait(10.0)
            # Eight completed requests behind a wedged reporter added exactly
            # the one dedicated thread, never one blocked thread each.
            assert len(reporter_threads() - before) == 1
    finally:
        gate.set()


def test_a_finished_run_retires_its_reporter_after_serving_requests(tmp_path):
    """Serving ends the reporter, so an embedding process accumulates none.

    A process that serves once and exits would never notice, but a long-lived
    one — a test session, an embedding host — would otherwise leave one
    blocked thread and one live callback per serve-and-interrupt cycle.
    """

    delivered: list[tuple[str, str | None]] = []

    def reporter(outcome, route):
        delivered.append((outcome, route))

    before = reporter_threads()
    with running_server(tmp_path, report=reporter) as rig:
        assert status_of(rig.exchange(rig.request_bytes())) == 200
    rig.thread.join(30.0)
    assert not rig.thread.is_alive()

    for thread in reporter_threads() - before:
        thread.join(30.0)
    assert reporter_threads() - before == set()
    # The retirement is a stop, not a silencing: the completed request's own
    # line still reached the callback.
    assert delivered == [("served", "/mirror")]


def test_a_second_interruption_cuts_the_flush_short(tmp_path):
    """The immediate close outranks delivering diagnostics.

    A blocked writer would otherwise hold the class-9 envelope for the rest
    of the drain deadline, which is exactly what the second interruption
    exists to refuse.
    """

    gate = threading.Event()
    reported = threading.Event()

    def reporter(outcome, route):
        reported.set()
        gate.wait(30.0)

    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        report=reporter,
        _resolver=page_resolver(MARKER_PAGE),
        _timeouts=GENEROUS,
    )
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()
    try:
        assert status_of(rig.exchange(rig.request_bytes())) == 200
        assert reported.wait(10.0)
        server.interrupt()
        server.interrupt()
        rig.thread.join(10.0)

        assert not rig.thread.is_alive()
    finally:
        gate.set()


def test_a_completed_line_reaches_the_sink_before_the_run_returns(tmp_path):
    """A finished run has already reported what it served.

    The reporter is a daemon thread with a sink the invocation captured, so a
    line still in flight when `serve` returns races the caller: a CLI process
    exits, a runner closes that stderr, and the required route and outcome
    are simply lost. The reporter here takes long enough to write that only
    the bounded flush can close the gap.
    """

    delivered: list[tuple[str, str | None]] = []

    def reporter(outcome, route):
        time.sleep(0.05)
        delivered.append((outcome, route))

    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        report=reporter,
        _resolver=page_resolver(MARKER_PAGE),
        _timeouts=GENEROUS,
    )
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()

    assert status_of(rig.exchange(rig.request_bytes())) == 200
    server.interrupt()
    rig.thread.join(30.0)

    assert not rig.thread.is_alive()
    assert delivered == [("served", "/mirror")]


def test_a_completed_line_is_queued_before_the_drain_can_return(tmp_path):
    """The instant a slot comes back, the request's line is already queued.

    Releasing the admission is what lets a waiting drain return and retire
    the reporter. This drives the interruption from inside that release, so
    a line enqueued afterwards would provably arrive behind the sentinel and
    never reach the callback.
    """

    delivered: list[tuple[str, str | None]] = []
    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        report=lambda outcome, route: delivered.append((outcome, route)),
        _resolver=page_resolver(MARKER_PAGE),
        _timeouts=GENEROUS,
    )
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()

    real_release = server._release_admission
    shut_down = threading.Event()

    def release_then_shut_down(connection, lease=None):
        real_release(connection, lease)
        # The slot is back and the drain count is zero: this is the exact
        # instant `_drain` may return and stop the reporter.
        server.interrupt()
        rig.thread.join(30.0)
        shut_down.set()

    server._release_admission = release_then_shut_down

    assert status_of(rig.exchange(rig.request_bytes())) == 200
    assert shut_down.wait(30.0)
    assert not rig.thread.is_alive()
    for thread in threading.enumerate():
        if thread.name == "view-report":
            thread.join(30.0)

    assert delivered == [("served", "/mirror")]


def test_a_saturated_report_queue_still_delivers_the_stop_signal(tmp_path):
    """A wedged reporter can cost lines, never the reporter's own termination.

    The data slots are bounded independently of the queue, so the last queue
    slot stays reserved for the sentinel: once the callback recovers, the
    reporter drains what it holds and returns instead of blocking on an
    empty queue forever.
    """

    gate = threading.Event()
    entered = threading.Event()
    delivered: list[tuple[str, str | None]] = []

    def reporter(outcome, route):
        entered.set()
        gate.wait(30.0)
        delivered.append((outcome, route))

    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        report=reporter,
        _resolver=page_resolver(MARKER_PAGE),
        # A short drain, because the wedged reporter here makes the flush
        # spend that whole allowance: joining for exactly it would race.
        _timeouts=Timeouts(receive=30.0, processing=30.0, emit=30.0, drain=0.3),
    )
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()
    try:
        assert status_of(rig.exchange(rig.request_bytes())) == 200
        assert entered.wait(10.0)
        # Fill every data slot the semaphore grants while the reporter is
        # wedged inside the callback, leaving only the reserved slot free.
        for _ in range(view_server.REPORT_QUEUE_LIMIT + 8):
            server._enqueue_report(("served", "/mirror"))
        assert server._report_queue.full() is False
    finally:
        server.interrupt()
        rig.thread.join(30.0)
        gate.set()

    assert not rig.thread.is_alive()
    for thread in threading.enumerate():
        if thread.name == "view-report":
            thread.join(30.0)
    assert not any(thread.name == "view-report" for thread in threading.enumerate())


class DrainWhileReceivingClock:
    """Opens the drain from inside the connection thread's first clock read.

    Under a drain every phase wait becomes `min(phase, drain)`, so a wait
    *entered* after the drain opens ends at the drain deadline itself — the
    connection would then release its own slot at the same instant `_drain`
    wakes, and `serve` could legitimately report either class. Firing here
    removes that tie: `_receive` evaluates `_phase_deadline` before this
    clock read, so the receive wait already holds its full pre-drain budget
    and the connection is provably still unfinished when the drain expires.
    """

    def __init__(self) -> None:
        self.owner = threading.current_thread()
        self.server: ViewServer | None = None
        self.serve_thread: threading.Thread | None = None
        self.fired = threading.Event()

    def __call__(self) -> float:
        now = time.monotonic()
        current = threading.current_thread()
        if (
            not self.fired.is_set()
            and current is not self.owner
            and current is not self.serve_thread
        ):
            # Set before interrupting: `interrupt` reads this same clock.
            self.fired.set()
            assert self.server is not None
            self.server.interrupt()
        return now


def test_drain_expiry_forces_the_close_without_a_response(tmp_path):
    timeouts = Timeouts(receive=30.0, processing=30.0, emit=30.0, drain=0.2)
    clock = DrainWhileReceivingClock()
    bind = free_bind()
    server = ViewServer(
        tmp_path,
        bind,
        _resolver=page_resolver(MARKER_PAGE),
        _clock=clock,
        _timeouts=timeouts,
    )
    clock.server = server
    server.open()
    rig = Rig(server, bind)
    clock.serve_thread = rig.thread
    rig.thread.start()
    try:
        # No request bytes at all, so the connection takes exactly one
        # receive wait and stays parked in it: any byte sent here would let
        # the loop come round again and recompute that wait against the
        # drain deadline, which is the tie the clock above exists to avoid.
        client = rig.connect()
        assert read_to_close(client) == b""
        client.close()
        rig.thread.join(15.0)
        assert not rig.thread.is_alive()
        assert clock.fired.is_set()
        assert rig.result == "expired"
    finally:
        server.interrupt()
        server.interrupt()
        rig.thread.join(15.0)


def test_second_interrupt_shuts_down_immediately(tmp_path):
    release = threading.Event()
    entered = threading.Event()

    def resolver(workspace, route, query, **_kwargs):
        entered.set()
        release.wait(30.0)
        return MARKER_PAGE

    bind = free_bind()
    server = ViewServer(tmp_path, bind, _resolver=resolver, _timeouts=GENEROUS)
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()
    try:
        client = rig.connect()
        client.sendall(rig.request_bytes())
        assert entered.wait(10.0)
        server.interrupt()
        server.interrupt()
        assert read_to_close(client) == b""
        client.close()
        rig.thread.join(15.0)
        assert not rig.thread.is_alive()
        assert rig.result == "interrupted"
    finally:
        release.set()
