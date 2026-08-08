"""§30 local-view resolution behind the §14.17 `view serve` command.

This module is the whole state-dependent half of a served request: it parses
one closed selector, resolves it, applies §16.11's gate, revalidates the
§13.14 managed set, and composes exactly one §30 rule 7 outcome. It owns no
socket, no HTTP framing, and no CLI form — the transport hands it a route and
a query and receives a `ViewPage`.

Every read is read-only and uncached (§30 rule 6): the database reads happen
inside one §8.1 read transaction that closes before the managed-filesystem
revalidation begins, nothing is carried between requests, and no business
row, managed output, telemetry row, or provider call is ever written.

The pages composed here reuse §17's HTML emitter, so every document is inert
by construction — no script, no form, no URL, one hash-admitted inline
stylesheet — and every nonliteral value passes through §17's one total
escaping function on its way in.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import stat
import time
from typing import Callable, ContextManager, Iterator, Literal

from exp2res.errors import (
    AssessmentExportBlockedError,
    Exp2ResError,
    IntegrityFailureError,
    SchemaCompatibilityError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
    WorkspaceBusyError,
)
from exp2res.exports.companions import SelfClaimsDocument
from exp2res.exports.document import Block, Key, Lit, ReportDocument, Section, Val
from exp2res.exports.graph import (
    assessment_integrity_failure,
    load_assessment_graph,
    load_current_snapshot,
    load_snapshot_claims,
)
from exp2res.exports.html import render_html
from exp2res.exports.managed import ENTITY_ID, read_current_assessment_members
from exp2res.services.export import require_export_eligible
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    inspect_workspace,
    read_database,
)


__all__ = [
    "CONTENT_TYPE",
    "MIRROR_ROUTE",
    "PROCESSING_TIMEOUT_FACTOR",
    "QUESTIONS_ROUTE",
    "ROUTES",
    "ViewPage",
    "authority_not_bound_page",
    "internal_error_page",
    "malformed_request_page",
    "method_not_allowed_page",
    "processing_timeout_page",
    "resolve",
    "route_not_found_page",
    "schema_incompatible_page",
    "workspace_busy_page",
]


MIRROR_ROUTE = b"/mirror"
QUESTIONS_ROUTE = b"/questions"
ROUTES = (MIRROR_ROUTE, QUESTIONS_ROUTE)

# §14.17: the absolute processing budget is exactly three §8.1 contention
# timeouts, and the request's first SQLite read may not begin with fewer than
# two of them left. The transport owns the clock; these constants keep both
# bounds one shared definition rather than two.
PROCESSING_TIMEOUT_FACTOR = 3
_FIRST_READ_MARGIN_FACTOR = 2

CONTENT_TYPE = "text/html; charset=utf-8"

# §30 rule 7's closed outcome set. Each label is the service-owned literal the
# response carries in `Exp2Res-View-Outcome`; none is ever a request value.
Outcome = Literal[
    "served",
    "malformed_request",
    "invalid_selector",
    "assessment_blocked",
    "route_not_found",
    "no_current_view",
    "method_not_allowed",
    "assessment_inconsistent",
    "export_not_current",
    "question_companion_invalid",
    "export_residual",
    "schema_incompatible",
    "authority_not_bound",
    "internal_error",
    "workspace_busy",
    "export_changed",
    "processing_timeout",
]

_STATUS: dict[str, int] = {
    "served": 200,
    "malformed_request": 400,
    "invalid_selector": 400,
    "assessment_blocked": 403,
    "route_not_found": 404,
    "no_current_view": 404,
    "method_not_allowed": 405,
    "assessment_inconsistent": 409,
    "export_not_current": 409,
    "question_companion_invalid": 409,
    "export_residual": 409,
    "schema_incompatible": 409,
    "export_changed": 409,
    "authority_not_bound": 421,
    "internal_error": 500,
    "workspace_busy": 503,
    "processing_timeout": 503,
}

_TITLES: dict[str, str] = {
    "malformed_request": "Request Not Accepted",
    "invalid_selector": "Selector Not Accepted",
    "assessment_blocked": "Assessment Not Verified for Export",
    "route_not_found": "No Such View",
    "no_current_view": "No Current Assessment View",
    "method_not_allowed": "Method Not Supported",
    "assessment_inconsistent": "Stored Assessment State Is Inconsistent",
    "export_not_current": "Published Assessment Is Not Current",
    "question_companion_invalid": "Published Question Set Is Not Readable",
    "export_residual": "Published Assessment Needs Manual Repair",
    "schema_incompatible": "Workspace Schema Is Not Supported",
    "export_changed": "Published Assessment Changed While Reading",
    "authority_not_bound": "Request Authority Not Served",
    "internal_error": "View Unavailable",
    "workspace_busy": "Workspace Is Busy",
    "processing_timeout": "Request Took Too Long",
}

_ASSESS_LIST = "exp2res assess list"
_ASSESS_GENERATE = "exp2res assess generate"

_SELECTOR_SHAPE = (
    "Exactly one selector is accepted: ?scope=global, or ?snapshot= with one "
    "exact snapshot ID. An absent, unknown, repeated, combined, or malformed "
    "parameter is refused rather than interpreted."
)
_SELECTOR_ABSENT = (
    "This view serves one explicitly selected assessment view. Add "
    "?scope=global for the current global mirror, or ?snapshot= with one "
    "exact snapshot ID."
)
_SNAPSHOT_GRAMMAR = (
    "The snapshot selector must be one exact stored snapshot ID: 1 to 128 "
    "lowercase ASCII letters, digits, underscores, or hyphens."
)
_SCHEMA_INCOMPATIBLE = (
    "This workspace is not at a schema version this build can read, so "
    "nothing is served."
)
_WORKSPACE_BUSY = (
    "Another SQLite operation held this workspace longer than the bounded "
    "wait allows. Nothing partial is served; reload once it finishes."
)
_PROCESSING_TIMEOUT = (
    "This request did not finish inside its fixed processing budget, so its "
    "unfinished work was released. Reload; if this persists, stop serving and "
    "inspect local workspace or filesystem health."
)
_EXPORT_NOT_CURRENT = (
    "The published assessment set for this view is missing, stale, or no "
    "longer matches current state. Nothing partial or re-rendered is served "
    "in its place."
)
_EXPORT_RESIDUAL = (
    "The published assessment set is in a state no re-export can replace on "
    "its own. Remove or repair the residual the export command reports "
    "before publishing again."
)


@dataclass(frozen=True)
class ViewPage:
    """One complete §30 rule 7 outcome: its class, status, and exact bytes.

    `published_member` marks the one body the transport may not touch: the
    mirror serves exactly the revalidated `report.html` bytes, so nothing —
    not even outcome metadata — may be added to or rewritten inside it.
    """

    outcome: Outcome
    status: int
    body: bytes
    published_member: bool = False
    content_type: str = CONTENT_TYPE


ConnectionRegistrar = Callable[[sqlite3.Connection], ContextManager[object]]


@contextmanager
def _unregistered(_connection: sqlite3.Connection) -> Iterator[None]:
    """Register nothing: the default when no transport owns cancellation."""

    yield


@dataclass(frozen=True)
class _Selector:
    """Exactly one of §30 rule 3's two closed selector forms."""

    snapshot_id: str | None = None
    scope: str | None = None

    @property
    def by_identity(self) -> bool:
        return self.scope is not None


