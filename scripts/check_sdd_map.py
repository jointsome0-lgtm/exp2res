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
BULLET_RE = re.compile(r"^- \S")
SECTION_BULLET_RE = re.compile(r"^- §(0|[1-9]\d*) ")
SPEC_FILE_RE = re.compile(r"^(\d+)-.+\.md$")


def read_index_bullets() -> tuple[list[str], list[str]]:
    try:
        lines = SDD_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [], [f"cannot read {SDD_PATH.relative_to(REPOSITORY_ROOT)}: {exc}"]

    try:
        start = lines.index(INDEX_HEADING) + 1
    except ValueError:
        return [], [f"SDD.md has no {INDEX_HEADING!r} heading"]

    bullets: list[str] = []
    errors: list[str] = []
    for line in lines[start:]:
        if line == "---" or line.startswith("## "):
            break
        if BULLET_RE.match(line):
            bullets.append(line)
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
        names = sorted(
            entry.name for entry in SPEC_DIRECTORY.iterdir() if entry.is_file()
        )
    except OSError as exc:
        return set(), [f"cannot list {SPEC_DIRECTORY.relative_to(REPOSITORY_ROOT)}: {exc}"]

    numbers: list[int] = []
    errors: list[str] = []
    for name in names:
        if match := SPEC_FILE_RE.fullmatch(name):
            numbers.append(int(match.group(1)))
        elif name.endswith(".md") and name[0].isdigit():
            errors.append(f"malformed numbered spec filename: spec/{name}")
    errors += [
        f"spec/ has multiple files for §{number}"
        for number, count in sorted(Counter(numbers).items())
        if count > 1
    ]
    return set(numbers), errors


def main() -> int:
    bullets, errors = read_index_bullets()
    spec_numbers, spec_errors = read_spec_numbers()
    errors.extend(spec_errors)

    index_numbers: list[int] = []
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

    errors.extend(
        f"duplicate § index line: §{number}"
        for number, count in sorted(Counter(index_numbers).items())
        if count > 1
    )
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
