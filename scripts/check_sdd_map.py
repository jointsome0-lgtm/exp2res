#!/usr/bin/env python3
"""Check that the SDD.md § Index stays a router: budgeted lines, one per § file."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SDD_PATH = REPOSITORY_ROOT / "SDD.md"
SPEC_DIRECTORY = REPOSITORY_ROOT / "spec"
LINE_BUDGET = 250
INDEX_HEADING = "## § Index"
INDEX_HEADING_VARIANT_RE = re.compile(
    r" {0,3}#{1,6}[ \t]+§\s*Index\s*#*\s*"
)
SETEXT_TITLE_RE = re.compile(r" {0,3}§\s*Index[ \t]*")
SETEXT_UNDERLINE_RE = re.compile(r" {0,3}[-=]+[ \t]*")
INDEX_BOUNDARY_RE = re.compile(r"^ {0,3}#{1,2}(?:[ \t]+|$)")
ORDERED_ITEM_RE = re.compile(r"[0-9]+[.)]\s")
BULLET_ITEM_RE = re.compile(r"^ {0,3}[-+*][ \t]+")
ORDERED_ITEM_WITH_INDENT_RE = re.compile(r"^ {0,3}[0-9]+[.)][ \t]+")
BULLET_RE = re.compile(r"^- \S")
SECTION_BULLET_RE = re.compile(r"^- §(0|[1-9][0-9]*) ")
DECISION_LOG_BULLET_RE = re.compile(r"^- Decision Log — \S")
SPEC_FILE_RE = re.compile(r"^([0-9]+)-.+\.md$")
OPENING_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SECTION_HEADING_RE = re.compile(r"^ {0,3}## §(0|[1-9][0-9]*)\. \S")
CANONICAL_SECTION_HEADING_RE = re.compile(r"^## §(0|[1-9][0-9]*)\. \S")
SETEXT_SECTION_TITLE_RE = re.compile(r"^ {0,3}§(0|[1-9][0-9]*)\. \S")


def opening_fence(line: str) -> tuple[str, int] | None:
    """Return a CommonMark fence's character and length, if line opens one."""
    match = OPENING_FENCE_RE.fullmatch(line)
    if not match:
        return None
    marker, info_string = match.groups()
    if marker[0] == "`" and "`" in info_string:
        return None
    return marker[0], len(marker)


def is_closing_fence(line: str, fence: tuple[str, int]) -> bool:
    """True when line is a compatible CommonMark close for fence."""
    character, length = fence
    return bool(
        re.fullmatch(
            rf" {{0,3}}{re.escape(character)}{{{length},}}[ \t]*",
            line,
        )
    )


def is_indented_code(line: str) -> bool:
    """True for indentation that cannot start a top-level Markdown block."""
    return line.startswith("    ") or line.startswith("\t")


def is_list_item(line: str) -> bool:
    """True when line begins a top-level Markdown list item."""
    return bool(
        BULLET_ITEM_RE.match(line) or ORDERED_ITEM_WITH_INDENT_RE.match(line)
    )


