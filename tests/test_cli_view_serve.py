"""§14.17 `view serve`: bind admission, startup URLs, and the class-9 envelope.

These tests drive the real command through the Typer runner against real
loopback sockets. The published port is a fixed service constant, so every
test that actually binds passes an explicit `--port` taken from a probe
socket the operating system just released — `--port 0` is refused by the
public surface on purpose.

Interruption is exercised by signalling this process: the command installs
its own `SIGINT` handler, and only a real signal proves that the handler
reaches §14.17's drain instead of the default `KeyboardInterrupt`.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import os
import signal
import socket
import sqlite3
import sys
import threading
import time

import pytest
from typer.testing import CliRunner

from exp2res import cli
from exp2res.cli import app
from exp2res.services.view_server import DEFAULT_PORT, ViewServer


pytestmark = [pytest.mark.lifecycle]

runner = CliRunner()


def free_port(host: str = "127.0.0.1") -> int:
    """A port the operating system just released, so the bind is unattended."""

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def invoke(workspace: Path, *args: str, controls: tuple[str, ...] = ()) -> object:
    return runner.invoke(
        app, [*controls, "--workspace", str(workspace), "view", "serve", *args]
    )


def envelope(result) -> dict:
    """The one §14.14 envelope a `--json` run puts on stdout."""

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    return json.loads(lines[0])


@pytest.fixture
def serving(monkeypatch) -> threading.Event:
    """An event the command sets the instant it starts accepting.

    The handler is installed before the bind, so its presence no longer says
    the listener exists. A helper thread that connected on that signal alone
    could be refused and never send the interrupt, leaving the invocation
    running until the test session gave up. `serve` is entered only after
    `open` published a listening socket, which makes this the one signal that
    covers both the handler and the port.
    """

    started = threading.Event()
    original = ViewServer.serve

    def serve(self):
        started.set()
        return original(self)

    monkeypatch.setattr(ViewServer, "serve", serve)
    return started


def installed_handler(previous, deadline: float = 30.0):
    """Wait for the handler the serving command owns, then hand it back.

    Observing the handler is what proves the command still owns `SIGINT`: it
    installs the handler before the bind, and the runtime restores the
    previous one only after the envelope. A real `os.kill` is safe once that
    handler is observed, and only once per run: a second signal could land
    after the restore and reach the test runner instead of the command.
    Whether the *listener* is up is a separate question, which
    `handler_when_serving` answers.

    `previous` is whatever held `SIGINT` before the invocation started, read
    on the main thread by the caller. Identity against it is what makes the
    wait specific: a runner, IDE, or plugin may already have installed a
    callable handler of its own, and calling *that* one would leave the
    command serving forever.
    """

    limit = time.monotonic() + deadline
    while time.monotonic() < limit:
        handler = signal.getsignal(signal.SIGINT)
        if callable(handler) and handler is not previous:
            return handler
        time.sleep(0.005)
    raise AssertionError("the command never installed its interrupt handler")


def handler_when_serving(serving: threading.Event, previous, deadline: float = 30.0):
    """The command's handler, once its listener is also accepting."""

    assert serving.wait(deadline), "the command never started serving"
    return installed_handler(previous, deadline)


def interrupt_once_serving(
    serving: threading.Event, times: int = 1
) -> threading.Thread:
    """Interrupt the serving command from a helper thread, `times` times.

    Called on the main thread before the invocation, so the handler it reads
    now is the one the command is about to displace.
    """

    previous = signal.getsignal(signal.SIGINT)

    def run() -> None:
        handler = handler_when_serving(serving, previous)
        for _ in range(times):
            handler(signal.SIGINT, None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def nothing_listens(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), 0.5):
            return False
    except OSError:
        return True


