"""Shared deterministic §17 generated-voice Markdown machinery."""

from __future__ import annotations

import re
import unicodedata


_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_BACKTICK_RUN = re.compile(br"`+")


def normalize_generated_text(value: str) -> str:
    """Apply the §13.12 generated-text projection without Markdown escaping."""

    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def escape_generated(value: str, *, continuation_indent: str = "") -> str:
    """Escape one nonliteral generated value under §17.

    The two spaces before each structural LF are the renderer-owned hard-break
    spelling. ``continuation_indent`` is supplied by the current block.
    """

    logical_lines = normalize_generated_text(value).split("\n")
    rendered: list[str] = []
    for line in logical_lines:
        parts: list[str] = []
        for character in line:
            if character == "\t":
                parts.append("&#9;")
            elif character in _PUNCTUATION:
                parts.extend(("\\", character))
            else:
                parts.append(character)
        rendered.append("".join(parts))
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