def without_html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Blank HTML comment spans while preserving visible text positions."""
    if not in_comment and is_indented_code(line):
        return line, False

    visible: list[str] = []
    cursor = 0
    while cursor < len(line):
        if in_comment:
            end = line.find("-->", cursor)
            if end < 0:
                visible.append(" " * (len(line) - cursor))
                break
            visible.append(" " * (end + 3 - cursor))
            cursor = end + 3
            in_comment = False
            continue

        start = line.find("<!--", cursor)
        if start < 0:
            visible.append(line[cursor:])
            break
        if line[cursor:start].strip():
            # A block comment begins only when the marker is the first
            # non-whitespace content, not inside inline code or an escape.
            visible.append(line[cursor:])
            break
        visible.append(line[cursor:start])
        visible.append(" " * 4)
        cursor = start + 4
        in_comment = True
    return "".join(visible), in_comment


def visible_markdown_lines(lines: list[str]) -> list[str | None]:
    """Hide fenced-code and HTML-comment lines from structural checks."""
    visible: list[str | None] = []
    fence: tuple[str, int] | None = None
    in_comment = False
    for raw_line in lines:
        if fence is not None:
            if is_closing_fence(raw_line, fence):
                fence = None
            visible.append(None)
            continue

        line, in_comment = without_html_comments(raw_line, in_comment)
        fence = opening_fence(line)
        if fence is not None:
            visible.append(None)
        else:
            visible.append(line)
    return visible


def is_setext_boundary(lines: list[str | None], index: int) -> bool:
    """True when index starts a visible, non-code Setext H1 or H2."""
    if index + 1 >= len(lines):
        return False
    title = lines[index]
    underline = lines[index + 1]
    return bool(
        title is not None
        and title.strip()
        and not is_indented_code(title)
        and not is_list_item(title)
        and underline is not None
        and SETEXT_UNDERLINE_RE.fullmatch(underline)
    )


def read_index_bullets() -> tuple[list[str], list[str]]:
    try:
        lines = SDD_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [], [f"cannot read {SDD_PATH.relative_to(REPOSITORY_ROOT)}: {exc}"]

    visible_lines = visible_markdown_lines(lines)
    headings = [
        index
        for index, line in enumerate(visible_lines)
        if line is not None
        and (
            INDEX_HEADING_VARIANT_RE.fullmatch(line)
            or (
                SETEXT_TITLE_RE.fullmatch(line)
                and index + 1 < len(visible_lines)
                and visible_lines[index + 1] is not None
                and SETEXT_UNDERLINE_RE.fullmatch(visible_lines[index + 1])
            )
        )
    ]
    if len(headings) != 1 or lines[headings[0]] != INDEX_HEADING:
        return [], [
            f"SDD.md must contain exactly one canonical {INDEX_HEADING!r} heading "
            f"and no variant spellings, found {len(headings)} candidate(s)"
        ]
    start = headings[0] + 1

    bullets: list[str] = []
    errors: list[str] = []
    for index, visible_line in enumerate(visible_lines[start:], start=start):
        line = visible_line or ""
        if (
            line == "---"
            or INDEX_BOUNDARY_RE.match(line)
            or is_setext_boundary(visible_lines, index)
        ):
            break
        if BULLET_RE.match(line):
            bullets.append(line)
        elif not is_indented_code(line) and (
            line.strip()[:1] in {"-", "*", "+"}
            or ORDERED_ITEM_RE.match(line.strip())
        ):
            errors.append(f"malformed § Index bullet: {line.strip()[:60]}…")
        elif not bullets or not line.strip():
            continue
        elif line[0].isspace():
            # A wrapped bullet is still one router: continuation text counts
            # toward its budget.
            bullets[-1] += " " + line.strip()
        else:
            errors.append(
                f"unexpected non-bullet text inside the § Index list: {line[:60]}…"
            )
    if not bullets:
        return [], [*errors, "§ Index contains no bullets"]
    return bullets, errors


def read_spec_numbers() -> tuple[set[int], list[str]]:
    try:
        entries = sorted(SPEC_DIRECTORY.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        return set(), [f"cannot list {SPEC_DIRECTORY.relative_to(REPOSITORY_ROOT)}: {exc}"]

    numbers: list[int] = []
    errors: list[str] = []
    for entry in entries:
        name = entry.name
        is_numbered_markdown = name[:1].isdigit() and name.lower().endswith(".md")
        if is_numbered_markdown and (entry.is_symlink() or not entry.is_file()):
            errors.append(f"numbered spec path is not a regular file: spec/{name}")
            continue
        if not entry.is_file():
            continue
        if match := SPEC_FILE_RE.fullmatch(name):
            number = int(match.group(1))
            if match.group(1) != f"{number:02d}":
                errors.append(f"malformed numbered spec filename: spec/{name}")
                continue
            numbers.append(number)
            try:
                body = (SPEC_DIRECTORY / name).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(f"cannot read spec/{name}: {exc}")
                continue
            if not has_section_heading(body, number):
                errors.append(
                    f"spec/{name} must contain exactly one rendered top-level "
                    f"'## §{number}. <title>' heading and no foreign root § heading"
                )
        elif is_numbered_markdown:
            errors.append(f"malformed numbered spec filename: spec/{name}")
    errors += [
        f"spec/ has multiple files for §{number}"
        for number, count in sorted(Counter(numbers).items())
        if count > 1
    ]
    return set(numbers), errors


def has_section_heading(body: str, number: int) -> bool:
    """True when exactly one rendered root § heading matches the filename."""
    visible_lines = visible_markdown_lines(body.splitlines())
    headings: list[int] = []
    has_canonical_heading = False
    for index, line in enumerate(visible_lines):
        if line is None:
            continue
        if match := SECTION_HEADING_RE.match(line):
            heading_number = int(match.group(1))
            headings.append(heading_number)
            canonical_match = CANONICAL_SECTION_HEADING_RE.match(line)
            if canonical_match and heading_number == number:
                has_canonical_heading = True
        elif (
            (match := SETEXT_SECTION_TITLE_RE.match(line))
            and is_setext_boundary(visible_lines, index)
        ):
            headings.append(int(match.group(1)))
    return has_canonical_heading and headings == [number]


def main() -> int:
    bullets, errors = read_index_bullets()
    spec_numbers, spec_errors = read_spec_numbers()
    errors.extend(spec_errors)

    index_numbers: list[int] = []
    decision_log_bullets = 0
    for bullet in bullets:
        if len(bullet) > LINE_BUDGET:
            errors.append(
                f"index line exceeds {LINE_BUDGET} characters "
                f"({len(bullet)}): {bullet[:60]}…"
            )
        if match := SECTION_BULLET_RE.match(bullet):
            index_numbers.append(int(match.group(1)))
            if not bullet[match.end():].strip():
                errors.append(f"index line has no routing text: {bullet.rstrip()}")
        elif bullet.startswith("- §"):
            errors.append(f"malformed § index anchor (canonical form is §N): {bullet[:60]}…")
        elif DECISION_LOG_BULLET_RE.match(bullet):
            decision_log_bullets += 1
        else:
            errors.append(f"unowned § Index bullet: {bullet[:60]}…")
    if bullets and decision_log_bullets != 1:
        errors.append(
            f"§ Index must contain exactly one Decision Log bullet, found {decision_log_bullets}"
        )

    errors.extend(
        f"duplicate § index line: §{number}"
        for number, count in sorted(Counter(index_numbers).items())
        if count > 1
    )
    if index_numbers != sorted(index_numbers):
        errors.append("§ index lines are not in ascending numeric order")
    if not errors:
        errors.extend(
            f"§{number} has an index line but no spec/{number:02d}-*.md file"
            for number in sorted(set(index_numbers) - spec_numbers)
        )
        errors.extend(
            f"spec/{number:02d}-*.md has no § index line"
            for number in sorted(spec_numbers - set(index_numbers))
        )

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(bullets)} index bullets within {LINE_BUDGET} characters; "
        f"{len(index_numbers)} § lines match spec/ files one-to-one"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