class _Refusal(Exception):
    """One decided outcome, carrying only renderer-owned text and a remedy."""

    def __init__(
        self, outcome: Outcome, message: str, command: str | None = None
    ) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.message = message
        self.command = command


def notice_page(
    outcome: Outcome, message: str, command: str | None = None
) -> ViewPage:
    """Render one owner-visible outcome page under §30 rules 6–7.

    The page states its outcome class and, where one exists, the §14 command
    that resolves it, and nothing else about local state. `command` is
    composed from renderer-owned literals plus at most one stored entity ID
    that resolution already proved current — never from request bytes.
    """

    blocks: tuple[Block, ...] = ()
    if command is not None:
        blocks = (
            Block(
                lead=(
                    Lit("Run this command, then reload: "),
                    Val(command, style="token"),
                )
            ),
        )
    document = ReportDocument(
        title=_TITLES[outcome],
        header=((Val(message),), (Key("Outcome"), Val(outcome, style="token"))),
        sections=(
            (Section(heading="What Resolves This", blocks=blocks),) if blocks else ()
        ),
    )
    return ViewPage(
        outcome=outcome, status=_STATUS[outcome], body=render_html(document)
    )


def _refusal_page(refusal: _Refusal) -> ViewPage:
    return notice_page(refusal.outcome, refusal.message, refusal.command)


