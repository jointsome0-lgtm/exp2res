"""Offline §17 escaping, fencing, and assessment rendering tests."""

from __future__ import annotations

from dataclasses import replace
from itertools import cycle

from markdown_it import MarkdownIt
import pytest

from exp2res.errors import IntegrityFailureError
from exp2res.exports.markdown import (
    escape_generated,
    normalize_generated_text,
    source_voice_fence,
)
from exp2res.exports.report import render_report

from export_helpers import assessment_graph


pytestmark = [pytest.mark.unit, pytest.mark.golden]


PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"

EXPECTED_HEADINGS = (
    "## 1. Summary",
    "## 2. Strongly Supported Facts",
    "## 3. Recurring Signals",
    "## 4. Current Strengths",
    "## 5. Weakly Supported Strengths",
    "## 6. Gaps",
    "## 7. Contradictions",
    "## 8. Risks / Failure Modes",
    "## 9. Unknowns and Open Questions",
    "## 10. Counterevidence",
)

# Values a §15 writer could emit that would otherwise open a block, an inline
# construct, or raw HTML in the rendered report.
HOSTILE_VALUES = (
    "# Vera Example injected heading",
    "- Vera Example injected bullet",
    "+ Vera Example injected bullet",
    "1. Vera Example injected ordered item",
    "9) Vera Example injected ordered item",
    "> Vera Example injected quote",
    "Vera Example fence\n```\ninjected\n```",
    "Vera Example fence\n~~~\ninjected\n~~~",
    "Vera Example break\n***\n___\n---",
    "Vera Example setext\n===",
    "| Vera | Example |\n|---|---|\n| 1 | 2 |",
    "Vera Example table\n:--|:--",
    "<script>alert('Vera Example')</script>",
    "Vera Example <b>bold</b> & <em>more</em>",
    "[Vera Example](https://example.invalid)",
    "[Vera Example]: https://example.invalid",
    "~~Vera Example~~ and ~/Life/notes.md ~/other",
    "*Vera Example* _emphasis_ `code`",
    "    Vera Example indented",
    "Vera Example trailing  ",
    "Vera\tExample tab",
    "é " + PUNCTUATION,
)

PARSERS = (
    MarkdownIt("commonmark"),
    MarkdownIt("commonmark").enable(["table", "strikethrough"]),
)

# Everything the §17 renderer is allowed to build around an escaped value: its
# own list item and paragraph, plus text and the renderer-owned line breaks.
CONTAINED_TOKEN_TYPES = frozenset(
    {
        "bullet_list_open",
        "bullet_list_close",
        "list_item_open",
        "list_item_close",
        "paragraph_open",
        "paragraph_close",
        "inline",
        "text",
        "softbreak",
        "hardbreak",
    }
)


def token_types(tokens) -> list[str]:
    collected: list[str] = []
    for token in tokens:
        collected.append(token.type)
        if token.children:
            collected.extend(token_types(token.children))
    return collected


def token_text(tokens) -> str:
    parts: list[str] = []
    for token in tokens:
        if token.type == "inline":
            parts.append(token_text(token.children))
        elif token.type == "text":
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
    return "".join(parts)


def test_escape_generated_applies_the_closed_positional_set() -> None:
    escaped = escape_generated(
        "e\u0301\r\n" + PUNCTUATION + "\tcontinued",
        continuation_indent="  ",
    )
    assert escaped == (
        "é  \n  "
        # A line-leading `!` opens no block; `<` and `&` become character
        # references; `*`, `[`, `\`, `]`, `_`, backtick, and `~` always escape.
        + "!\"#$%&amp;'()\\*+,-./:;&lt;=>?@\\[\\\\\\]^\\_\\`{|}\\~"
        + "&#9;continued"
    )
    assert "\r" not in escaped


def test_escape_generated_keeps_prose_instants_and_typed_ids_legible() -> None:
    legible = (
        "2026-07-25T02:29:22.288649+00:00",
        "fact_vera_export_0001",
        "You validated the pipeline (approximately 20 or more runs).",
        "Which exact scale did you validate?",
        "Approximate range: 2026-04-01T00:00:00+00:00 / 2026-04-30T00:00:00+00:00",
        "missing_scale",
    )
    for value in legible:
        assert escape_generated(value) == value


def test_escape_generated_escapes_block_openers_only_when_line_leading() -> None:
    line_leading = {
        "# heading": "\\# heading",
        "- bullet": "\\- bullet",
        "+ bullet": "\\+ bullet",
        "> quote": "\\> quote",
        "=== setext": "\\=== setext",
        "|---|": "\\|---|",
        ":--|:--": "\\:--|:--",
        "1. ordered": "1\\. ordered",
        "42) ordered": "42\\) ordered",
        "1.": "1\\.",
        "1234567890. not a marker": "1234567890. not a marker",
        "1.5 million records": "1.5 million records",
        "  indented": "&#32;&#32;indented",
        "trailing  ": "trailing&#32;&#32;",
        "a # b - c > d | e = f : g + h 1. i": "a # b - c > d | e = f : g + h 1. i",
    }
    for value, expected in line_leading.items():
        assert escape_generated(value) == expected, value


