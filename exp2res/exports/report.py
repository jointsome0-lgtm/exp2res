"""Deterministic §17 assessment ``report.md`` renderer."""

from __future__ import annotations

from .document import (
    Block,
    Key,
    Line,
    ReportDocument,
    Val,
    build_assessment_document,
    claim_section,
)
from .graph import AssessmentExportGraph
from .markdown import escape_generated


__all__ = ["claim_section", "render_markdown", "render_report"]


def _render_line(line: Line, continuation_indent: str) -> str:
    parts: list[str] = []
    for segment in line:
        if isinstance(segment, Key):
            parts.append(
                f"**{segment.text}:** " if segment.emphasized else f"{segment.text}: "
            )
        elif isinstance(segment, Val):
            parts.append(
                escape_generated(segment.text, continuation_indent=continuation_indent)
            )
        else:
            parts.append(segment.text)
    return "".join(parts)


def _render_block(block: Block, depth: int, lines: list[str]) -> None:
    # A continuation line must indent to the current item's content column,
    # or an embedded line break would leave the block or list item (§17).
    indent = "  " * depth
    continuation = "  " * (depth + 1)
    lines.append(f"{indent}- {_render_line(block.lead, continuation)}")
    for line in block.fields:
        lines.append(f"{continuation}{_render_line(line, continuation)}")
    for child in block.children:
        _render_block(child, depth + 1, lines)


def render_markdown(document: ReportDocument) -> bytes:
    """Emit one §17 document as the canonical Markdown member."""

    lines = [f"# {document.title}", ""]
    lines.extend(_render_line(line, "") for line in document.header)
    lines.append("")
    for section in document.sections:
        lines.append(f"## {section.heading}")
        for block in section.blocks:
            _render_block(block, 0, lines)
        lines.append("")
    return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")


def render_report(graph: AssessmentExportGraph) -> bytes:
    return render_markdown(build_assessment_document(graph))
