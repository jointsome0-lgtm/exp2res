#!/usr/bin/env python3
"""Lint the compact, vendorable selfos Decision Log format.

Entries on or before ``--baseline`` keep parsing-critical checks but are exempt
from word, reference, stale-waiver, and missing-rejected-clause checks.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import sys
from typing import Iterable, Optional, Sequence


CHECKER_VERSION = "1.1.0"

ENTRY_RE = re.compile(r"^- (?P<date>\d{4}-\d{2}-\d{2}) — (?P<text>.*)$")
ATX_HEADING_RE = re.compile(
    r"^[ ]{0,3}(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*?))?[ \t]*$"
)
ATX_CLOSING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")
FENCE_RE = re.compile(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})(?P<rest>.*)$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
MARKDOWN_LINK_START_RE = re.compile(r"(?P<label>\[[^\]\n]*\])\(")
WAIVER_COMMENT_RE = re.compile(
    r"<!-- decision-log: allow-long(?P<body>.*?)-->"
)
WAIVER_MARKER_RE = re.compile(r"<!-- decision-log: allow-long")
REJECTED_CANONICAL_RE = re.compile(
    r"\b(?:Rejected:|Rejected alternative:|Rejected alternatives:)\s+"
    r"(?P<alternative>\S(?:.*?\S)?)\s+—\s+"
    r"(?P<reason>\S(?:.*?\S)?)\."
    r"(?=\s|$)"
)
REJECTED_BECAUSE_RE = re.compile(
    r"\bRejected\s+"
    r"(?P<alternative>\S(?:.*?\S)?)\s+because\s+"
    r"(?P<reason>\S(?:.*?\S)?)\."
    r"(?=\s|$)"
)
REFERENCE_RE = re.compile(
    r"(?<![\w#])(?:PR\s+#\d+|#\d+|GH-\d+)(?!\w)"
    r"|(?<!\w)[0-9A-Fa-f]{7,40}(?!\w)",
    re.IGNORECASE,
)
SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")
DATE_TEXT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    filename: str
    line: int
    message: str

    def sort_key(self) -> tuple[str, int, int, str]:
        severity_order = 0 if self.severity == "ERROR" else 1
        return (self.filename, self.line, severity_order, self.message)


@dataclass(frozen=True)
class EntryPart:
    line: int
    text: str
    first: bool


@dataclass(frozen=True)
class Entry:
    line: int
    date_text: str
    parts: tuple[EntryPart, ...]


@dataclass(frozen=True)
class WaiverResult:
    valid: bool
    line: Optional[int]
    diagnostics: tuple[tuple[int, str], ...]


def parse_iso_date(value: str) -> Optional[date]:
    """Return an exact YYYY-MM-DD date, or None when invalid."""
    if not DATE_TEXT_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def baseline_argument(value: str) -> date:
    parsed = parse_iso_date(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("must be a calendar-valid YYYY-MM-DD date")
    return parsed


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def heading_at(line: str) -> Optional[tuple[int, str]]:
    match = ATX_HEADING_RE.fullmatch(line)
    if match is None:
        return None
    title = ATX_CLOSING_HASHES_RE.sub("", match.group("title") or "").strip()
    return (len(match.group("marks")), title)


def markdown_headings(lines: Sequence[str]) -> list[tuple[int, int, str]]:
    """Return zero-based (line, level, title) ATX headings outside fences."""
    headings: list[tuple[int, int, str]] = []
    fence_character: Optional[str] = None
    fence_length = 0

    for index, line in enumerate(lines):
        fence_match = FENCE_RE.fullmatch(line)
        if fence_character is None:
            if fence_match is not None:
                fence = fence_match.group("fence")
                rest = fence_match.group("rest")
                if not (fence[0] == "`" and "`" in rest):
                    fence_character = fence[0]
                    fence_length = len(fence)
                    continue
        else:
            if fence_match is not None:
                fence = fence_match.group("fence")
                rest = fence_match.group("rest")
                if (
                    fence[0] == fence_character
                    and len(fence) >= fence_length
                    and not rest.strip()
                ):
                    fence_character = None
                    fence_length = 0
            continue

        heading = heading_at(line)
        if heading is not None:
            headings.append((index, heading[0], heading[1]))

    return headings


def merge_regions(regions: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(regions):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def decision_log_regions(lines: Sequence[str]) -> list[tuple[int, int]]:
    headings = markdown_headings(lines)
    targets = [heading for heading in headings if heading[2].casefold() == "decision log"]
    if not targets:
        return [(0, len(lines))]

    regions: list[tuple[int, int]] = []
    for target_line, target_level, _title in targets:
        end = len(lines)
        for heading_line, heading_level, _heading_title in headings:
            if heading_line > target_line and heading_level <= target_level:
                end = heading_line
                break
        regions.append((target_line + 1, end))
    return merge_regions(regions)


def make_diagnostic(
    severity: str, filename: str, line: int, message: str
) -> Diagnostic:
    return Diagnostic(severity=severity, filename=filename, line=line, message=message)


def parse_region(
    lines: Sequence[str], start: int, end: int, filename: str
) -> tuple[list[Entry], list[Diagnostic]]:
    entries: list[Entry] = []
    diagnostics: list[Diagnostic] = []
    current_line: Optional[int] = None
    current_date = ""
    current_parts: list[EntryPart] = []

    def finish_entry() -> None:
        nonlocal current_line, current_date, current_parts
        if current_line is not None:
            entries.append(
                Entry(current_line, current_date, tuple(current_parts))
            )
        current_line = None
        current_date = ""
        current_parts = []

    for index in range(start, end):
        raw_line = lines[index]
        line_number = index + 1
        if not raw_line.strip():
            continue

        entry_match = ENTRY_RE.fullmatch(raw_line)
        if entry_match is not None:
            finish_entry()
            current_line = line_number
            current_date = entry_match.group("date")
            current_parts = [
                EntryPart(line_number, entry_match.group("text"), True)
            ]
            continue

        exactly_two_spaces = (
            raw_line.startswith("  ")
            and len(raw_line) > 2
            and not raw_line[2].isspace()
        )
        indented_entry = exactly_two_spaces and raw_line[2:].startswith("- ")
        markdown_heading = heading_at(raw_line) is not None
        if (
            exactly_two_spaces
            and current_line is not None
            and not indented_entry
            and not markdown_heading
        ):
            current_parts.append(EntryPart(line_number, raw_line[2:], False))
            continue

        finish_entry()
        if raw_line.startswith("-"):
            message = "entry does not begin with '- YYYY-MM-DD — '"
        else:
            message = "malformed continuation indentation or multi-entry ambiguity"
        diagnostics.append(make_diagnostic("ERROR", filename, line_number, message))

    finish_entry()
    return entries, diagnostics


def entry_source_text(entry: Entry) -> str:
    return " ".join(part.text for part in entry.parts)


def strip_markdown_link_targets(text: str) -> str:
    """Remove balanced inline-link targets while preserving their labels."""
    pieces: list[str] = []
    cursor = 0
    while True:
        match = MARKDOWN_LINK_START_RE.search(text, cursor)
        if match is None:
            pieces.append(text[cursor:])
            break

        pieces.append(text[cursor : match.start()])
        pieces.append(match.group("label"))
        index = match.end()
        depth = 1
        while index < len(text):
            character = text[index]
            if character == "\\" and index + 1 < len(text):
                index += 2
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    cursor = index + 1
                    break
            index += 1
        else:
            pieces.append(text[match.end() - 1 :])
            break

    return "".join(pieces)


def comment_stripped_entry_text(entry: Entry) -> str:
    text = entry_source_text(entry)
    text = HTML_COMMENT_RE.sub(" ", text)
    return " ".join(text.split())


def visible_entry_text(entry: Entry) -> str:
    text = comment_stripped_entry_text(entry)
    text = strip_markdown_link_targets(text)
    return " ".join(text.split())


def visible_first_line_text(entry: Entry) -> str:
    """The dated line's own TEXT — the grammar requires it there, not merely somewhere in the entry."""
    text = HTML_COMMENT_RE.sub(" ", entry.parts[0].text)
    text = strip_markdown_link_targets(text)
    return " ".join(text.split())


