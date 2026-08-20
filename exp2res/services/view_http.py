"""§30 rule 9's closed transport grammar as a pure incremental parser (no I/O,
no clock, nothing normalized) plus the one writer of §30 rule 6 response bytes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from exp2res.services.views import ViewPage


# §30 rule 9: fixed constants, not configurable.
MAX_REQUEST_LINE_OCTETS = 8192
MAX_HEADER_OCTETS = 32768
MAX_FIELD_LINES = 64

_TCHAR = frozenset(
    b"!#$%&'*+-.^_`|~"
    b"0123456789"
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"abcdefghijklmnopqrstuvwxyz"
)
# Origin-form bytes: RFC 3986 `pchar` plus `/`, `?`, `%`. §30 rule 9 refuses
# anything else (`#`, `\`, `"`, …) here, before it can reach a route.
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


def _complete_escapes(path: bytes) -> bool:
    """§30 rule 9: every `%` in the path is a full triplet; the query is
    excluded because rule 6 hands its escapes to selector parsing."""

    index = path.find(b"%")
    while index >= 0:
        escape = path[index + 1 : index + 3]
        if len(escape) != 2 or any(byte not in _HEXDIG for byte in escape):
            return False
        index = path.find(b"%", index + 3)
    return True


@dataclass(frozen=True)
class ParsedRequest:
    """The fields the rules consult; `host`/`origin` are OWS-trimmed exact
    bytes for §30 rule 1, `origin` None when the field was absent."""

    method: bytes
    path: bytes
    query: bytes | None
    host: bytes
    origin: bytes | None
    framing: Framing


class RequestParser:
    """Incremental state machine over one request envelope; counts are
    cumulative across fragments, and bytes past the empty line are dropped."""

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
        """Cap remainder + 1: overflow is established by one octet past the cap."""

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
                # §30 rule 9: a bare LF never terminates a line.
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
        """Overflow and a bare CR in the incomplete tail fail here."""

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
        """§30 rule 9: exactly method SP origin-form-target SP `HTTP/1.1`."""

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
        # §30 rule 9: a malformed target is `malformed_request` here, never
        # normalized into a later `route_not_found` or `invalid_selector`.
        if not target.startswith(b"/") or any(
            byte not in _TARGET for byte in target
        ):
            self._fail()
            return
        path, separator, query = target.partition(b"?")
        # Path only; §30 rule 6 gives the query's escapes to selector parsing.
        if not _complete_escapes(path):
            self._fail()
            return
        self._method = method
        self._path = path
        self._query = query if separator else None
        self._state = "headers"

    def _parse_field_line(self, content: bytes) -> None:
        """tchar name, immediate `:`, bounded value; folding fails the name
        grammar, names match case-insensitively, values are never folded."""

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
        # §30 rule 9: exactly one Host; §30 rule 1: at most one Origin.
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
        """§30 rule 2's closed classification; `None` is a parse failure."""

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


def compose_response_parts(page: ViewPage, *, head: bool) -> tuple[bytes, bytes]:
    """Header and body parts; §30 rule 2 HEAD keeps GET's headers with an
    empty body, and the body is the original (never copied) bytes object."""

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
    return header, b"" if head else page.body


