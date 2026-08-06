"""Pure §30 rule 9 grammar and rule 2 framing tests over `RequestParser`.

No socket appears here: every case feeds exact bytes — whole or fragmented —
and asserts the closed classification. The synthetic requests are invented
Vera Example traffic and carry no real data.
"""

from __future__ import annotations

import pytest

from exp2res.services.view_http import (
    MAX_FIELD_LINES,
    MAX_HEADER_OCTETS,
    MAX_REQUEST_LINE_OCTETS,
    RequestParser,
    compose_response,
)
from exp2res.services.views import ViewPage, method_not_allowed_page


pytestmark = [pytest.mark.unit]

AUTHORITY = b"127.0.0.1:8731"


def raw_request(
    line: bytes = b"GET /mirror?scope=global HTTP/1.1",
    headers: tuple[bytes, ...] = (b"Host: " + AUTHORITY,),
) -> bytes:
    return b"\r\n".join((line, *headers, b"", b""))


def parse(data: bytes, chunk: int | None = None) -> RequestParser:
    parser = RequestParser()
    if chunk is None:
        parser.feed(data)
        return parser
    for start in range(0, len(data), chunk):
        parser.feed(data[start : start + chunk])
    return parser


@pytest.mark.parametrize("chunk", [None, 1, 7])
def test_complete_request_parses_identically_across_fragmentation(chunk):
    parser = parse(raw_request(), chunk)
    assert parser.done and not parser.malformed
    request = parser.request
    assert request is not None
    assert request.method == b"GET"
    assert request.path == b"/mirror"
    assert request.query == b"scope=global"
    assert request.host == AUTHORITY
    assert request.origin is None
    assert request.framing == "bodyless"
    assert parser.receive_budget == 0


def test_query_split_is_literal_and_first_question_mark_only():
    request = parse(raw_request(line=b"GET /mirror HTTP/1.1")).request
    assert request is not None and request.query is None
    request = parse(raw_request(line=b"GET /mirror? HTTP/1.1")).request
    assert request is not None and request.query == b""
    request = parse(raw_request(line=b"GET /m%69rror?a=b?c HTTP/1.1")).request
    assert request is not None
    assert request.path == b"/m%69rror"
    assert request.query == b"a=b?c"


def test_the_full_origin_form_byte_set_still_reaches_route_and_selector():
    # Narrowing the target grammar to RFC 3986 must not refuse a byte an
    # absolute path or query may legitimately carry: unreserved, every
    # sub-delim, `:`, `@`, and a percent-encoded octet all stay parseable,
    # so the refusal they earn is the route's or the selector's, not the
    # parser's.
    target = b"/a-b._~!$&'()*+,;=:@%2f?x=1&y=a:b@c,d;e=(f)*+$!'~"
    request = parse(raw_request(line=b"GET " + target + b" HTTP/1.1")).request
    assert request is not None
    assert request.path == b"/a-b._~!$&'()*+,;=:@%2f"
    assert request.query == b"x=1&y=a:b@c,d;e=(f)*+$!'~"


@pytest.mark.parametrize(
    "line",
    [
        b"GET  /mirror HTTP/1.1",
        b"GET /mirror  HTTP/1.1",
        b" GET /mirror HTTP/1.1",
        b"GET /mirror HTTP/1.1 ",
        b"GET /mirror HTTP/1.0",
        b"GET /mirror HTTP/2",
        b"GET /mirror http/1.1",
        b"GET /mirror",
        b"GET",
        b"",
        b"GET mirror HTTP/1.1",
        b"GET http://127.0.0.1:8731/mirror HTTP/1.1",
        b"CONNECT 127.0.0.1:8731 HTTP/1.1",
        b"OPTIONS * HTTP/1.1",
        b"GET /mirror\tHTTP/1.1",
        b"G@T /mirror HTTP/1.1",
        b"GET /mi\x80rror HTTP/1.1",
        b"GET /mi\x00rror HTTP/1.1",
        b"GET /mirror#frag HTTP/1.1",
        b"GET /mirror?scope=global#frag HTTP/1.1",
        # Visible bytes no absolute path or query may carry. §30 rule 9 runs
        # before the route, so these are `malformed_request` rather than a
        # later `route_not_found` or `invalid_selector`.
        b"GET /mirror\\evil HTTP/1.1",
        b'GET /mirror" HTTP/1.1',
        b"GET /mirror<script> HTTP/1.1",
        b"GET /mirror{0} HTTP/1.1",
        b"GET /mirror|pipe HTTP/1.1",
        b"GET /mirror^caret HTTP/1.1",
        b"GET /mirror?scope=glo\\bal HTTP/1.1",
    ],
)
def test_request_line_outside_the_closed_shape_is_malformed(line):
    parser = parse(raw_request(line=line))
    assert parser.malformed