@pytest.mark.parametrize(
    ("args", "diagnostic"),
    [
        (("--host", "0.0.0.0"), "view_bind_not_loopback"),
        (("--host", "localhost"), "view_bind_not_loopback"),
        (("--host", "::"), "view_bind_not_loopback"),
        (("--host", "127.0.0.2"), "view_bind_not_loopback"),
        (("--port", "0"), "view_bind_invalid"),
        (("--port", "1023"), "view_bind_invalid"),
        (("--port", "65536"), "view_bind_invalid"),
    ],
)
def test_an_inadmissible_bind_is_refused_before_a_socket_exists(
    workspace: Path, args: tuple[str, ...], diagnostic: str
) -> None:
    """§30 rule 1: wildcard, routable, and resolved names are all refused.

    `localhost` currently resolves to loopback and is still refused, because
    what it resolves to later is not Exp2Res's decision.
    """

    result = invoke(workspace, *args, controls=("--json",))

    assert result.exit_code == 2
    body = envelope(result)
    assert body["diagnostic_class"] == diagnostic
    assert body["status"] == "failed"
    assert body["command"] == "view serve"
    assert body["result"] is None
    # Nothing was served: the default port is the only one this run could
    # have taken, and no other port or interface is ever tried.
    assert nothing_listens("127.0.0.1", DEFAULT_PORT)