def route_not_found_page() -> ViewPage:
    return notice_page(
        "route_not_found",
        "This server serves the mirror at /mirror and the open questions at "
        "/questions, each with one explicit selector.",
    )


def method_not_allowed_page() -> ViewPage:
    return notice_page(
        "method_not_allowed",
        "The local views are read-only and answer GET and HEAD requests only.",
    )


def malformed_request_page() -> ViewPage:
    return notice_page(
        "malformed_request",
        "These views answer a bounded HTTP/1.1 request that carries no body. "
        "A request Exp2Res cannot read as one is refused rather than "
        "interpreted.",
    )


def authority_not_bound_page() -> ViewPage:
    return notice_page(
        "authority_not_bound",
        "Open this view through the loopback address it is bound to. Another "
        "authority or declared origin is refused before any state is read.",
    )


def internal_error_page() -> ViewPage:
    return notice_page("internal_error", "The selected view cannot be served.")


def workspace_busy_page() -> ViewPage:
    return notice_page("workspace_busy", _WORKSPACE_BUSY)


def processing_timeout_page() -> ViewPage:
    return notice_page("processing_timeout", _PROCESSING_TIMEOUT)


def schema_incompatible_page(workspace: Path) -> ViewPage:
    return notice_page(
        "schema_incompatible", _SCHEMA_INCOMPATIBLE, _schema_remedy(workspace)
    )


def _schema_remedy(workspace: Path) -> str:
    """Name a migration only where §12.14 offers one to run.

    A stored version newer than this build needs a newer build, and an
    unrecognized workspace has no migration path at all; both are answered by
    the read-only status command rather than by guidance that would fail.
    """

    try:
        status = inspect_workspace(workspace, require_managed_root=False)
    except Exp2ResError:
        return "exp2res db status"
    if status.recognized and status.migration_path_available:
        return "exp2res db migrate"
    return "exp2res db status"


def _export_command(snapshot_id: str) -> str:
    return f"exp2res export assessment --snapshot {snapshot_id}"


def _verify_command(snapshot_id: str) -> str:
    return f"exp2res assess verify --snapshot {snapshot_id}"


def _percent_decode(raw: bytes) -> str | None:
    """Decode one selector value exactly once, as §30 rule 6 requires.

    Only a value is decoded, and only here: no second pass ever runs, so a
    double-encoded value yields a literal percent sequence that resolves to
    nothing instead of becoming some other selector. `+` is not a space: this
    is a URI escape, never a form encoding.
    """

    decoded = bytearray()
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte != 0x25:  # '%'
            decoded.append(byte)
            index += 1
            continue
        escape = raw[index + 1 : index + 3]
        if len(escape) != 2:
            return None
        try:
            decoded.append(int(escape.decode("ascii"), 16))
        except (UnicodeDecodeError, ValueError):
            return None
        index += 3
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _split_pairs(query: bytes) -> list[tuple[bytes, bytes]] | None:
    """Split the query on its literal `&` and `=` bytes, before any decoding.

    Structure is matched as the bytes received (§30 rule 6): a percent-escape
    standing in a structural position is never decoded into a delimiter or a
    parameter name, and a field carrying no `=`, or a second one, does not
    match the one accepted shape.
    """

    pairs: list[tuple[bytes, bytes]] = []
    for field in query.split(b"&"):
        name, separator, value = field.partition(b"=")
        if not separator or b"=" in value:
            return None
        pairs.append((name, value))
    return pairs


