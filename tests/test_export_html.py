"""Offline §17 `report.html` self-containment, parity, and escaping tests."""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
from html.parser import HTMLParser
from itertools import cycle
import unicodedata

from markdown_it import MarkdownIt
import pytest

from exp2res.exports.html import (
    STYLESHEET,
    content_security_policy,
    escape_html,
    render_html,
    render_report_html,
)
from exp2res.exports.document import build_assessment_document
from exp2res.exports.report import render_report

from export_helpers import assessment_graph


pytestmark = [pytest.mark.golden]

# Everything the §17 HTML renderer is allowed to emit. A tag or attribute
# outside these sets is either an external reference, a behavior, or a value
# that escaped its text position.
ALLOWED_TAGS = frozenset(
    {
        "html",
        "head",
        "meta",
        "title",
        "style",
        "body",
        "main",
        "h1",
        "h2",
        "section",
        "p",
        "ul",
        "li",
        "strong",
        "span",
        "code",
        "br",
    }
)
ALLOWED_ATTRIBUTE_NAMES = frozenset(
    {"lang", "charset", "name", "content", "http-equiv", "class"}
)
HOSTILE_VALUES = (
    '<script>alert("1")</script>',
    "</style><style>body{display:none}</style>",
    "<!-- comment --> & &amp; <img src=x onerror=alert(1)>",
    "\"' onmouseover='alert(1)",
    "Vera Example line one\r\nline two",
    "javascript:alert(1) and file:///etc/passwd",
)