def test_a_bind_the_operating_system_refuses_tries_nothing_else(
    workspace: Path,
) -> None:
    """§14.17: an address already in use fails closed with nothing served."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        taken = holder.getsockname()[1]

        result = invoke(workspace, "--port", str(taken), controls=("--json",))

    assert result.exit_code == 2
    body = envelope(result)
    assert body["diagnostic_class"] == "view_bind_failed"
    assert body["result"] is None
    assert nothing_listens("127.0.0.1", DEFAULT_PORT)


def test_the_schema_gate_fails_closed_before_any_bind(workspace: Path) -> None:
    """§14.17: an incompatible workspace is exit class 4, not a served view."""

    port = free_port()
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        # A version this build cannot recognize: §12.14's gate has no
        # migration path to it, so serving must refuse rather than read.
        connection.execute("UPDATE schema_meta SET version = version + 1000")

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 4
    body = envelope(result)
    assert body["diagnostic_class"] == "schema_incompatible"
    assert body["result"] is None
    assert nothing_listens("127.0.0.1", port)


def test_startup_contention_is_class_five_not_an_unrecognized_workspace(
    workspace: Path,
) -> None:
    """§21.57: a blocked startup compatibility read is its own class.

    Contention says nothing about the stored version, so it must not be
    relabelled as an incompatible schema, answered as a per-request 503, or
    reached after the socket is already bound.
    """

    port = free_port()
    blocker = sqlite3.connect(
        workspace / ".exp2res" / "exp2res.sqlite", isolation_level=None
    )
    try:
        blocker.execute("PRAGMA locking_mode = EXCLUSIVE")
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("PRAGMA user_version = 1")

        result = invoke(workspace, "--port", str(port), controls=("--json",))
    finally:
        blocker.rollback()
        blocker.close()

    assert result.exit_code == 5
    body = envelope(result)
    assert body["diagnostic_class"] == "workspace_busy"
    assert body["result"] is None
    assert result.stderr.splitlines() == ["The workspace is busy."]
    assert nothing_listens("127.0.0.1", port)


def test_a_broken_managed_root_is_served_per_request_not_refused_at_startup(
    workspace: Path, serving: threading.Event
) -> None:
    """A broken `out/` entry is §30's business, not the startup gate's.

    §12.14's compatibility gate is about the schema this build can read. A
    managed root that is a symlink rather than a directory is a §13.14 rule 6
    containment failure, and this workspace has published nothing at all — so
    §30's ordering answers the request before revalidation is even reached.
    Either way the owner sees a served outcome, which is only possible if the
    command binds instead of refusing in front of it.
    """

    moved = workspace / "elsewhere"
    (workspace / "out").rename(moved)
    (workspace / "out").symlink_to(moved, target_is_directory=True)

    port = free_port()
    previous = signal.getsignal(signal.SIGINT)

    def request_then_interrupt() -> None:
        handler_when_serving(serving, previous)
        with socket.create_connection(("127.0.0.1", port), 10.0) as client:
            client.sendall(
                b"GET /mirror?scope=global HTTP/1.1\r\n"
                b"Host: 127.0.0.1:" + str(port).encode("ascii") + b"\r\n\r\n"
            )
            while client.recv(65536):
                pass
        handler_when_serving(serving, previous)(signal.SIGINT, None)

    thread = threading.Thread(target=request_then_interrupt, daemon=True)
    thread.start()

    result = invoke(workspace, "--port", str(port))
    thread.join(30.0)

    assert result.exit_code == 9
    assert result.stderr.splitlines()[2:] == ["/mirror no_current_view"]


@pytest.mark.parametrize("port", [1024, 65535])
def test_the_two_allowed_boundary_ports_bind_exactly_as_requested(
    workspace: Path, port: int, serving: threading.Event
) -> None:
    """§21.57: 1024 and 65535 are inside the allowed range, not just outside.

    The case is stated over free ports at those boundaries; a machine where
    something already holds one has no boundary bind to observe.
    """

    if not nothing_listens("127.0.0.1", port):
        pytest.skip(f"port {port} is already in use on this machine")

    interrupt_once_serving(serving)

    result = invoke(workspace, "--port", str(port))

    assert result.exit_code == 9
    assert result.stderr.splitlines() == [
        f"http://127.0.0.1:{port}/mirror?scope=global",
        f"http://127.0.0.1:{port}/questions?scope=global",
    ]


def test_an_interrupt_before_the_bind_cancels_without_advertising_a_url(
    workspace: Path, monkeypatch
) -> None:
    """The whole startup window belongs to the command's own handler.

    An interrupt between construction and the bind must reach `interrupt`,
    not the default `KeyboardInterrupt`: the run still ends in class 9, but
    only an installed handler keeps the second interrupt out of envelope
    assembly. Nothing was bound, so nothing is advertised.
    """

    port = free_port()
    original = ViewServer.open

    def interrupt_then_open(self):
        os.kill(os.getpid(), signal.SIGINT)
        return original(self)

    monkeypatch.setattr(ViewServer, "open", interrupt_then_open)

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 9
    assert envelope(result)["status"] == "cancelled"
    assert result.stderr == ""
    assert nothing_listens("127.0.0.1", port)


def test_an_interrupt_during_the_startup_gate_keeps_the_command_in_charge(
    workspace: Path, monkeypatch
) -> None:
    """Cancellation is installed before anything interruptible runs.

    The compatibility read can block on a busy workspace, and an interrupt
    there left to the default handler ends the operation but leaves envelope
    assembly unguarded — where the owner's second Ctrl-C raises past every
    catch in the runtime. Both interrupts land here, and the envelope still
    comes out.
    """

    port = free_port()
    gate = cli.require_compatible
    lines = cli._invalidated_view_lines

    def interrupt_then_check(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGINT)
        return gate(*args, **kwargs)

    def interrupt_then_report(views):
        os.kill(os.getpid(), signal.SIGINT)
        return lines(views)

    monkeypatch.setattr(cli, "require_compatible", interrupt_then_check)
    monkeypatch.setattr(cli, "_invalidated_view_lines", interrupt_then_report)

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 9
    assert envelope(result)["status"] == "cancelled"
    assert nothing_listens("127.0.0.1", port)


def test_an_interrupt_after_the_bind_advertises_no_unreachable_url(
    workspace: Path, monkeypatch
) -> None:
    """A URL is advertised only while its listener is still live.

    `interrupt` closes the socket `open` published, so an interrupt landing
    between them would otherwise hand the owner two addresses nothing is
    listening on.
    """

    port = free_port()
    original = ViewServer.open

    def open_then_interrupt(self):
        original(self)
        os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(ViewServer, "open", open_then_interrupt)

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 9
    assert envelope(result)["status"] == "cancelled"
    assert result.stderr == ""
    assert nothing_listens("127.0.0.1", port)


def test_a_second_interrupt_during_the_envelope_still_reports_class_nine(
    workspace: Path, monkeypatch, serving: threading.Event
) -> None:
    """The command owns `SIGINT` until §14.14 rule 6's envelope is written.

    An owner who sends the second Ctrl-C just as the first drain finishes
    lands it in the window between serving and output. Handing the signal
    back to the default handler at the end of serving would raise
    `KeyboardInterrupt` there, past every handler in the runtime, and the
    process would exit without the cancelled envelope.
    """

    port = free_port()
    interrupt_once_serving(serving)

    original = cli._invalidated_view_lines

    def interrupt_then_continue(views):
        os.kill(os.getpid(), signal.SIGINT)
        return original(views)

    monkeypatch.setattr(cli, "_invalidated_view_lines", interrupt_then_continue)

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 9
    assert envelope(result)["status"] == "cancelled"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_a_successful_bind_reports_exactly_two_urls_then_cancels(
    workspace: Path, host: str, serving: threading.Event
) -> None:
    """§14.17: both supported loopback forms bind and advertise both routes."""

    port = free_port(host)
    interrupt_once_serving(serving)

    result = invoke(workspace, "--host", host, "--port", str(port))

    assert result.exit_code == 9
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    assert result.stderr.splitlines() == [
        f"http://{authority}/mirror?scope=global",
        f"http://{authority}/questions?scope=global",
    ]
    # Human mode prints no result line for a command that reaches none.
    assert result.stdout == ""


def test_json_stdout_carries_exactly_one_cancelled_envelope(
    workspace: Path, serving: threading.Event
) -> None:
    """§14.17: the URLs stay on stderr, so `--json` stdout is one envelope."""

    port = free_port()
    interrupt_once_serving(serving)

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 9
    body = envelope(result)
    assert body["command"] == "view serve"
    assert body["status"] == "cancelled"
    assert body["exit_code"] == 9
    assert body["diagnostic_class"] == "cancelled"
    assert body["result"] is None
    assert body["workspace"] == str(workspace)
    # Individual requests never appear in the envelope (§14.17).
    assert body["affected_ids"] == {"created": [], "superseded": [], "deleted": []}
    assert body["generation_ids"] == []
    assert body["run_ids"] == []
    assert body["invalidated_views"] == []
    assert body["findings"] == []
    assert body["warnings"] == []
    assert body["residual_paths"] == []
    assert result.stderr.splitlines() == [
        f"http://127.0.0.1:{port}/mirror?scope=global",
        f"http://127.0.0.1:{port}/questions?scope=global",
    ]


def test_a_second_interruption_still_cancels(
    workspace: Path, serving: threading.Event
) -> None:
    """§14.17: the forced close keeps exit class 9 rather than another class."""

    port = free_port()
    interrupt_once_serving(serving, times=2)

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 9
    assert envelope(result)["status"] == "cancelled"


def test_a_real_interrupt_signal_drains_and_cancels(
    workspace: Path, serving: threading.Event
) -> None:
    """The installed handler, not the default `KeyboardInterrupt`, ends serving.

    Delivering the signal for real is what proves the wiring: the handler runs
    on the main thread between bytecodes while `accept` is blocked, and the
    command still reports §14.14 rule 6's class-9 envelope.
    """

    port = free_port()
    previous = signal.getsignal(signal.SIGINT)

    def run() -> None:
        handler_when_serving(serving, previous)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=run, daemon=True).start()

    result = invoke(workspace, "--port", str(port), controls=("--json",))

    assert result.exit_code == 9
    assert envelope(result)["status"] == "cancelled"


def test_the_default_port_is_the_documented_one(workspace: Path) -> None:
    """§14.17: `--port` defaults to 8731, so the advertised URL is stable."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        # Holding the default port makes the default-path bind fail without
        # this test ever serving on a port another test could reach.
        try:
            holder.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            pytest.skip("the default port is in use on this host")
        holder.listen(1)

        result = invoke(workspace, controls=("--json",))

    assert result.exit_code == 2
    assert envelope(result)["diagnostic_class"] == "view_bind_failed"