def _parse_selector(query: bytes | None) -> _Selector:
    if not query:
        raise _Refusal("invalid_selector", _SELECTOR_ABSENT)
    pairs = _split_pairs(query)
    if pairs is None:
        raise _Refusal("invalid_selector", _SELECTOR_SHAPE)
    if len(pairs) != 1 or pairs[0][0] not in (b"scope", b"snapshot"):
        raise _Refusal("invalid_selector", _SELECTOR_SHAPE)
    name, raw = pairs[0]
    value = _percent_decode(raw)
    if value is None:
        raise _Refusal("invalid_selector", _SELECTOR_SHAPE)
    if name == b"scope":
        # §30 rule 3: `global` is the only `AssessmentScope` value, so it is
        # the only identity this form names.
        if value != "global":
            raise _Refusal("invalid_selector", _SELECTOR_SHAPE)
        return _Selector(scope="global")
    # §13.14 rule 1's stored-ID grammar decides before any lookup.
    if ENTITY_ID.fullmatch(value) is None:
        raise _Refusal("invalid_selector", _SNAPSHOT_GRAMMAR)
    return _Selector(snapshot_id=value)


def _resolve_snapshot(connection: sqlite3.Connection, selector: _Selector):
    """Resolve one explicit selector to exactly one current snapshot row."""

    if selector.snapshot_id is not None:
        try:
            snapshot_row, snapshot = load_current_snapshot(
                connection, selector.snapshot_id
            )
        except (SelectorNotFoundError, SnapshotNotCurrentError) as error:
            raise _Refusal(
                "no_current_view",
                "No current assessment snapshot has that ID. A superseded "
                "snapshot is history, never a served view.",
                _ASSESS_LIST,
            ) from error
        return snapshot_row, snapshot

    # §30 rule 3: the identity form resolves only to the unique current
    # snapshot of exactly that view — never the newest of several. §11.7
    # admits at most one current snapshot, so both zero and more than one are
    # fail-closed outcomes rather than a choice.
    current = connection.execute(
        "SELECT id FROM assessment_snapshots WHERE superseded_at IS NULL"
    ).fetchall()
    if not current:
        raise _Refusal(
            "no_current_view",
            "No current global assessment view exists yet. Generate one, "
            "verify it, and export it.",
            _ASSESS_GENERATE,
        )
    if len(current) > 1:
        # §13.6 admits one current snapshot per view identity and is not a
        # corruption-repair surface, so this outcome names no command: the
        # owner recovers the invariant outside the §14 command surface.
        raise _Refusal(
            "assessment_inconsistent",
            "More than one current snapshot claims the global assessment "
            "view, so no single view can be served. Stop serving and recover "
            "the workspace invariant before reading this view again.",
        )
    return load_current_snapshot(connection, current[0]["id"])


def _require_integrity(selector: _Selector, snapshot, claims) -> None:
    """Run §16.11's integrity half, which precedes its status half."""

    failure = assessment_integrity_failure(snapshot, claims)
    if failure is None:
        return
    if failure == "aggregate_mismatch":
        # Verification recomputes the aggregate on exactly this snapshot, so
        # it is the remedy under either selector form.
        raise _Refusal(
            "assessment_inconsistent",
            "This snapshot's stored verification aggregate is no longer the "
            "reduction of its own current claims, so its status is not a "
            "verdict serving can be read against.",
            _verify_command(snapshot.id),
        )
    # Claim membership belongs to generation, and generation creates a new ID:
    # it repairs the identity URL but never this exact-ID one.
    raise _Refusal(
        "assessment_inconsistent",
        "This snapshot does not carry exactly one narrative summary claim "
        "matching its stored summary, so its claim set is broken stored state "
        "rather than a mirror.",
        _ASSESS_GENERATE if selector.by_identity else _ASSESS_LIST,
    )


@contextmanager
def _stored_state(selector: _Selector) -> Iterator[None]:
    """Report a broken stored assessment graph as §30's own 409, not a 500.

    Every check inside the read transaction that raises `IntegrityFailureError`
    — an empty or superseded claim set, a claim from another generation, a row
    that no longer hydrates, a source or answer-log reference the graph
    requires — reports stored state that breaks an invariant serving depends
    on and that no corrected request can repair. That is rule 7's
    `assessment_inconsistent`, and only an unexpected failure is
    `internal_error`. Claim membership belongs to generation, so the remedy is
    the one rule 7 gives the other claim-set invariant.
    """

    try:
        yield
    except IntegrityFailureError as error:
        raise _Refusal(
            "assessment_inconsistent",
            "This snapshot's stored claim graph breaks an invariant serving "
            "depends on, so there is no coherent assessment to read a view "
            "against.",
            _ASSESS_GENERATE if selector.by_identity else _ASSESS_LIST,
        ) from error