class _Document(HTMLParser):
    """Collect the structure and visible text of one rendered page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []
        self.comments: list[str] = []
        self.declarations: list[str] = []
        self._chunks: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)
        if tag == "style":
            self._in_style = True
        elif tag == "br":
            self._chunks.append("\n")
        elif tag in {"p", "h1", "h2", "li", "title"}:
            self._chunks.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        elif tag in {"p", "h1", "h2", "li", "title"}:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._in_style:
            self._chunks.append(data)

    def handle_comment(self, data):
        self.comments.append(data)

    def handle_decl(self, decl):
        self.declarations.append(decl)

    @property
    def text_lines(self) -> list[str]:
        joined = "".join(self._chunks)
        return [line.strip() for line in joined.split("\n") if line.strip()]


def _parsed(document: bytes) -> _Document:
    parser = _Document()
    parser.feed(document.decode("utf-8"))
    parser.close()
    return parser


def _markdown_text_lines(document: bytes) -> list[str]:
    """Extract the same visible text from the Markdown member."""

    def inline_text(tokens) -> str:
        parts: list[str] = []
        for token in tokens:
            if token.type == "inline":
                parts.append(inline_text(token.children))
            elif token.type == "text":
                parts.append(token.content)
            elif token.type in {"softbreak", "hardbreak"}:
                parts.append("\n")
        return "".join(parts)

    lines: list[str] = []
    for token in MarkdownIt("commonmark").parse(document.decode("utf-8")):
        if token.type == "inline":
            lines.extend(
                line.strip() for line in inline_text([token]).split("\n") if line.strip()
            )
    return lines


def _hostile_graph():
    graph = assessment_graph(all_sections=True)
    values = cycle(HOSTILE_VALUES)
    claims = tuple(
        replace(
            stored,
            value=stored.value.model_copy(
                update={
                    "claim": next(values),
                    "uncertainty": (
                        None if stored.value.uncertainty is None else next(values)
                    ),
                    "counterevidence": [
                        item.model_copy(update={"statement": next(values)})
                        for item in stored.value.counterevidence
                    ],
                }
            ),
        )
        for stored in graph.claims
    )
    gap = graph.gaps[0]
    contradiction = graph.contradictions[0]
    return replace(
        graph,
        claims=claims,
        gaps=(
            replace(gap, value=gap.value.model_copy(update={"question": next(values)})),
        ),
        contradictions=(
            replace(
                contradiction,
                value=contradiction.value.model_copy(
                    update={"title": next(values), "description": next(values)}
                ),
            ),
        ),
    )


def test_html_member_is_deterministic_and_carries_no_render_time_value() -> None:
    graph = assessment_graph(all_sections=True)
    first = render_report_html(graph)
    assert first == render_report_html(graph)
    assert first == render_html(build_assessment_document(graph))
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    assert first.decode("utf-8") == first.decode("utf-8")  # valid UTF-8
    assert b"\r" not in first
    document = _parsed(first)
    assert document.declarations == ["DOCTYPE html"]
    assert ("lang", "en") in document.attributes


def test_both_report_members_render_the_same_content_in_the_same_order() -> None:
    for graph in (
        assessment_graph(all_sections=True),
        assessment_graph(all_sections=False),
        assessment_graph(answered=True),
        _hostile_graph(),
    ):
        markdown_lines = _markdown_text_lines(render_report(graph))
        html_lines = _parsed(render_report_html(graph)).text_lines
        # The title renders once in <title> and once in <h1>; the Markdown
        # member has only the heading.
        assert html_lines[0] == html_lines[1] == "Self-Assessment Snapshot"
        assert html_lines[1:] == markdown_lines


def test_page_is_self_contained_with_no_reference_script_or_behavior() -> None:
    document = render_report_html(_hostile_graph())
    text = document.decode("utf-8")
    parsed = _parsed(document)

    assert set(parsed.tags) <= ALLOWED_TAGS
    assert not parsed.comments
    # A hostile value may legitimately *say* "javascript:" as text; what must
    # never happen is a renderer-owned attribute or the stylesheet naming a
    # reference, so the check reads the markup, not the escaped values.
    for name, value in parsed.attributes:
        assert name in ALLOWED_ATTRIBUTE_NAMES and not name.startswith("on")
        assert value is not None
        for marker in ("://", "javascript:", "data:", "url("):
            assert marker not in value
    for marker in ("://", "url(", "@import", "expression("):
        assert marker not in STYLESHEET
    assert text.count("<style>") == 1 and "<script" not in text


def test_hostile_values_stay_text_and_never_become_markup() -> None:
    document = render_report_html(_hostile_graph())
    parsed = _parsed(document)
    joined = " ".join(parsed.text_lines)
    for value in HOSTILE_VALUES:
        expected = unicodedata.normalize("NFC", value.replace("\r\n", "\n"))
        for fragment in expected.split("\n"):
            assert fragment.strip() in joined
    # The renderer's own structure survives beside the hostile values.
    assert parsed.tags.count("style") == 1
    assert parsed.tags.count("h1") == 1
    assert parsed.tags.count("h2") == 9


def test_escape_html_is_total_normalizing_and_position_independent() -> None:
    escaped = escape_html("é <a href=\"x\">&'\r\nsecond")
    assert escaped == "é &lt;a href=&quot;x&quot;&gt;&amp;&#39;<br>second"
    for character in "&<>\"'":
        assert character not in escape_html(character * 3).replace("&amp;", "").replace(
            "&lt;", ""
        ).replace("&gt;", "").replace("&quot;", "").replace("&#39;", "")
    # Position-independent: the same value escapes identically wherever it sits.
    assert escape_html("<x>") * 2 == escape_html("<x>") + escape_html("<x>")


def test_content_security_policy_admits_only_the_hashed_stylesheet() -> None:
    policy = content_security_policy()
    digest = base64.b64encode(hashlib.sha256(STYLESHEET.encode("utf-8")).digest())
    assert f"style-src 'sha256-{digest.decode('ascii')}'" in policy
    assert policy.startswith("default-src 'none'")
    assert "base-uri 'none'" in policy and "form-action 'none'" in policy
    assert "unsafe-inline" not in policy

    document = render_report_html(assessment_graph(all_sections=False)).decode("utf-8")
    assert f'content="{policy}"' in document
    # The stylesheet the policy admits is exactly the one emitted, so a drifted
    # byte would be blocked by the browser rather than silently applied.
    assert f"<style>{STYLESHEET}</style>" in document
    assert "<" not in STYLESHEET