def test_a_served_request_prints_only_its_route_and_outcome_class(
    workspace: Path, serving: threading.Event
) -> None:
    """§30 rule 6: no request byte reaches a diagnostic line.

    The workspace has no published view, so the outcome is the closed
    `no_current_view` refusal — what matters here is that the line names the
    route literal and that class and carries nothing from the request.
    """

    port = free_port()
    previous = signal.getsignal(signal.SIGINT)

    def request_then_interrupt() -> None:
        handler_when_serving(serving, previous)
        with socket.create_connection(("127.0.0.1", port), 10.0) as client:
            client.sendall(
                b"GET /mirror?scope=global HTTP/1.1\r\n"
                b"Host: 127.0.0.1:" + str(port).encode("ascii") + b"\r\n"
                b"X-Vera-Example: probe-value\r\n"
                b"\r\n"
            )
            while client.recv(65536):
                pass
        handler_when_serving(serving, previous)(signal.SIGINT, None)

    thread = threading.Thread(target=request_then_interrupt, daemon=True)
    thread.start()

    result = invoke(workspace, "--port", str(port))
    thread.join(30.0)

    assert result.exit_code == 9
    lines = result.stderr.splitlines()
    assert lines[:2] == [
        f"http://127.0.0.1:{port}/mirror?scope=global",
        f"http://127.0.0.1:{port}/questions?scope=global",
    ]
    assert lines[2:] == ["/mirror no_current_view"]
    assert "probe-value" not in result.stderr
    assert str(workspace) not in result.stderr


