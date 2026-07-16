#!/usr/bin/env python3
"""Check that §21 eval headings, authored cases, and coverage rows stay synced."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import sys
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # exercised by the supported Python 3.10 environment
    import tomli as tomllib  # type: ignore[no-redef]


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPOSITORY_ROOT / "spec" / "21-evals.md"
CASES_PATH = REPOSITORY_ROOT / "spec" / "21-evals-cases.toml"
MAP_PATH = REPOSITORY_ROOT / "tests" / "coverage_map.toml"
HEADING_RE = re.compile(r"^## §21\.([1-9]\d*) (.+)$")
ENFORCES_RE = re.compile(r"^Enforces (.+)\.$")
KEY_RE = re.compile(r"^21\.[1-9]\d*$")


def requirement_sort_key(key: str) -> tuple[int, str]:
    match = KEY_RE.fullmatch(key)
    return (int(key.split(".", 1)[1]), key) if match else (sys.maxsize, key)


def read_spec() -> tuple[list[tuple[str, str, str | None]], list[str]]:
    try:
        lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [], [f"cannot read {SPEC_PATH.relative_to(REPOSITORY_ROOT)}: {exc}"]

    errors = [
        f"{SPEC_PATH.relative_to(REPOSITORY_ROOT)}:{number}: case bodies live in "
        "spec/21-evals-cases.toml; fenced content is forbidden"
        for number, line in enumerate(lines, start=1)
        if line.startswith("```")
    ]
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.fullmatch(line)
        if match is None:
            continue
        case_id = f"21.{match.group(1)}"
        title = match.group(2)
        headings.append((index, case_id, title))
        if title != title.strip():
            errors.append(f"{case_id}: heading title has leading or trailing whitespace")

    duplicates = sorted(
        (key for key, count in Counter(key for _index, key, _title in headings).items() if count > 1),
        key=requirement_sort_key,
    )
    errors.extend(f"duplicate §21 heading: {key}" for key in duplicates)

    parsed: list[tuple[str, str, str | None]] = []
    for position, (line_index, case_id, title) in enumerate(headings):
        limit = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        cursor = line_index + 1
        while cursor < limit and not lines[cursor]:
            cursor += 1
        enforces = None
        if cursor < limit and (match := ENFORCES_RE.fullmatch(lines[cursor])):
            enforces = match.group(1)
        parsed.append((case_id, title, enforces))
    return parsed, errors


def read_toml(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return {}, [f"cannot parse {path.relative_to(REPOSITORY_ROOT)}: {exc}"]
    if not isinstance(parsed, dict):
        return {}, [f"{path.relative_to(REPOSITORY_ROOT)} must contain TOML tables"]
    return parsed, []


def validate_cases(cases: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for case_id, entry in cases.items():
        if not KEY_RE.fullmatch(case_id):
            errors.append(f"malformed eval-case key: {case_id}")
        if not isinstance(entry, dict):
            errors.append(f"{case_id}: entry must be a TOML table")
            continue
        actual_keys = set(entry)
        required_keys = {"title", "case"}
        allowed_keys = required_keys | {"enforces"}
        missing = sorted(required_keys - actual_keys)
        unexpected = sorted(actual_keys - allowed_keys)
        if missing:
            errors.append(f"{case_id}: missing keys: {', '.join(missing)}")
        if unexpected:
            errors.append(f"{case_id}: unexpected keys: {', '.join(unexpected)}")
        for key, value in entry.items():
            if key in allowed_keys and not isinstance(value, str):
                errors.append(f"{case_id}: {key} must be a string")
        case = entry.get("case")
        if isinstance(case, str) and not case.strip():
            errors.append(f"{case_id}: case must be non-empty after stripping whitespace")
    return errors


def validate_sync(
    spec_entries: list[tuple[str, str, str | None]],
    cases: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    spec_ids = [case_id for case_id, _title, _enforces in spec_entries]
    case_ids = list(cases)
    if spec_ids != case_ids:
        errors.append(
            "§21 Markdown ID sequence does not match eval-case TOML ID sequence: "
            f"Markdown={spec_ids!r}, TOML={case_ids!r}"
        )
    for case_id, title, md_enforces in spec_entries:
        entry = cases.get(case_id)
        if not isinstance(entry, dict):
            continue
        if entry.get("title") != title:
            errors.append(f"{case_id}: TOML title does not match Markdown heading byte-for-byte")
        has_md_enforces = md_enforces is not None
        has_toml_enforces = "enforces" in entry
        if has_md_enforces != has_toml_enforces:
            errors.append(f"{case_id}: Enforces presence differs between Markdown and TOML")
        elif has_md_enforces and entry.get("enforces") != md_enforces:
            errors.append(f"{case_id}: enforces value differs between Markdown and TOML byte-for-byte")
    return errors


def validate_coverage(spec_ids: set[str], coverage_map: dict[str, Any]) -> list[str]:
    map_ids = set(coverage_map)
    errors = [
        f"missing coverage-map entry: {key}"
        for key in sorted(spec_ids - map_ids, key=requirement_sort_key)
    ]
    errors.extend(
        f"orphaned coverage-map entry: {key}"
        for key in sorted(map_ids - spec_ids, key=requirement_sort_key)
    )
    return errors


def main() -> int:
    spec_entries, errors = read_spec()
    cases, case_errors = read_toml(CASES_PATH)
    errors.extend(case_errors)
    if not case_errors:
        errors.extend(validate_cases(cases))
        errors.extend(validate_sync(spec_entries, cases))

    coverage_map, map_errors = read_toml(MAP_PATH)
    errors.extend(map_errors)
    if not map_errors:
        errors.extend(
            validate_coverage(
                {case_id for case_id, _title, _enforces in spec_entries},
                coverage_map,
            )
        )

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(spec_entries)} §21 eval cases synced across "
        "spec/21-evals.md, spec/21-evals-cases.toml, and tests/coverage_map.toml"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
