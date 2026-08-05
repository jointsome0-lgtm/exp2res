"""Socket-level §14.17/§30 transport tests over real loopback connections.

Every request here is invented Vera Example traffic against a stub resolver
or a freshly initialized synthetic workspace. Determinism is event-driven:
expiry tests block a phase on an Event or an unfinished request and assert
the observable release, never a calibrated sleep.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
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
from exp2res.services.view_http import RequestParser
from exp2res.services.view_server import (
    _AbandonedError,
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
) -> Iterator[Rig]:
    bind = free_bind(host)
    server = ViewServer(
        workspace,
        bind,
        report=report,
        _resolver=resolver if resolver is not None else page_resolver(MARKER_PAGE),
        _timeouts=timeouts,
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


def test_socket_creation_failure_fails_closed_as_bind_failed(tmp_path, monkeypatch):
    """A refused socket creation — disabled family, exhausted descriptors —
    is the same operating-system bind refusal as a failing `bind` call."""

    server = ViewServer(tmp_path, free_bind())

    def refuse(*_args, **_kwargs):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(socket, "socket", refuse)
    with pytest.raises(ViewBindFailedError):
        server.open()


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


def test_stalled_reporter_does_not_consume_the_drain(tmp_path):
    """§14.17 reserves the drain for unfinished request work: a reporter
    blocked after its request closed and released everything must not make
    the drain wait, let alone expire."""

    gate = threading.Event()
    reported = threading.Event()

    def reporter(outcome, route):
        reported.set()
        gate.wait(30.0)

    try:
        with running_server(tmp_path, report=reporter) as rig:
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

    def reporter_thread_count() -> int:
        return sum(
            thread.name == "view-report" for thread in threading.enumerate()
        )

    before = reporter_thread_count()
    try:
        with running_server(tmp_path, report=reporter) as rig:
            for _ in range(8):
                assert status_of(rig.exchange(rig.request_bytes())) == 200
            assert entered.wait(10.0)
            # Eight completed requests behind a wedged reporter added exactly
            # the one dedicated thread, never one blocked thread each.
            assert reporter_thread_count() == before + 1
    finally:
        gate.set()


def test_drain_expiry_forces_the_close_without_a_response(tmp_path):
    timeouts = Timeouts(receive=30.0, processing=30.0, emit=30.0, drain=0.2)
    bind = free_bind()
    server = ViewServer(
        tmp_path, bind, _resolver=page_resolver(MARKER_PAGE), _timeouts=timeouts
    )
    server.open()
    rig = Rig(server, bind)
    rig.thread.start()
    try:
        client = rig.connect()
        client.sendall(b"GET /mirr")  # held mid-receive, 30s phase budget
        server.interrupt()
        assert read_to_close(client) == b""
        client.close()
        rig.thread.join(15.0)
        assert not rig.thread.is_alive()
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