def _require_export_gate(selector: _Selector, snapshot) -> None:
    """Apply §16.11's assessment-export allowlist as a completed refusal."""

    try:
        require_export_eligible(snapshot.verification_status)
    except AssessmentExportBlockedError as error:
        if snapshot.verification_status == "unverified":
            # Verification updates exactly the selected snapshot, so it is the
            # remedy under either selector form.
            command = _verify_command(snapshot.id)
        else:
            # A completed negative verdict needs replacement claims, which
            # only generation produces — and generation creates a new ID, so
            # it repairs an identity URL but never an exact-ID one.
            command = _ASSESS_GENERATE if selector.by_identity else _ASSESS_LIST
        raise _Refusal(
            "assessment_blocked",
            "This assessment does not pass the verification gate that admits "
            "it to export, so no part of it is served.",
            command,
        ) from error


def _questions_document(
    members: dict[str, bytes], *, export_command: str
) -> ReportDocument:
    """Project §30 rule 5's open-question set and nothing else.

    The companion is revalidated as the closed §13.12 document before it is
    read, and only the `question` values of unanswered `unknowns` reach the
    page: no gap ID, target, reason, priority, claim, contradiction, or
    snapshot field is emitted, so a question stays readable without becoming
    an answer link-back token.
    """

    try:
        document = SelfClaimsDocument.model_validate_json(members["self_claims.json"])
    except ValueError as error:
        # An expected projection-integrity failure over a matching-digest
        # member, never an unexpected local failure (§30 rule 7).
        raise _Refusal(
            "question_companion_invalid",
            "The published question set matches its recorded digest but is "
            "not a valid companion document, so no part of it is projected.",
            export_command,
        ) from error
    questions = tuple(
        Block(lead=(Val(unknown.question),))
        for unknown in document.unknowns
        if not unknown.answered
    )
    return ReportDocument(
        title="Open Questions",
        header=(
            (
                Val(
                    "The questions this assessment view still has no answer "
                    "for. An answer returns only as an ordinary log."
                ),
            ),
        ),
        sections=(Section(heading="Open Questions", blocks=questions),),
    )


def _entry_is_present_non_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)


def _compatibility_refusal(workspace: Path) -> _Refusal:
    """Tell an unreadable schema apart from an unusable managed root.

    The §8.1 read gate refuses both, but §30 answers them differently: a
    symlinked or non-directory `out/` entry under a schema this build reads is
    §13.14 rule 6's containment failure, which no migration resolves. An
    absent root never arrives here — the §30 read does not require one, so
    that state is classified in phase 2 with the rest of the managed output.
    """

    try:
        status = inspect_workspace(workspace, require_managed_root=False)
    except WorkspaceBusyError:
        return _Refusal("workspace_busy", _WORKSPACE_BUSY)
    if (
        status.recognized
        and status.compatible
        and _entry_is_present_non_directory(workspace / "out")
    ):
        return _Refusal("export_residual", _EXPORT_RESIDUAL)
    return _Refusal(
        "schema_incompatible", _SCHEMA_INCOMPATIBLE, _schema_remedy(workspace)
    )


def _is_contention(error: sqlite3.Error) -> bool:
    text = str(error).lower()
    return "locked" in text or "busy" in text


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _composed(page: ViewPage, deadline: float) -> ViewPage:
    """Return one composed page, or the timeout that outlived composing it.

    §14.17's ordinary deadline is an outer boundary over determining *and*
    composing a row: a row that is not fully composed when the budget expires
    is not this request's outcome, whatever it would have said. Only the fixed
    timeout page itself is exempt, because it is what that expiry composes.
    """

    if page.outcome == "processing_timeout" or _remaining(deadline) > 0:
        return page
    return processing_timeout_page()


