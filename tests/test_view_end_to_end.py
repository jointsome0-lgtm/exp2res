"""§21.57 end to end: a real published workspace served over a real socket.

Everything below runs the whole offline Vera Example pipeline, publishes the
managed set, and then reads the pages back through actual loopback HTTP —
the state-dependent half of `tests/test_view_resolution.py` and the
transport half of `tests/test_view_transport.py` joined into the one flow
§21.57 describes.

The bind uses `ViewServer`'s internal constructor on an ephemeral port,
because `--port 0` is refused on the public surface: the command form itself
is covered by `tests/test_cli_view_serve.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import socket
import sqlite3
import threading
from typing import Iterator

import pytest

from exp2res.services.capture import capture_gap_answer
from exp2res.storage.workspace import CURRENT_SCHEMA_VERSION
from exp2res.services.export import export_assessment
from exp2res.services.view_server import (
    BindAddress,
    Timeouts,
    ViewServer,
    bound_urls,
)

from test_view_resolution import exported_workspace, member, rewrite_companion


pytestmark = [pytest.mark.lifecycle]

# Generous absolute budgets: these tests assert served content, never expiry,
# so no assertion here may turn into a timing race on a loaded machine.
GENEROUS = Timeouts(receive=60.0, processing=60.0, emit=60.0, drain=60.0)


def free_bind(host: str = "127.0.0.1") -> BindAddress:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return BindAddress(host=host, port=probe.getsockname()[1])


class Response:
    """One parsed HTTP response, split exactly at the header terminator."""

    def __init__(self, raw: bytes) -> None:
        head, _, self.body = raw.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        self.status = int(lines[0].split(b" ")[1])
        self.headers = {
            name.decode("ascii").lower(): value.strip().decode("ascii")
            for name, _, value in (line.partition(b":") for line in lines[1:])
        }

    @property
    def outcome(self) -> str:
        return self.headers["exp2res-view-outcome"]


class LiveView:
    """A running server plus the client half of one request at a time."""

    def __init__(self, server: ViewServer, bind: BindAddress) -> None:
        self.server = server
        self.bind = bind
        self.thread = threading.Thread(target=server.serve, daemon=True)

    def get(
        self,
        route: str,
        selector: str,
        *,
        method: str = "GET",
        extra: tuple[str, ...] = (),
    ) -> Response:
        lines = [
            f"{method} {route}?{selector} HTTP/1.1",
            f"Host: {self.bind.authority}",
            *extra,
            "",
            "",
        ]
        request = "\r\n".join(lines).encode("ascii")
        with socket.create_connection(
            (self.bind.host, self.bind.port), 60.0
        ) as client:
            client.sendall(request)
            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return Response(b"".join(chunks))

    def mirror(self, selector: str = "scope=global", **kwargs) -> Response:
        return self.get("/mirror", selector, **kwargs)

    def questions(self, selector: str = "scope=global", **kwargs) -> Response:
        return self.get("/questions", selector, **kwargs)


@contextmanager
def live_view(workspace: Path, host: str = "127.0.0.1") -> Iterator[LiveView]:
    """One real server over the real resolver — no stub anywhere in the path."""

    bind = free_bind(host)
    server = ViewServer(workspace, bind, _timeouts=GENEROUS)
    server.open()
    view = LiveView(server, bind)
    view.thread.start()
    try:
        yield view
    finally:
        server.interrupt()
        view.thread.join(60.0)


def unknowns(workspace: Path, snapshot_id: str) -> list[dict]:
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    return companion["unknowns"]


def answer_one_gap(workspace: Path, snapshot_id: str) -> None:
    """The ordinary owner answer that invalidates the published set."""

    open_gap = next(
        item for item in unknowns(workspace, snapshot_id) if not item["answered"]
    )
    capture_gap_answer(
        workspace,
        gap_id=open_gap["id"],
        raw_text="Vera Example validated it against one production tenant.",
        artifacts=(),
    )


def test_both_routes_serve_both_selector_forms_over_a_real_socket(
    workspace: Path,
) -> None:
    """§21.57: the published mirror bytes and the question projection, live."""

    snapshot_id = exported_workspace(workspace)
    published = member(workspace, snapshot_id, "report.html")
    open_questions = [
        item["question"] for item in unknowns(workspace, snapshot_id)
        if not item["answered"]
    ]
    assert open_questions, "the fixture must publish at least one open question"

    with live_view(workspace) as view:
        by_identity = view.mirror()
        by_id = view.mirror(f"snapshot={snapshot_id}")
        repeated = view.mirror()
        questions_by_identity = view.questions()
        questions_by_id = view.questions(f"snapshot={snapshot_id}")

    for response in (by_identity, by_id, repeated):
        assert response.status == 200
        assert response.outcome == "served"
        # Byte-identical to the revalidated member: nothing is re-rendered,
        # and the transport adds nothing to the body.
        assert response.body == published
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["connection"] == "close"
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["content-length"] == str(len(published))

    for response in (questions_by_identity, questions_by_id):
        assert response.status == 200
        assert response.outcome == "served"
    assert questions_by_id.body == questions_by_identity.body
    page = questions_by_identity.body.decode("utf-8")
    for question in open_questions:
        assert question in page
    for item in unknowns(workspace, snapshot_id):
        assert item["id"] not in page
        assert item["target_id"] not in page
    assert snapshot_id not in page
    assert str(workspace) not in page


def test_head_returns_the_get_status_and_headers_with_no_body(
    workspace: Path,
) -> None:
    """§30 rule 2: `HEAD` runs the same resolution and revalidation."""

    snapshot_id = exported_workspace(workspace)

    with live_view(workspace) as view:
        for route in ("/mirror", "/questions"):
            get = view.get(route, "scope=global")
            head = view.get(route, "scope=global", method="HEAD")

            assert head.status == get.status
            assert head.outcome == get.outcome
            assert head.headers["content-length"] == get.headers["content-length"]
            assert head.body == b""
    assert snapshot_id


def test_one_running_server_observes_invalidation_and_the_replacement(
    workspace: Path,
) -> None:
    """§21.57: nothing is cached between requests on a live server.

    The same server, never restarted, serves the current set, refuses both
    routes once an ordinary answer invalidates it, and then serves the
    replacement bytes — the request bytes never change, only the state.
    """

    snapshot_id = exported_workspace(workspace)
    first_report = member(workspace, snapshot_id, "report.html")

    with live_view(workspace) as view:
        assert view.mirror().body == first_report
        first_questions = view.questions().body

        answer_one_gap(workspace, snapshot_id)

        stale_mirror = view.mirror()
        stale_questions = view.questions()
        assert stale_mirror.status == 409
        assert stale_questions.status == 409
        assert stale_mirror.outcome == "export_not_current"
        assert stale_questions.outcome == "export_not_current"
        assert stale_mirror.body != first_report
        assert stale_questions.body != first_questions

        export_assessment(workspace, snapshot_id=snapshot_id)
        replacement = member(workspace, snapshot_id, "report.html")

        served_mirror = view.mirror()
        served_questions = view.questions()

    assert served_mirror.status == 200
    assert served_mirror.body == replacement
    assert served_questions.status == 200
    # The answered question is gone from the replacement projection.
    assert served_questions.body != first_questions


def migrate_beyond_this_build(workspace: Path) -> None:
    """Let another build carry the workspace one schema version past ours.

    Written straight to the database, the way a newer build's migration
    would land it while this server keeps holding its socket.
    """

    connection = sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite")
    try:
        connection.execute(
            "INSERT INTO schema_meta(version, applied_at, app_version)"
            " VALUES (?, ?, ?)",
            (
                CURRENT_SCHEMA_VERSION + 1,
                "2026-08-06T00:00:00+00:00",
                "99.0.0",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_a_forward_migration_under_a_live_server_refuses_the_next_request(
    workspace: Path,
) -> None:
    """§21.57: the §12.14 gate is a per-request check, not a bind-time one.

    The server was bound against a compatible workspace and served the
    published bytes. Another build then migrates past this one, and the very
    next request on that same server refuses instead of replaying what it
    already read.
    """

    snapshot_id = exported_workspace(workspace)
    published = member(workspace, snapshot_id, "report.html")

    with live_view(workspace) as view:
        assert view.mirror().body == published

        migrate_beyond_this_build(workspace)

        refused_mirror = view.mirror()
        refused_questions = view.questions()

    assert refused_mirror.status == 409
    assert refused_mirror.outcome == "schema_incompatible"
    assert refused_mirror.body != published
    assert refused_questions.status == 409
    assert refused_questions.outcome == "schema_incompatible"


def test_the_ipv6_loopback_serves_the_same_routes(workspace: Path) -> None:
    """§14.17: `--host ::1` is a served bind, not merely an advertised one."""

    snapshot_id = exported_workspace(workspace)
    published = member(workspace, snapshot_id, "report.html")

    with live_view(workspace, host="::1") as view:
        assert bound_urls(view.bind) == (
            f"http://[::1]:{view.bind.port}/mirror?scope=global",
            f"http://[::1]:{view.bind.port}/questions?scope=global",
        )
        served = view.mirror()
        questions = view.questions()

    assert served.status == 200
    assert served.body == published
    assert questions.status == 200
    assert questions.outcome == "served"


def test_an_empty_question_projection_is_still_a_served_document(
    workspace: Path,
) -> None:
    """§21.57: no unanswered unknown is a valid 200, not a refusal."""

    snapshot_id = exported_workspace(workspace)
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    emptied = dict(companion, unknowns=[])
    rewrite_companion(
        workspace,
        snapshot_id,
        json.dumps(emptied, separators=(",", ":")).encode("utf-8"),
    )

    with live_view(workspace) as view:
        response = view.questions()

    assert response.status == 200
    assert response.outcome == "served"
    assert response.body.startswith(b"<!DOCTYPE html>") or response.body.startswith(
        b"<!doctype html>"
    )
    for item in companion["unknowns"]:
        assert item["question"].encode("utf-8") not in response.body


def test_a_page_spanning_many_socket_writes_arrives_whole(workspace: Path) -> None:
    """A response past one socket write still arrives complete and framed.

    Each question is stretched to just under §11's 16 KiB text bound, so the
    page is real projected content rather than a value the closed §13.12
    schema would reject on its own.
    """

    snapshot_id = exported_workspace(workspace)
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    markers = [
        f"Vera Example scale question {index} " + "x" * 15_000
        for index, _ in enumerate(companion["unknowns"])
    ]
    assert len(markers) > 1, "the fixture must publish more than one question"
    enlarged = dict(
        companion,
        unknowns=[
            dict(item, question=marker, answered=False)
            for item, marker in zip(companion["unknowns"], markers)
        ],
    )
    rewrite_companion(
        workspace,
        snapshot_id,
        json.dumps(enlarged, separators=(",", ":")).encode("utf-8"),
    )

    with live_view(workspace) as view:
        response = view.questions()

    assert response.status == 200
    assert response.outcome == "served"
    page = response.body.decode("utf-8")
    for marker in markers:
        assert marker in page
    # Declared length and delivered length agree, so nothing was truncated.
    assert len(response.body) == int(response.headers["content-length"])
    assert len(response.body) > 30_000


def test_a_shell_embed_is_served_under_either_fetch_metadata_declaration(
    workspace: Path,
) -> None:
    """§21.57: the site declaration is not consulted and refuses nothing.

    The consumer this surface exists for embeds the view by URL from its own
    web origin, so the browser sends exactly these headers on the navigation
    §30 is built to serve. Refusing either declaration would refuse the
    intended embed, so both get the published bytes.
    """

    snapshot_id = exported_workspace(workspace)
    published = member(workspace, snapshot_id, "report.html")

    with live_view(workspace) as view:
        cross_site = view.mirror(
            extra=(
                "Sec-Fetch-Site: cross-site",
                "Sec-Fetch-Mode: navigate",
                "Sec-Fetch-Dest: iframe",
            )
        )
        same_site = view.mirror(
            extra=(
                "Sec-Fetch-Site: same-site",
                "Sec-Fetch-Mode: navigate",
                "Sec-Fetch-Dest: document",
            )
        )

    for response in (cross_site, same_site):
        assert response.status == 200
        assert response.outcome == "served"
        assert response.body == published


def test_an_ambient_cookie_is_ignored_and_no_cross_origin_read_is_granted(
    workspace: Path,
) -> None:
    """§21.57: the browser's ambient state reaches nothing, in either direction.

    A cookie a browser attached for the loopback host is one of §30 rule 6's
    ignored headers, so it changes neither the outcome nor the bytes. A page
    elsewhere declaring its own origin is refused by rule 1 before any state
    is read, and the closed response header set stores nothing back in the
    browser and grants no cross-origin read either way.
    """

    snapshot_id = exported_workspace(workspace)
    published = member(workspace, snapshot_id, "report.html")

    with live_view(workspace) as view:
        with_cookie = view.mirror(
            extra=("Cookie: session=vera-example-ambient-value",)
        )
        cross_origin = view.mirror(extra=("Origin: http://elsewhere.example",))

    assert with_cookie.status == 200
    assert with_cookie.outcome == "served"
    assert with_cookie.body == published
    assert "vera-example-ambient-value" not in with_cookie.body.decode("utf-8")

    assert cross_origin.status == 421
    assert cross_origin.outcome == "authority_not_bound"
    assert cross_origin.body != published

    for response in (with_cookie, cross_origin):
        assert set(response.headers) == {
            "exp2res-view-outcome",
            "cache-control",
            "content-type",
            "content-length",
            "connection",
        }
