"""§30 local-view resolution behind §14.17 `view serve`: route + query in,
one read-only, uncached §30 rule 7 `ViewPage` out (no socket, framing, or CLI)."""

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
from exp2res.services.writers import is_busy
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    inspect_workspace,
    read_database,
)


MIRROR_ROUTE = b"/mirror"
QUESTIONS_ROUTE = b"/questions"
ROUTES = (MIRROR_ROUTE, QUESTIONS_ROUTE)

# §14.17: the budget is three §8.1 contention timeouts; the first SQLite
# read needs two of them left. The transport owns the clock.
PROCESSING_TIMEOUT_FACTOR = 3
_FIRST_READ_MARGIN_FACTOR = 2

CONTENT_TYPE = "text/html; charset=utf-8"

# §30 rule 7's closed outcome set, carried in `Exp2Res-View-Outcome`.
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

# §30 rule 7: one row per outcome — HTTP status, page title, and the fixed
# notice text for the outcomes that need no remedy command.
_OUTCOMES: dict[str, tuple[int, str, str | None]] = {
    "served": (200, "", None),
    "malformed_request": (
        400,
        "Request Not Accepted",
        "These views answer a bounded HTTP/1.1 request that carries no body. "
        "A request Exp2Res cannot read as one is refused rather than "
        "interpreted.",
    ),
    "invalid_selector": (400, "Selector Not Accepted", None),
    "assessment_blocked": (403, "Assessment Not Verified for Export", None),
    "route_not_found": (
        404,
        "No Such View",
        "This server serves the mirror at /mirror and the open questions at "
        "/questions, each with one explicit selector.",
    ),
    "no_current_view": (404, "No Current Assessment View", None),
    "method_not_allowed": (
        405,
        "Method Not Supported",
        "The local views are read-only and answer GET and HEAD requests only.",
    ),
    "assessment_inconsistent": (409, "Stored Assessment State Is Inconsistent", None),
    "export_not_current": (409, "Published Assessment Is Not Current", None),
    "question_companion_invalid": (409, "Published Question Set Is Not Readable", None),
    "export_residual": (409, "Published Assessment Needs Manual Repair", None),
    "schema_incompatible": (409, "Workspace Schema Is Not Supported", None),
    "export_changed": (409, "Published Assessment Changed While Reading", None),
    "authority_not_bound": (
        421,
        "Request Authority Not Served",
        "Open this view through the loopback address it is bound to. Another "
        "authority or declared origin is refused before any state is read.",
    ),
    "internal_error": (500, "View Unavailable", "The selected view cannot be served."),
    "workspace_busy": (
        503,
        "Workspace Is Busy",
        "Another SQLite operation held this workspace longer than the bounded "
        "wait allows. Nothing partial is served; reload once it finishes.",
    ),
    "processing_timeout": (
        503,
        "Request Took Too Long",
        "This request did not finish inside its fixed processing budget, so its "
        "unfinished work was released. Reload; if this persists, stop serving and "
        "inspect local workspace or filesystem health.",
    ),
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
    """One §30 rule 7 outcome; `published_member` bodies are served byte-exact."""

    outcome: Outcome
    status: int
    body: bytes
    published_member: bool = False
    content_type: str = CONTENT_TYPE


ConnectionRegistrar = Callable[[sqlite3.Connection], ContextManager[object]]


@contextmanager
def _unregistered(_connection: sqlite3.Connection) -> Iterator[None]:
    yield


@dataclass(frozen=True)
class _Selector:
    """One of §30 rule 3's two selector forms."""

    snapshot_id: str | None = None
    scope: str | None = None

    @property
    def by_identity(self) -> bool:
        return self.scope is not None


class _Refusal(Exception):
    """One decided outcome: renderer-owned text (the table's fixed notice by
    default) plus an optional remedy."""

    def __init__(
        self, outcome: Outcome, message: str | None = None, command: str | None = None
    ) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.message = _OUTCOMES[outcome][2] if message is None else message
        self.command = command


def notice_page(
    outcome: Outcome, message: str, command: str | None = None
) -> ViewPage:
    """§30 rules 6–7 outcome page; `command` never derives from request bytes."""

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
    status, title, _notice = _OUTCOMES[outcome]
    document = ReportDocument(
        title=title,
        header=((Val(message),), (Key("Outcome"), Val(outcome, style="token"))),
        sections=(
            (Section(heading="What Resolves This", blocks=blocks),) if blocks else ()
        ),
    )
    return ViewPage(outcome=outcome, status=status, body=render_html(document))


def _refusal_page(refusal: _Refusal) -> ViewPage:
    return notice_page(refusal.outcome, refusal.message, refusal.command)


def standard_page(outcome: Outcome) -> ViewPage:
    """The fixed notice page for one §30 outcome that needs no remedy command."""
    return _refusal_page(_Refusal(outcome))


def schema_incompatible_page(workspace: Path) -> ViewPage:
    return notice_page(
        "schema_incompatible", _SCHEMA_INCOMPATIBLE, _schema_remedy(workspace)
    )


def _schema_remedy(workspace: Path) -> str:
    """Name `db migrate` only where §12.14 offers a path; else `db status`."""

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
    """§30 rule 6: decode a selector value exactly once; `+` is not a space."""

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
    """§30 rule 6: split on literal `&`/`=` bytes before any decoding."""

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
        # §30 rule 3: `global` is the only `AssessmentScope` value.
        if value != "global":
            raise _Refusal("invalid_selector", _SELECTOR_SHAPE)
        return _Selector(scope="global")
    # §13.14 rule 1's stored-ID grammar decides before any lookup.
    if ENTITY_ID.fullmatch(value) is None:
        raise _Refusal("invalid_selector", _SNAPSHOT_GRAMMAR)
    return _Selector(snapshot_id=value)


def _resolve_snapshot(connection: sqlite3.Connection, selector: _Selector):
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

    # §30 rule 3 / §11.7: the unique current snapshot, never the newest of several.
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
        # §13.6 is not a corruption-repair surface, so no command is named.
        raise _Refusal(
            "assessment_inconsistent",
            "More than one current snapshot claims the global assessment "
            "view, so no single view can be served. Stop serving and recover "
            "the workspace invariant before reading this view again.",
        )
    return load_current_snapshot(connection, current[0]["id"])


def _require_integrity(selector: _Selector, snapshot, claims) -> None:
    # §16.11: the integrity half precedes the status half.
    failure = assessment_integrity_failure(snapshot, claims)
    if failure is None:
        return
    if failure == "aggregate_mismatch":
        # Verification recomputes the aggregate on this exact snapshot.
        raise _Refusal(
            "assessment_inconsistent",
            "This snapshot's stored verification aggregate is no longer the "
            "reduction of its own current claims, so its status is not a "
            "verdict serving can be read against.",
            _verify_command(snapshot.id),
        )
    # Generation creates a new ID: it repairs the identity URL, not an exact-ID one.
    raise _Refusal(
        "assessment_inconsistent",
        "This snapshot does not carry exactly one narrative summary claim "
        "matching its stored summary, so its claim set is broken stored state "
        "rather than a mirror.",
        _ASSESS_GENERATE if selector.by_identity else _ASSESS_LIST,
    )


@contextmanager
def _stored_state(selector: _Selector) -> Iterator[None]:
    """§30 rule 7: `IntegrityFailureError` in the read is a 409, not a 500."""

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
    # §16.11's export allowlist as a completed refusal.
    try:
        require_export_eligible(snapshot.verification_status)
    except AssessmentExportBlockedError as error:
        if snapshot.verification_status == "unverified":
            command = _verify_command(snapshot.id)
        else:
            # A negative verdict needs generation, which creates a new ID.
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
    """§30 rule 5: only the `question` text of unanswered `unknowns` — no IDs
    or fields that could become an answer link-back token."""

    try:
        document = SelfClaimsDocument.model_validate_json(members["self_claims.json"])
    except ValueError as error:
        # §30 rule 7: an expected projection failure, not `internal_error`.
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
    """Tell an unreadable schema from a non-directory `out/` (§13.14 rule 6),
    which the §8.1 read gate refuses alike but no migration resolves."""

    try:
        status = inspect_workspace(workspace, require_managed_root=False)
    except WorkspaceBusyError:
        return _Refusal("workspace_busy")
    if (
        status.recognized
        and status.compatible
        and _entry_is_present_non_directory(workspace / "out")
    ):
        return _Refusal("export_residual", _EXPORT_RESIDUAL)
    return _Refusal(
        "schema_incompatible", _SCHEMA_INCOMPATIBLE, _schema_remedy(workspace)
    )


@dataclass(frozen=True)
class Deadline:
    """§14.17: one absolute `time.monotonic` bound and its arithmetic."""

    at: float

    def left(self, clock: Callable[[], float] = time.monotonic) -> float:
        return self.at - clock()

    def expired(self, clock: Callable[[], float] = time.monotonic) -> bool:
        return self.left(clock) <= 0

    def capped(self, other: "Deadline | None") -> "Deadline":
        return self if other is None or self.at <= other.at else other


def _composed(page: ViewPage, deadline: Deadline) -> ViewPage:
    """§14.17: a page not fully composed by the deadline is a timeout instead."""

    if page.outcome == "processing_timeout" or not deadline.expired():
        return page
    return standard_page("processing_timeout")


def resolve(
    workspace: Path,
    route: bytes,
    query: bytes | None,
    *,
    deadline: float,
    register_connection: ConnectionRegistrar = _unregistered,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ViewPage:
    """Answer one §30 request. `route`/`query` are the received target bytes
    (`query` None without `?`); `deadline` is §14.17's `time.monotonic` bound;
    `register_connection` lets a transport interrupt the read on cancellation."""

    bound = Deadline(deadline)
    if route not in ROUTES:
        return _composed(standard_page("route_not_found"), bound)
    try:
        selector = _parse_selector(query)

        if bound.left() < _FIRST_READ_MARGIN_FACTOR * busy_timeout_ms / 1000:
            # §14.17: no room for a full contention wait — never open the read.
            raise _Refusal("processing_timeout")

        # Phase 1 — one §8.1 read transaction, closed before phase 2.
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
            raise _Refusal("workspace_busy") from error
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            raise _Refusal("workspace_busy") from error

        if bound.expired():
            raise _Refusal("processing_timeout")

        # Phase 2 — §13.14 revalidation with no transaction open.
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

        if bound.expired():
            raise _Refusal("processing_timeout")

        if route == MIRROR_ROUTE:
            # §30 rule 3: exactly the revalidated member bytes.
            page = ViewPage(
                outcome="served",
                status=_OUTCOMES["served"][0],
                body=members["report.html"],
                published_member=True,
            )
        else:
            document = _questions_document(
                members, export_command=_export_command(snapshot.id)
            )
            page = ViewPage(
                outcome="served", status=_OUTCOMES["served"][0], body=render_html(document)
            )
    except _Refusal as refusal:
        return _composed(_refusal_page(refusal), bound)
    except (Exp2ResError, sqlite3.Error, OSError, ValueError):
        # §30 rule 7: fail closed, name no detail.
        return _composed(standard_page("internal_error"), bound)
    return _composed(page, bound)