def test_quiet_keeps_the_urls_and_drops_the_request_lines(
    workspace: Path, serving: threading.Event
) -> None:
    """§14.14 rule 5: `--quiet` may suppress progress.

    The two startup URLs are the whole usable output of the command, so a
    quiet run keeps them and silences the per-request lines instead.
    """

    port = free_port()
    previous = signal.getsignal(signal.SIGINT)

    def request_then_interrupt() -> None:
        handler_when_serving(serving, previous)
        with socket.create_connection(("127.0.0.1", port), 10.0) as client:
            client.sendall(
                b"GET /mirror?scope=global HTTP/1.1\r\n"
                b"Host: 127.0.0.1:" + str(port).encode("ascii") + b"\r\n\r\n"
            )
            while client.recv(65536):
                pass
        handler_when_serving(serving, previous)(signal.SIGINT, None)

    thread = threading.Thread(target=request_then_interrupt, daemon=True)
    thread.start()

    result = invoke(workspace, "--port", str(port), controls=("--quiet",))
    thread.join(30.0)

    assert result.exit_code == 9
    assert result.stderr.splitlines() == [
        f"http://127.0.0.1:{port}/mirror?scope=global",
        f"http://127.0.0.1:{port}/questions?scope=global",
    ]


def test_the_default_sigint_handler_is_restored(
    workspace: Path, serving: threading.Event
) -> None:
    """The command owns the handler only while it serves."""

    before = signal.getsignal(signal.SIGINT)
    port = free_port()
    interrupt_once_serving(serving)

    result = invoke(workspace, "--port", str(port))

    assert result.exit_code == 9
    assert signal.getsignal(signal.SIGINT) is before


def test_serving_off_the_main_thread_is_refused_before_any_bind(
    workspace: Path,
) -> None:
    """Only the main thread can install the handler that ends serving.

    Elsewhere the command would block on `accept` with no path to §14.14
    rule 6's class-9 envelope at all, so it refuses before a socket exists
    rather than serving something nothing can stop.
    """

    port = free_port()
    outcome: dict[str, object] = {}

    def run() -> None:
        result = invoke(workspace, "--port", str(port), controls=("--json",))
        outcome["exit_code"] = result.exit_code
        outcome["stdout"] = result.stdout

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(30.0)

    assert not thread.is_alive()
    assert outcome["exit_code"] == 1
    body = json.loads(outcome["stdout"].strip())
    assert body["diagnostic_class"] == "internal_error"
    assert body["result"] is None
    assert nothing_listens("127.0.0.1", port)


def test_a_late_progress_line_never_reaches_a_later_invocation(
    workspace: Path, serving: threading.Event
) -> None:
    """The reporter writes to the stream its own invocation owned.

    §14.17 forbids cancellation from waiting on a wedged reporter, so a line
    can still be written after the command returned. It must not follow
    `sys.stderr` to wherever the process points next.
    """

    port = free_port()
    reporters: list[object] = []
    real_reporter = cli._progress_reporter

    def capture(controls) -> object:
        made = real_reporter(controls)
        reporters.append(made)
        return made

    cli._progress_reporter = capture
    try:
        interrupt_once_serving(serving)
        first = invoke(workspace, "--port", str(port))
    finally:
        cli._progress_reporter = real_reporter

    assert first.exit_code == 9
    assert len(reporters) == 1

    # A line the retired reporter writes after its command returned reaches
    # the stream that command owned, which the runner has already closed —
    # never whatever `sys.stderr` names now.
    marker = "/mirror served-after-return"
    live = io.StringIO()
    previous, sys.stderr = sys.stderr, live
    try:
        with contextlib.suppress(ValueError):
            reporters[0]("served-after-return", "/mirror")
    finally:
        sys.stderr = previous

    assert marker not in live.getvalue()
