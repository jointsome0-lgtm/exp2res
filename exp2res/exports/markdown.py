"""Shared deterministic §17 generated-voice Markdown machinery."""

from __future__ import annotations

from datetime import datetime
import re
import unicodedata

from exp2res.domain.models import OccurredAt


_BACKTICK_RUN = re.compile(br"`+")

# §17's closed positional escape set. Nothing outside these three groups is
# escaped, so ISO 8601 instants, typed IDs, and ordinary prose stay legible in
# the canonical file the owner reads directly.
_CHARACTER_REFERENCES = {"\t": "&#9;", "<": "&lt;", "&": "&amp;"}
_SPACE_REFERENCE = "&#32;"
_ALWAYS_ESCAPED = frozenset("\\`*[]~")
_LINE_LEADING_ESCAPED = frozenset("-+#>=|:")
# A marker is one to nine digits, `.` or `)`, then a space or the line end; a
# tab cannot follow one because the tab itself becomes a character reference.
_ORDERED_LIST_MARKER = re.compile(r"[0-9]{1,9}[.)](?=[ ]|$)")
_ASCII_ALPHANUMERIC = frozenset(
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


def render_occurred(
    occurred: OccurredAt, *, attested_as_of: datetime | None = None
) -> str:
    """Render one temporal placement as deterministic §17 plain text."""

    if occurred.precision == "unknown":
        return "Unknown occurrence time"
    assert occurred.start is not None
    start = occurred.start.isoformat()
    if occurred.precision in {"date_range", "approximate_range"}:
        flavor = (
            "Approximate range" if occurred.precision == "approximate_range"
            else "Date range"
        )
        if occurred.end is not None:
            return f"{flavor}: {start} / {occurred.end.isoformat()}"
        if attested_as_of is None:
            raise ValueError("open-ended occurrence requires an as-of attestation")
        open_flavor = (
            "Approximate open period"
            if occurred.precision == "approximate_range"
            else "Open period"
        )
        return (
            f"{open_flavor}: {start}; "
            f"no recorded end as of {attested_as_of.isoformat()}"
        )
    labels = {
        "exact_datetime": "Exact datetime",
        "exact_day": "Exact day (representational anchor)",
        "week": "Week (representational anchor)",
        "month": "Month (representational anchor)",
        "quarter": "Quarter (representational anchor)",
        "year": "Year (representational anchor)",
    }
    return f"{labels[occurred.precision]}: {start}"


def normalize_generated_text(value: str) -> str:
    """Apply the §13.12 generated-text projection without Markdown escaping."""

    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _is_inert_intraword_underscore(body: str, index: int) -> bool:
    """Report whether CommonMark's intraword rule already neutralizes ``_``.

    The neighbour test is ASCII-only and therefore independent of the Unicode
    version in use, which keeps the escaped bytes equal across implementations.
    """

    if index == 0 or index + 1 >= len(body):
        return False
    return (
        body[index - 1] in _ASCII_ALPHANUMERIC
        and body[index + 1] in _ASCII_ALPHANUMERIC
    )


def _escape_logical_line(line: str) -> str:
    """Apply §17's positional escape rule to one logical line."""

    body_start = 0
    while body_start < len(line) and line[body_start] == " ":
        body_start += 1
    body_end = len(line)
    while body_end > body_start and line[body_end - 1] == " ":
        body_end -= 1
    body = line[body_start:body_end]

    # A line that starts with a space cannot open a block construct once its
    # indentation is emitted as character references, so the line-leading
    # escapes apply only to a body that is itself at the start of the line.
    ordered_delimiter = -1
    if body_start == 0:
        marker = _ORDERED_LIST_MARKER.match(body)
        if marker is not None:
            ordered_delimiter = marker.end() - 1

    parts: list[str] = [_SPACE_REFERENCE] * body_start
    for index, character in enumerate(body):
        reference = _CHARACTER_REFERENCES.get(character)
        if reference is not None:
            parts.append(reference)
        elif character in _ALWAYS_ESCAPED:
            parts.extend(("\\", character))
        elif character == "_":
            if _is_inert_intraword_underscore(body, index):
                parts.append("_")
            else:
                parts.extend(("\\", "_"))
        elif body_start == 0 and index == 0 and character in _LINE_LEADING_ESCAPED:
            parts.extend(("\\", character))
        elif index == ordered_delimiter:
            parts.extend(("\\", character))
        else:
            parts.append(character)
    parts.extend([_SPACE_REFERENCE] * (len(line) - body_end))
    return "".join(parts)


def escape_generated(value: str, *, continuation_indent: str = "") -> str:
    """Escape one nonliteral generated value under §17.

    The two spaces before each structural LF are the renderer-owned hard-break
    spelling. ``continuation_indent`` is supplied by the current block.
    """

    logical_lines = normalize_generated_text(value).split("\n")
    rendered = [_escape_logical_line(line) for line in logical_lines]
    return ("  \n" + continuation_indent).join(rendered)


def source_voice_fence(excerpt: bytes) -> bytes:
    """Fence a validated source excerpt while preserving its interior bytes.

    Boundary LFs belong to the structural fence. The bytes between those
    boundaries are copied without newline or Unicode normalization.
    """

    longest = max((len(match.group(0)) for match in _BACKTICK_RUN.finditer(excerpt)), default=0)
    fence = b"`" * max(3, longest + 1)
    boundary = b"" if excerpt.endswith((b"\n", b"\r")) else b"\n"
    return fence + b"\n" + excerpt + boundary + fence + b"\n"
