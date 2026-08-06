"""§30 rule 9's transport grammar as a pure incremental parser.

This module performs no I/O and reads no clock: the transport feeds it the
bytes one socket produced and asks `receive_budget` how many more octets it
may read. That budget is always the applicable §30 rule 9 cap's remaining
allowance plus the one deciding octet, so the parser establishes overflow by
reading at most one octet beyond a cap and never buffers more than the capped
component plus that octet.

The grammar is exact and closed: CRLF line endings only, tchar field names,
SP/HTAB/visible-ASCII field values, origin-form `HTTP/1.1` request lines, and
exactly one `Host` field. Nothing is normalized — no folding, joining,
case-folding of values, or duplicate-field policy — so §30 rule 2's framing
classification and rule 1's authority comparison run over the exact received
bytes and no parser behavior can change an outcome.

`compose_response` is the one writer of response bytes: every §30 rule 6
response header — the outcome class, `Cache-Control: no-store`, the exact
media type, and `Connection: close` — comes from here and from the composed
`ViewPage`, never from request bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from exp2res.services.views import ViewPage


__all__ = [
    "MAX_FIELD_LINES",
    "MAX_HEADER_OCTETS",
    "MAX_REQUEST_LINE_OCTETS",
    "ParsedRequest",
    "RequestParser",
    "compose_response",
]


# §30 rule 9: fixed service constants with no flag, environment, or
# configuration representation.
MAX_REQUEST_LINE_OCTETS = 8192
MAX_HEADER_OCTETS = 32768
MAX_FIELD_LINES = 64

_TCHAR = frozenset(
    b"!#$%&'*+-.^_`|~"
    b"0123456789"
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"abcdefghijklmnopqrstuvwxyz"
)
# Origin-form's byte set: RFC 3986 `pchar` — unreserved, sub-delims, `:` and
# `@` — plus `/` for segment separators, `?` for the query, and `%` for a
# percent-encoded octet. Every other visible byte, including `"`, `<`, `>`,
# `\`, `^`, `` ` ``, `{`, `}`, `|`, and the fragment's `#`, is outside the
# target grammar, so §30 rule 9 refuses it during transport parsing instead
# of letting it reach a route or a selector.
_TARGET = frozenset(
    b"-._~!$&'()*+,;=:@/?%"
    b"0123456789"
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"abcdefghijklmnopqrstuvwxyz"
)
_HEXDIG = frozenset(b"0123456789abcdefABCDEF")
_OWS = b" \t"
_CR = 0x0D
_LF = 0x0A

Framing = Literal["bodyless", "declared_body"]


def _complete_escapes(target: bytes) -> bool:
    """Every `%` in the target opens a full `%` HEXDIG HEXDIG triplet.

    The byte set alone accepts `%` anywhere, so `/mirror%` and `/mirror%GG`
    would parse and reach a route. They are not origin-form targets, and
    nothing downstream decodes them into one — rule 6 splits the query on
    literal bytes — so the escape is checked here, where §30 rule 9 puts it.
    Work stays bounded by the request line's own cap.
    """

    index = target.find(b"%")
    while index >= 0:
        escape = target[index + 1 : index + 3]
        if len(escape) != 2 or any(byte not in _HEXDIG for byte in escape):
            return False
        index = target.find(b"%", index + 3)
    return True


@dataclass(frozen=True)
class ParsedRequest:
    """One complete request envelope, reduced to the fields the rules consult.

    `host` and `origin` are the OWS-trimmed exact value bytes for §30 rule 1's
    literal comparison; `origin` is `None` only when the request carried no
    `Origin` field, which passes that check. Every other header was validated
    syntactically and then ignored (§30 rule 6).
    """

    method: bytes
    path: bytes
    query: bytes | None
    host: bytes
    origin: bytes | None
    framing: Framing


class RequestParser:
    """An incremental state machine over one request's raw envelope.

    Feed it exact received byte fragments; counts are cumulative across
    fragments, so splitting a line or section never resets a bound. Once
    `done`, the request is either `malformed` — §30 rule 7's first-ordered
    refusal — or available as `request`, and any bytes past the terminating
    empty line are dropped unread: the connection serves one request and
    closes, so no undrained byte is ever framed as a next request.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._state: Literal["line", "headers", "complete", "malformed"] = "line"
        self._header_octets = 0
        self._field_lines = 0
        self._method = b""
        self._path = b""
        self._query: bytes | None = None
        self._host_values: list[bytes] = []
        self._origin_values: list[bytes] = []
        self._content_lengths: list[bytes] = []
        self._transfer_encodings = 0
        self._request: ParsedRequest | None = None

    @property
    def done(self) -> bool:
        return self._state in ("complete", "malformed")

    @property
    def malformed(self) -> bool:
        return self._state == "malformed"

    @property
    def request(self) -> ParsedRequest | None:
        return self._request

    @property
    def receive_budget(self) -> int:
        """How many octets the transport may still read: cap remainder + 1."""

        if self.done:
            return 0
        if self._state == "line":
            return MAX_REQUEST_LINE_OCTETS - len(self._buffer) + 1
        pending = self._header_octets + len(self._buffer)
        return MAX_HEADER_OCTETS - pending + 1

    def feed(self, data: bytes) -> None:
        if self.done:
            return
        self._buffer.extend(data)
        while not self.done:
            index = self._buffer.find(_LF)
            if index < 0:
                self._check_partial()
                return
            if index == 0 or self._buffer[index - 1] != _CR:
                # A bare LF never terminates a line (§30 rule 9).
                self._fail()
                return
            content = bytes(self._buffer[: index - 1])
            if _CR in content:
                # A CR appears only immediately before the terminating LF.
                self._fail()
                return
            del self._buffer[: index + 1]
            self._consume_line(content, index + 1)

    def _check_partial(self) -> None:
        """Bound the incomplete tail: overflow and a bare CR fail here."""

        if self._buffer.find(_CR, 0, len(self._buffer) - 1) >= 0:
            self._fail()
            return
        if self._state == "line":
            if len(self._buffer) > MAX_REQUEST_LINE_OCTETS:
                self._fail()
        elif self._header_octets + len(self._buffer) > MAX_HEADER_OCTETS:
            self._fail()

    def _consume_line(self, content: bytes, octets: int) -> None:
        if self._state == "line":
            if octets > MAX_REQUEST_LINE_OCTETS:
                self._fail()
                return
            self._parse_request_line(content)
            return
        self._header_octets += octets
        if self._header_octets > MAX_HEADER_OCTETS:
            self._fail()
            return
        if not content:
            self._finalize()
            return
        self._field_lines += 1
        if self._field_lines > MAX_FIELD_LINES:
            self._fail()
            return
        self._parse_field_line(content)

    def _parse_request_line(self, content: bytes) -> None:
        """Exactly method SP origin-form-target SP `HTTP/1.1` (§30 rule 9).

        Splitting on the single SP byte makes extra whitespace an empty part,
        so any other spacing fails; absolute-form, authority-form,
        asterisk-form, and every other HTTP version fail the closed shape and
        are never normalized into a route.
        """

        parts = content.split(b" ")
        if len(parts) != 3:
            self._fail()
            return
        method, target, version = parts
        if version != b"HTTP/1.1":
            self._fail()
            return
        if not method or any(byte not in _TCHAR for byte in method):
            self._fail()
            return
        # §30 rule 9 puts transport parsing first, so a target that is not a
        # well-formed origin-form — a raw `#` opening a fragment, a `\` or `"`
        # no absolute path or query may contain, a `%` that is not a complete
        # escape — is `malformed_request` here rather than a later
        # `route_not_found` or `invalid_selector` reached by normalizing it
        # into a route.
        if (
            not target.startswith(b"/")
            or any(byte not in _TARGET for byte in target)
            or not _complete_escapes(target)
        ):
            self._fail()
            return
        self._method = method
        path, separator, query = target.partition(b"?")
        self._path = path
        self._query = query if separator else None
        self._state = "headers"

    def _parse_field_line(self, content: bytes) -> None:
        """One field line: tchar name, immediate `:`, bounded value bytes.

        A line beginning with SP or HTAB — obsolete folding — fails the name
        grammar here, so folding is never unfolded. Field names are
        recognized ASCII-case-insensitively; values are never case-folded.
        """

        colon = content.find(b":")
        if colon <= 0:
            self._fail()
            return
        name = content[:colon]
        if any(byte not in _TCHAR for byte in name):
            self._fail()
            return
        value = content[colon + 1 :]
        if any(
            byte not in (0x20, 0x09) and (byte < 0x21 or byte > 0x7E)
            for byte in value
        ):
            self._fail()
            return
        lowered = name.lower()
        if lowered == b"host":
            self._host_values.append(value)
        elif lowered == b"origin":
            self._origin_values.append(value)
        elif lowered == b"content-length":
            self._content_lengths.append(value)
        elif lowered == b"transfer-encoding":
            self._transfer_encodings += 1

    def _finalize(self) -> None:
        # §30 rule 9: exactly one Host, counting every spelling; §30 rule 1:
        # at most one declared Origin, even with byte-equal repetition.
        if len(self._host_values) != 1 or len(self._origin_values) > 1:
            self._fail()
            return
        framing = self._classify_framing()
        if framing is None:
            self._fail()
            return
        self._request = ParsedRequest(
            method=self._method,
            path=self._path,
            query=self._query,
            host=self._host_values[0].strip(_OWS),
            origin=(
                self._origin_values[0].strip(_OWS) if self._origin_values else None
            ),
            framing=framing,
        )
        self._buffer.clear()
        self._state = "complete"

    def _classify_framing(self) -> Framing | None:
        """§30 rule 2's one closed classification; `None` is a parse failure.

        Any `Transfer-Encoding`, any field combination or repetition, and any
        `Content-Length` value outside the two canonical forms fail before
        the method check, so no joining, overflow behavior, or duplicate
        policy can change the outcome.
        """

        if self._transfer_encodings:
            return None
        if not self._content_lengths:
            return "bodyless"
        if len(self._content_lengths) != 1:
            return None
        value = self._content_lengths[0].strip(_OWS)
        if value == b"0":
            return "bodyless"
        if value.isdigit() and not value.startswith(b"0"):
            return "declared_body"
        return None

    def _fail(self) -> None:
        self._buffer.clear()
        self._state = "malformed"


_REASONS = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    421: "Misdirected Request",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def compose_response(page: ViewPage, *, head: bool) -> bytes:
    """Serialize one composed outcome as its complete HTTP/1.1 response.

    A `HEAD` response carries exactly the `GET` outcome's status and headers
    — `Content-Length` included — with an empty body (§30 rule 2). The body
    bytes are appended untouched: for the mirror they are the revalidated
    published member, which nothing may rewrite.
    """

    lines = [
        f"HTTP/1.1 {page.status} {_REASONS[page.status]}",
        f"Exp2Res-View-Outcome: {page.outcome}",
        "Cache-Control: no-store",
        f"Content-Type: {page.content_type}",
        f"Content-Length: {len(page.body)}",
    ]
    if page.outcome == "method_not_allowed":
        lines.append("Allow: GET, HEAD")
    lines.append("Connection: close")
    header = "\r\n".join(lines).encode("ascii") + b"\r\n\r\n"
    return header if head else header + page.body
