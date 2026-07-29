"""Deterministic §17 assessment ``report.html`` renderer.

The page is self-contained by construction: one document, one inline
stylesheet, and nothing else. No script, form, embedded object, image, or URL
of any kind is ever emitted, so opening the file issues no request and reads
no second file. It renders from the same §17 document as ``report.md`` and
never from that member's bytes, so it inherits no Markdown escape sequence.
"""

from __future__ import annotations

import base64
import hashlib
from typing import get_args

from exp2res.domain.enums import VerificationStatus

from .document import Block, Key, Line, ReportDocument, Val, build_assessment_document
from .graph import AssessmentExportGraph
from .markdown import normalize_generated_text


__all__ = [
    "STYLESHEET",
    "content_security_policy",
    "escape_html",
    "render_html",
    "render_report_html",
]


# §17: one total mapping. Every code point has exactly one output, so no value
# can open, close, or alter an element or an attribute wherever it is emitted.
_HTML_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
}
_LINE_BREAK = "<br>"
# A status modifier class is emitted only for the closed §10 member set, so the
# stylesheet's selectors stay a fixed renderer-owned vocabulary.
_STATUS_VALUES = frozenset(get_args(VerificationStatus))

STYLESHEET = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa;
  --fg: #1c1d20;
  --muted: #5d636c;
  --line: #e3e3df;
  --card: #ffffff;
  --ok: #2f6f4f;
  --warn: #8a6320;
  --note: #3f5f86;
  --bad: #8f3a3a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a;
    --fg: #e7e9eb;
    --muted: #99a0a9;
    --line: #282c33;
    --card: #191c21;
    --ok: #7cc39a;
    --warn: #d8b06a;
    --note: #8fb0dc;
    --bad: #e08e8e;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Ubuntu,
    "Helvetica Neue", Arial, sans-serif;
  line-height: 1.55;
  overflow-wrap: break-word;
}
main { max-width: 46rem; margin: 0 auto; padding: 3rem 1.25rem 4.5rem; }
h1 { font-size: 1.55rem; letter-spacing: -0.01em; margin: 0 0 0.9rem; }
h2 {
  font-size: 1.02rem;
  font-weight: 600;
  margin: 2.4rem 0 0.8rem;
  padding-bottom: 0.35rem;
  border-bottom: 1px solid var(--line);
}
p { margin: 0 0 0.35rem; }
p:last-child { margin-bottom: 0; }
.meta { color: var(--muted); font-size: 0.9rem; }
.key { font-weight: 600; color: var(--fg); }
ul { list-style: none; margin: 0; padding: 0; }
li {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0.75rem 0.9rem;
  margin: 0 0 0.6rem;
}
li ul { margin-top: 0.65rem; }
li li {
  background: none;
  border: 0;
  border-left: 2px solid var(--line);
  border-radius: 0;
  padding: 0 0 0 0.85rem;
  margin: 0 0 0.55rem;
}
li li:last-child { margin-bottom: 0; }
.field { color: var(--muted); font-size: 0.93rem; margin-top: 0.3rem; }
.field strong { color: var(--fg); font-weight: 600; }
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85em;
}
.tag {
  display: inline-block;
  font-size: 0.8rem;
  padding: 0.02rem 0.5rem;
  border: 1px solid var(--line);
  border-radius: 999px;
}
.tag-supported { color: var(--ok); border-color: currentColor; }
.tag-partially_supported,
.tag-inferred_but_acceptable { color: var(--warn); border-color: currentColor; }
.tag-needs_clarification { color: var(--note); border-color: currentColor; }
.tag-contradicted,
.tag-unsupported,
.tag-rejected { color: var(--bad); border-color: currentColor; }
"""


def escape_html(value: str) -> str:
    """Escape one nonliteral value under §17's total HTML rule."""

    normalized = normalize_generated_text(value)
    escaped = "".join(_HTML_ESCAPES.get(character, character) for character in normalized)
    # Applied after escaping, so the inserted element is renderer-owned and no
    # source code point can reach the document as markup.
    return escaped.replace("\n", _LINE_BREAK)


def content_security_policy() -> str:
    """Admit the one inline stylesheet by hash and nothing else."""

    digest = base64.b64encode(hashlib.sha256(STYLESHEET.encode("utf-8")).digest())
    return (
        "default-src 'none'; "
        f"style-src 'sha256-{digest.decode('ascii')}'; "
        "base-uri 'none'; "
        "form-action 'none'"
    )


def _render_line(line: Line) -> str:
    parts: list[str] = []
    for segment in line:
        if isinstance(segment, Key):
            label = escape_html(segment.text)
            parts.append(
                f"<strong>{label}:</strong> "
                if segment.emphasized
                else f'<span class="key">{label}:</span> '
            )
        elif isinstance(segment, Val):
            value = escape_html(segment.text)
            if segment.style == "token":
                parts.append(f"<code>{value}</code>")
            elif segment.style == "status":
                classes = (
                    f"tag tag-{value}" if segment.text in _STATUS_VALUES else "tag"
                )
                parts.append(f'<span class="{classes}">{value}</span>')
            else:
                parts.append(value)
        else:
            parts.append(escape_html(segment.text))
    return "".join(parts)


def _render_block(block: Block, lines: list[str]) -> None:
    lines.append("<li>")
    lines.append(f"<p>{_render_line(block.lead)}</p>")
    for line in block.fields:
        lines.append(f'<p class="field">{_render_line(line)}</p>')
    if block.children:
        lines.append("<ul>")
        for child in block.children:
            _render_block(child, lines)
        lines.append("</ul>")
    lines.append("</li>")


def render_html(document: ReportDocument) -> bytes:
    """Emit one §17 document as the self-contained HTML member."""

    title = escape_html(document.title)
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="'
        f'{content_security_policy()}">',
        f"<title>{title}</title>",
        f"<style>{STYLESHEET}</style>",
        "</head>",
        "<body>",
        "<main>",
        f"<h1>{title}</h1>",
    ]
    lines.extend(f'<p class="meta">{_render_line(line)}</p>' for line in document.header)
    for section in document.sections:
        lines.append("<section>")
        lines.append(f"<h2>{escape_html(section.heading)}</h2>")
        if section.blocks:
            lines.append("<ul>")
            for block in section.blocks:
                _render_block(block, lines)
            lines.append("</ul>")
        lines.append("</section>")
    lines.extend(["</main>", "</body>", "</html>"])
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_report_html(graph: AssessmentExportGraph) -> bytes:
    return render_html(build_assessment_document(graph))