def test_lowercase_method_is_grammar_valid_and_left_to_the_method_check():
    request = parse(raw_request(line=b"get /mirror HTTP/1.1")).request
    assert request is not None and request.method == b"get"


@pytest.mark.parametrize(
    "data",
    [
        b"GET /mirror HTTP/1.1\nHost: h\r\n\r\n",
        b"GET /mirror HTTP/1.1\r\nHost: h\n\r\n",
        b"GET /mirror HTTP/1.1\r\nHost: h\r\n\n",
        b"GET /mirror\rstill HTTP/1.1\r\nHost: h\r\n\r\n",
        b"GET /mirror HTTP/1.1\r\nHost: h\rx\r\n\r\n",
        b"GET /mirror HTTP/1.1\r\nHost: h\r\r\n\r\n",
    ],
)
def test_bare_lf_and_bare_cr_are_invalid(data):
    assert parse(data).malformed
    assert parse(data, chunk=1).malformed


def test_cr_split_across_fragments_still_terminates_a_line():
    parser = RequestParser()
    parser.feed(b"GET /mirror?scope=global HTTP/1.1\r")
    assert not parser.done
    parser.feed(b"\nHost: " + AUTHORITY + b"\r\n\r\n")
    assert parser.done and not parser.malformed


@pytest.mark.parametrize(
    "header",
    [
        b" folded: continuation",
        b"\tfolded",
        b"Host : spaced-colon",
        b": empty-name",
        b"Na me: value",
        b"Weird\x7f: value",
        b"X-Bytes: high\x80byte",
        b"X-Bytes: nul\x00byte",
        b"X-Bytes: del\x7fbyte",
        b"no-colon-line",
    ],
)
def test_field_line_grammar_violations_are_malformed(header):
    parser = parse(raw_request(headers=(b"Host: " + AUTHORITY, header)))
    assert parser.malformed


def test_empty_field_value_is_syntactically_valid():
    parser = parse(raw_request(headers=(b"Host: " + AUTHORITY, b"X-Empty:")))
    assert parser.done and not parser.malformed


@pytest.mark.parametrize(
    "headers",
    [
        (),
        (b"X-Not-Host: " + AUTHORITY,),
        (b"Host: " + AUTHORITY, b"Host: " + AUTHORITY),
        (b"Host: " + AUTHORITY, b"hOsT: " + AUTHORITY),
    ],
)
def test_host_cardinality_counts_every_spelling(headers):
    assert parse(raw_request(headers=headers)).malformed


def test_host_value_is_ows_trimmed_and_otherwise_exact():
    request = parse(
        raw_request(headers=(b"host:\t " + AUTHORITY + b" \t",))
    ).request
    assert request is not None and request.host == AUTHORITY


def test_origin_absent_single_and_repeated():
    assert parse(raw_request()).request.origin is None
    single = parse(
        raw_request(
            headers=(b"Host: " + AUTHORITY, b"Origin:  http://127.0.0.1:8731 ")
        )
    ).request
    assert single is not None and single.origin == b"http://127.0.0.1:8731"
    repeated = parse(
        raw_request(
            headers=(
                b"Host: " + AUTHORITY,
                b"Origin: http://127.0.0.1:8731",
                b"origin: http://127.0.0.1:8731",
            )
        )
    )
    assert repeated.malformed


@pytest.mark.parametrize(
    ("framing_headers", "expected"),
    [
        ((), "bodyless"),
        ((b"Content-Length: 0",), "bodyless"),
        ((b"content-LENGTH:  0 ",), "bodyless"),
        ((b"Content-Length: 17",), "declared_body"),
        ((b"Content-Length: 999999999999999999999999",), "declared_body"),
        ((b"Content-Length: 00",), None),
        ((b"Content-Length: 05",), None),
        ((b"Content-Length: +5",), None),
        ((b"Content-Length: -1",), None),
        ((b"Content-Length:",), None),
        ((b"Content-Length: 5 5",), None),
        ((b"Content-Length: 5,5",), None),
        ((b"Content-Length: 0x5",), None),
        ((b"Content-Length: 5", b"Content-Length: 5"), None),
        ((b"Content-Length: 5", b"Transfer-Encoding: chunked"), None),
        ((b"Transfer-Encoding: chunked",), None),
        ((b"transfer-encoding: identity",), None),
    ],
)
def test_framing_classification_is_closed(framing_headers, expected):
    parser = parse(
        raw_request(headers=(b"Host: " + AUTHORITY, *framing_headers))
    )
    if expected is None:
        assert parser.malformed
    else:
        assert not parser.malformed
        assert parser.request is not None
        assert parser.request.framing == expected