def word_count(entry: Entry) -> int:
    return len(visible_entry_text(entry).split())


def find_rejected_clause(text: str) -> Optional[re.Match[str]]:
    matches = [
        match
        for match in (
            REJECTED_CANONICAL_RE.search(text),
            REJECTED_BECAUSE_RE.search(text),
        )
        if match is not None
    ]
    return min(matches, key=lambda match: match.start()) if matches else None


def waiver_result(entry: Entry) -> WaiverResult:
    valid_lines: list[int] = []
    diagnostics: list[tuple[int, str]] = []

    for part in entry.parts:
        html_comment_spans = {
            (match.start(), match.end()) for match in HTML_COMMENT_RE.finditer(part.text)
        }
        comments = list(WAIVER_COMMENT_RE.finditer(part.text))
        marker_starts = [match.start() for match in WAIVER_MARKER_RE.finditer(part.text)]
        covered_starts = {match.start() for match in comments}
        for marker_start in marker_starts:
            if marker_start not in covered_starts:
                diagnostics.append(
                    (part.line, "allow-long waiver must use the exact HTML comment syntax")
                )

        for match in comments:
            if (match.start(), match.end()) not in html_comment_spans:
                diagnostics.append(
                    (part.line, "allow-long waiver must use the exact HTML comment syntax")
                )
                continue
            body = match.group("body")
            reason = body[3:-1] if body.startswith(" — ") and body.endswith(" ") else ""
            if not reason.strip():
                diagnostics.append(
                    (part.line, "allow-long waiver requires a non-empty reason")
                )
                continue
            if reason != reason.strip():
                diagnostics.append(
                    (part.line, "allow-long waiver must use the exact HTML comment syntax")
                )
                continue

            if part.first:
                separated = match.start() == 0 or part.text[match.start() - 1] == " "
                placed_correctly = separated and not part.text[match.end() :].strip()
            else:
                placed_correctly = part.text.strip() == match.group(0)
            if not placed_correctly:
                diagnostics.append(
                    (
                        part.line,
                        "allow-long waiver must be at the end of the first line or on its own continuation line",
                    )
                )
                continue
            valid_lines.append(part.line)

    if len(valid_lines) > 1:
        for duplicate_line in valid_lines[1:]:
            diagnostics.append(
                (duplicate_line, "entry must not contain multiple allow-long waivers")
            )

    return WaiverResult(
        valid=bool(valid_lines),
        line=valid_lines[0] if valid_lines else None,
        diagnostics=tuple(diagnostics),
    )