def resolve(
    workspace: Path,
    route: bytes,
    query: bytes | None,
    *,
    deadline: float,
    register_connection: ConnectionRegistrar = _unregistered,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ViewPage:
    """Answer one §30 request: resolve, revalidate, and compose one outcome.

    `route` and `query` are the exact received bytes of the already-split
    origin-form target, with `query` `None` when the target carried no `?`.
    Structure is matched on those bytes and only a selector value is decoded.

    `deadline` is §14.17's absolute processing deadline as a `time.monotonic`
    value. It is checked before the first SQLite read — which needs two full
    contention timeouts of budget left, so a contended read still completes
    its own bounded wait and composes `workspace_busy` — and again at each
    later composition point.

    `register_connection` publishes the open SQLite connection to the caller
    for the length of the read transaction, so a transport can interrupt it on
    cancellation. It defaults to registering nothing.
    """

    if route not in ROUTES:
        return _composed(route_not_found_page(), deadline)
    try:
        selector = _parse_selector(query)

        if _remaining(deadline) < _FIRST_READ_MARGIN_FACTOR * busy_timeout_ms / 1000:
            # §14.17: without room for one complete contention wait plus fixed
            # composition, the read transaction never opens.
            raise _Refusal("processing_timeout", _PROCESSING_TIMEOUT)

        # Phase 1 — every business read inside one §8.1 read transaction,
        # which re-reads §12.14 compatibility and closes before phase 2.
        try:
            with read_database(
                workspace, timeout_ms=busy_timeout_ms, require_managed_root=False
            ) as connection, register_connection(connection), _stored_state(selector):
                snapshot_row, snapshot = _resolve_snapshot(connection, selector)
                _record, claims = load_snapshot_claims(
                    connection, snapshot_row=snapshot_row, snapshot=snapshot
                )
                _require_integrity(selector, snapshot, claims)
                _require_export_gate(selector, snapshot)
                graph = load_assessment_graph(
                    connection, snapshot_row=snapshot_row, snapshot=snapshot
                )
        except SchemaCompatibilityError as error:
            raise _compatibility_refusal(workspace) from error
        except WorkspaceBusyError as error:
            raise _Refusal("workspace_busy", _WORKSPACE_BUSY) from error
        except sqlite3.OperationalError as error:
            if not _is_contention(error):
                raise
            raise _Refusal("workspace_busy", _WORKSPACE_BUSY) from error

        if _remaining(deadline) <= 0:
            raise _Refusal("processing_timeout", _PROCESSING_TIMEOUT)

        # Phase 2 — §13.14 revalidation over the managed filesystem, with no
        # transaction open and no database value re-read.
        read = read_current_assessment_members(workspace, graph)
        if read.status == "not_current":
            raise _Refusal(
                "export_not_current", _EXPORT_NOT_CURRENT, _export_command(snapshot.id)
            )
        if read.status == "residual":
            raise _Refusal("export_residual", _EXPORT_RESIDUAL)
        if read.status == "changed":
            raise _Refusal(
                "export_changed",
                "The published assessment set changed while this request was "
                "reading it. Repeat the request once the concurrent export "
                "has finished.",
            )
        members = read.members or {}

        if _remaining(deadline) <= 0:
            raise _Refusal("processing_timeout", _PROCESSING_TIMEOUT)

        if route == MIRROR_ROUTE:
            # §30 rule 3: exactly the revalidated member bytes, never a second
            # rendering of the same projection.
            page = ViewPage(
                outcome="served",
                status=_STATUS["served"],
                body=members["report.html"],
                published_member=True,
            )
        else:
            document = _questions_document(
                members, export_command=_export_command(snapshot.id)
            )
            page = ViewPage(
                outcome="served", status=_STATUS["served"], body=render_html(document)
            )
    except _Refusal as refusal:
        return _composed(_refusal_page(refusal), deadline)
    except (Exp2ResError, sqlite3.Error, OSError, ValueError):
        # Fail closed and say nothing more: an unexpected local failure names
        # no path, row, or exception detail (§30 rule 7).
        return _composed(internal_error_page(), deadline)
    return _composed(page, deadline)
