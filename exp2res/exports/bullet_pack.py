"""Deterministic §18 ``bullet_pack.md`` renderer."""

from __future__ import annotations

from exp2res.domain.enums import ResumeTargetSection

from .branch import BranchExportGraph
from .markdown import escape_generated


__all__ = ["render_bullet_pack", "section_heading"]


_TITLE = "# Verified Bullet Pack"
_SECTIONS: tuple[str, ...] = tuple(ResumeTargetSection.__args__)  # type: ignore[attr-defined]


def section_heading(section: str) -> str:
    """Derive one §18 heading from its canonical snake-case member.

    The derivation is closed: split on ``_``, capitalize the first ASCII
    letter of each token, join with one space. No display table, title map, or
    locale-dependent casing participates, so the heading bytes are the same
    everywhere the member name is.
    """

    tokens = []
    for token in section.split("_"):
        head, tail = token[:1], token[1:]
        tokens.append(head.upper() + tail if head.isascii() else token)
    return " ".join(tokens)


def render_bullet_pack(graph: BranchExportGraph) -> bytes:
    """Render every retained current branch bullet exactly once, in order.

    The renderer authors no factual text at all: outside the fixed title, the
    six derived headings, and §17's escaping and hard-break syntax, every byte
    comes from one persisted `ResumeBullet.text`. §18 forbids a bridge,
    summary, transition, or filler line, which is why an empty section renders
    its heading and nothing else.
    """

    grouped: dict[str, list[str]] = {section: [] for section in _SECTIONS}
    for item in graph.bullets:
        # §13.10 render order is already the graph's bullet order, and grouping
        # by section preserves it inside each section.
        grouped[item.value.target_section].append(item.value.text)

    lines: list[str] = [_TITLE, ""]
    for index, section in enumerate(_SECTIONS):
        lines.append(f"## {section_heading(section)}")
        texts = grouped[section]
        if texts:
            lines.append("")
            for text in texts:
                # A continuation line indents to the item's content column, or
                # an embedded break would leave the list item (§17).
                lines.append(f"- {escape_generated(text, continuation_indent='  ')}")
        # Every heading renders; only the final section gets no separator.
        if index != len(_SECTIONS) - 1:
            lines.append("")

    # §13.12 supplies the one final LF and forbids a trailing empty line.
    return ("\n".join(lines) + "\n").encode("utf-8")
