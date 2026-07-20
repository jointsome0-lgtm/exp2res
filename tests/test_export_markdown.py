"""Offline §17 escaping, fencing, and assessment rendering tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from exp2res.errors import IntegrityFailureError
from exp2res.exports.markdown import escape_generated, source_voice_fence
from exp2res.exports.report import render_report

from export_helpers import assessment_graph


pytestmark = [pytest.mark.unit, pytest.mark.golden]


def test_escape_generated_covers_every_metacharacter_tab_and_embedded_newline() -> None:
    punctuation = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    escaped = escape_generated(
        "e\u0301\r\n" + punctuation + "\tcontinued",
        continuation_indent="  ",
    )
    assert escaped == (
        "é  \n  "
        + "".join("\\" + character for character in punctuation)
        + "&#9;continued"
    )
    assert "\r" not in escaped


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
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 1. Summary",
        "## 2. Strongly Supported Facts",
        "## 3. Recurring Signals",
        "## 4. Current Strengths",
        "## 5. Weakly Supported Strengths",
        "## 6. Gaps",
        "## 7. Contradictions",
        "## 8. Risks / Failure Modes",
        "## 9. Unknowns",
        "## 10. Questions Worth Answering",
        "## 11. Evidence Map",
        "## 12. Counterevidence",
    ]
    assert "Scope target:" not in text
    assert r"Snapshot created: 2026\-07\-20T10\:00\:00\+02\:00" in text
    assert "**Status:** supported" in text
    assert r"**Fact ID:** fact\_vera\_export\_0001" in text

    sparse = assessment_graph(all_sections=False)
    sparse_text = render_report(sparse).decode("utf-8")
    assert "## 3. Recurring Signals\n\n## 4. Current Strengths" in sparse_text
    assert "placeholder" not in sparse_text.lower()


def test_answered_since_synthesis_is_explicit_and_question_is_omitted() -> None:
    text = render_report(assessment_graph(answered=True)).decode("utf-8")
    assert "**Answered since synthesis:** yes" in text
    question_section = text.split("## 10. Questions Worth Answering", 1)[1].split(
        "## 11. Evidence Map", 1
    )[0]
    assert "What scale" not in question_section


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