def test_escape_generated_leaves_only_ascii_intraword_underscores_unescaped() -> None:
    underscores = {
        "fact_vera_0001": "fact_vera_0001",
        "a_1_b": "a_1_b",
        "_leading": "\\_leading",
        "trailing_": "trailing\\_",
        "double__underscore": "double\\_\\_underscore",
        "spaced _ underscore": "spaced \\_ underscore",
        "кир_иллица": "кир\\_иллица",
    }
    for value, expected in underscores.items():
        assert escape_generated(value) == expected, value


def test_escaped_values_round_trip_and_open_no_construct() -> None:
    for value in HOSTILE_VALUES:
        document = "- " + escape_generated(value, continuation_indent="  ")
        for parser in PARSERS:
            tokens = parser.parse(document)
            assert set(token_types(tokens)) <= CONTAINED_TOKEN_TYPES, value
            assert token_text(tokens) == normalize_generated_text(value), value


def test_blank_logical_line_splits_a_paragraph_but_stays_inside_its_block() -> None:
    document = "- " + escape_generated(
        "Vera Example first\n\nVera Example second", continuation_indent="  "
    )
    for parser in PARSERS:
        types = token_types(parser.parse(document))
        assert set(types) <= CONTAINED_TOKEN_TYPES
        assert types.count("list_item_open") == 1
        assert types.count("paragraph_open") == 2


def test_hostile_generated_values_cannot_add_a_report_block() -> None:
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
    hostile = replace(
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
    document = render_report(hostile).decode("utf-8")
    for parser in PARSERS:
        tokens = parser.parse(document)
        types = token_types(tokens)
        assert not {
            "fence",
            "code_block",
            "html_block",
            "html_inline",
            "blockquote_open",
            "table_open",
            "hr",
            "ordered_list_open",
            "link_open",
            "image",
            "s_open",
        } & set(types)
        headings = [
            token_text([tokens[index + 1]])
            for index, token in enumerate(tokens)
            if token.type == "heading_open"
        ]
        assert headings == ["Self-Assessment Snapshot"] + [
            heading.removeprefix("## ") for heading in EXPECTED_HEADINGS
        ]


def test_source_voice_fence_is_shortest_and_preserves_interior_bytes() -> None:
    excerpt = b"Vera Example\r\n`` code ``` tail\n"
    fenced = source_voice_fence(excerpt)
    assert fenced.startswith(b"````\n")
    assert fenced.endswith(b"````\n")
    assert fenced == b"````\n" + excerpt + b"````\n"


def test_renderer_is_byte_deterministic_and_uses_closed_order_and_empty_headings() -> None:
    graph = assessment_graph(all_sections=True)
    first = render_report(graph)
    second = render_report(graph)
    assert first == second
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    text = first.decode("utf-8")
    headings = tuple(line for line in text.splitlines() if line.startswith("## "))
    assert headings == EXPECTED_HEADINGS
    assert "Scope target:" not in text
    assert "Snapshot created: 2026-07-20T10:00:00+02:00" in text
    assert "**Status:** supported" in text
    assert "**Fact ID:** fact_vera_export_0001" in text
    assert "**Raw log IDs:** log_vera_export_0001" in text
    assert (
        "**Sources:** signal_vera_export_0001 (facts fact_vera_export_0001); "
        "facts fact_vera_export_0001" in text
    )
    assert "\\" not in text

    sparse = assessment_graph(all_sections=False)
    sparse_text = render_report(sparse).decode("utf-8")
    assert "## 3. Recurring Signals\n\n## 4. Current Strengths" in sparse_text
    assert "placeholder" not in sparse_text.lower()


def test_answered_since_synthesis_is_explicit_and_question_stays_beside_id() -> None:
    text = render_report(assessment_graph(answered=True)).decode("utf-8")
    unknown_section = text.split("## 9. Unknowns and Open Questions", 1)[1].split(
        "## 10. Counterevidence", 1
    )[0]
    # §17: an answered-after-synthesis question keeps its block — question
    # first, gap ID beside it — plus the explicit marker.
    assert (
        "- What scale did you validate?\n"
        "  **Gap ID:** gap_vera_export_0001\n" in unknown_section
    )
    assert "**Answered since synthesis:** yes" in unknown_section


def test_unmatched_non_summary_claim_fails_closed() -> None:
    graph = assessment_graph(all_sections=False)
    original = graph.claims[0]
    invalid = original.value.model_copy(
        update={
            "claim_kind": "hypothesis",
            "dimension": "technical_skill",
            "verification_status": "unverified",
        }
    )
    bad = replace(graph, claims=(replace(original, value=invalid),))
    with pytest.raises(IntegrityFailureError, match="assessment_claim_section_invalid"):
        render_report(bad)