def validate_entry(
    entry: Entry,
    filename: str,
    baseline: Optional[date],
    max_words: int,
    warn_words: int,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    parsed_date = parse_iso_date(entry.date_text)
    if parsed_date is None:
        diagnostics.append(
            make_diagnostic(
                "ERROR", filename, entry.line, f"calendar-invalid date '{entry.date_text}'"
            )
        )

    is_historical = (
        baseline is not None and parsed_date is not None and parsed_date <= baseline
    )

    text = visible_entry_text(entry)
    if not visible_first_line_text(entry):
        diagnostics.append(
            make_diagnostic(
                "ERROR", filename, entry.line, "empty decision text on the dated entry line"
            )
        )

    rejected_clause = find_rejected_clause(text)
    if rejected_clause is None and not is_historical:
        diagnostics.append(
            make_diagnostic(
                "ERROR",
                filename,
                entry.line,
                "missing explicit rejected-alternative-with-reason clause",
            )
        )

    waiver = waiver_result(entry)
    diagnostics.extend(
        make_diagnostic("ERROR", filename, line, message)
        for line, message in waiver.diagnostics
    )

    count = word_count(entry)
    if not is_historical:
        if count > max_words and not waiver.valid:
            diagnostics.append(
                make_diagnostic(
                    "ERROR",
                    filename,
                    entry.line,
                    f"entry has {count} words, above the {max_words}-word ceiling, without an allow-long waiver",
                )
            )
        if count > warn_words:
            diagnostics.append(
                make_diagnostic(
                    "WARNING",
                    filename,
                    entry.line,
                    f"entry has {count} words, above the {warn_words}-word warning threshold; target is about 40 words",
                )
            )
        if waiver.valid and count <= warn_words:
            diagnostics.append(
                make_diagnostic(
                    "WARNING",
                    filename,
                    waiver.line or entry.line,
                    f"stale allow-long waiver: entry has {count} words at or below the {warn_words}-word warning threshold",
                )
            )
        if REFERENCE_RE.search(comment_stripped_entry_text(entry)) is None:
            diagnostics.append(
                make_diagnostic(
                    "WARNING",
                    filename,
                    entry.line,
                    "no issue/PR/SHA reference; detailed argument belongs in the issue, the commit body, or the SDD § edit, not in the log",
                )
            )

    if rejected_clause is not None:
        trailing_text = text[rejected_clause.end() :]
        if len(SENTENCE_END_RE.findall(trailing_text)) > 2:
            diagnostics.append(
                make_diagnostic(
                    "WARNING",
                    filename,
                    entry.line,
                    "paragraph-style duplication heuristic: more than two sentences follow the rejected-alternative clause",
                )
            )

    return diagnostics


def lint_text(
    text: str,
    filename: str,
    baseline: Optional[date],
    max_words: int,
    warn_words: int,
) -> list[Diagnostic]:
    lines = text.splitlines()
    diagnostics: list[Diagnostic] = []
    for start, end in decision_log_regions(lines):
        entries, parse_diagnostics = parse_region(lines, start, end, filename)
        diagnostics.extend(parse_diagnostics)
        for entry in entries:
            diagnostics.extend(
                validate_entry(entry, filename, baseline, max_words, warn_words)
            )
    return diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--baseline",
        type=baseline_argument,
        metavar="YYYY-MM-DD",
        help=(
            "exempt entries on or before this date from missing-rejected-clause, "
            "threshold, stale-waiver, and reference checks"
        ),
    )
    parser.add_argument(
        "--max-words",
        type=positive_integer,
        default=140,
        metavar="N",
        help="hard word ceiling (default: 140)",
    )
    parser.add_argument(
        "--warn-words",
        type=positive_integer,
        default=80,
        metavar="N",
        help="word warning threshold (default: 80)",
    )
    parser.add_argument("files", metavar="FILE", nargs="+")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.warn_words >= args.max_words:
        parser.error("--warn-words must be lower than --max-words")

    diagnostics: list[Diagnostic] = []
    for filename in sorted(args.files):
        try:
            text = Path(filename).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            parser.error(f"cannot read {filename!r}: {exc}")
        diagnostics.extend(
            lint_text(text, filename, args.baseline, args.max_words, args.warn_words)
        )

    ordered = sorted(diagnostics, key=Diagnostic.sort_key)
    for diagnostic in ordered:
        print(
            f"{diagnostic.severity}: {diagnostic.filename}:{diagnostic.line}: {diagnostic.message}",
            file=sys.stderr,
        )
    return 1 if any(item.severity == "ERROR" for item in ordered) else 0


if __name__ == "__main__":
    raise SystemExit(main())