def test_request_line_cap_admits_exactly_8192_octets():
    # Line content of 8190 octets + CRLF = exactly the cap.
    target = b"/mirror?pad=" + b"a" * (8190 - len(b"GET  HTTP/1.1/mirror?pad="))
    line = b"GET " + target + b" HTTP/1.1"
    assert len(line) + 2 == MAX_REQUEST_LINE_OCTETS
    parser = parse(raw_request(line=line))
    assert parser.done and not parser.malformed
    over = parse(raw_request(line=line + b"a"))
    assert over.malformed


def test_request_line_overflow_needs_at_most_one_deciding_octet():
    parser = RequestParser()
    assert parser.receive_budget == MAX_REQUEST_LINE_OCTETS + 1
    parser.feed(b"G" * MAX_REQUEST_LINE_OCTETS)
    assert not parser.done
    assert parser.receive_budget == 1
    parser.feed(b"G")
    assert parser.malformed
    assert parser.receive_budget == 0


def test_header_budget_counts_cumulatively_across_fragments():
    parser = RequestParser()
    parser.feed(b"GET /mirror HTTP/1.1\r\n")
    assert parser.receive_budget == MAX_HEADER_OCTETS + 1
    parser.feed(b"Host: ")
    assert parser.receive_budget == MAX_HEADER_OCTETS + 1 - len(b"Host: ")
    parser.feed(AUTHORITY + b"\r\n")
    assert (
        parser.receive_budget
        == MAX_HEADER_OCTETS + 1 - len(b"Host: " + AUTHORITY + b"\r\n")
    )


def test_header_section_cap_admits_exactly_32768_octets():
    host_line = b"Host: " + AUTHORITY + b"\r\n"
    # One filler line sized so lines + terminating CRLF hit the cap exactly.
    filler_value_len = MAX_HEADER_OCTETS - len(host_line) - len(b"X-Pad: \r\n") - 2
    filler = b"X-Pad: " + b"b" * filler_value_len
    data = raw_request(headers=(b"Host: " + AUTHORITY, filler))
    parser = parse(data, chunk=1024)
    assert parser.done and not parser.malformed
    over = parse(raw_request(headers=(b"Host: " + AUTHORITY, filler + b"b")))
    assert over.malformed


def test_field_line_count_cap_is_64():
    padding = tuple(
        b"X-%d: v" % index for index in range(MAX_FIELD_LINES - 1)
    )
    parser = parse(raw_request(headers=(b"Host: " + AUTHORITY, *padding)))
    assert parser.done and not parser.malformed
    over = parse(
        raw_request(headers=(b"Host: " + AUTHORITY, *padding, b"X-Over: v"))
    )
    assert over.malformed


def test_bytes_after_the_terminating_empty_line_are_dropped_never_framed():
    # Bytes a peer bundles past the terminating empty line may already sit in
    # the same socket read; the parser discards them and its budget drops to
    # zero, so nothing after the one request is ever framed as a next one.
    pipelined = raw_request() + b"GET /questions?scope=global HTTP/1.1\r\n"
    parser = parse(pipelined)
    assert parser.done and not parser.malformed
    assert parser.request is not None and parser.request.path == b"/mirror"
    assert parser.receive_budget == 0
    parser.feed(b"more")
    assert parser.done and not parser.malformed


def test_compose_response_emits_exact_closed_headers():
    page = ViewPage(outcome="served", status=200, body=b"<p>Vera Example</p>")
    assert compose_response(page, head=False) == (
        b"HTTP/1.1 200 OK\r\n"
        b"Exp2Res-View-Outcome: served\r\n"
        b"Cache-Control: no-store\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Length: 19\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"<p>Vera Example</p>"
    )


def test_head_response_keeps_headers_and_content_length_without_body():
    page = ViewPage(outcome="served", status=200, body=b"<p>Vera Example</p>")
    head = compose_response(page, head=True)
    assert head.endswith(b"\r\n\r\n")
    assert b"Content-Length: 19\r\n" in head
    assert b"<p>" not in head


def test_method_not_allowed_response_declares_allow():
    response = compose_response(method_not_allowed_page(), head=False)
    assert response.startswith(b"HTTP/1.1 405 Method Not Allowed\r\n")
    assert b"Allow: GET, HEAD\r\n" in response
    assert b"Exp2Res-View-Outcome: method_not_allowed\r\n" in response
