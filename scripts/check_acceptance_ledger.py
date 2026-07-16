#!/usr/bin/env python3
"""Check that every §24 criterion has honest, executable evidence metadata."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # exercised by the supported Python 3.10 environment
    import tomli as tomllib  # type: ignore[no-redef]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_coverage_map import execute_evidence_nodes  # noqa: E402


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPOSITORY_ROOT / "spec" / "24-acceptance-criteria.md"
LEDGER_PATH = REPOSITORY_ROOT / "tests" / "acceptance_ledger.toml"
COVERAGE_MAP_PATH = REPOSITORY_ROOT / "tests" / "coverage_map.toml"
ITEM_RE = re.compile(r"^([1-9]\d*)\. ")
KEY_RE = re.compile(r"^24\.[1-9]\d*$")
EVAL_RE = re.compile(r"^21\.[1-9]\d*$")
TABLE_RE = re.compile(r'^\s*\["([^"]+)"\]\s*(?:#.*)?$')
STATUSES = frozenset({"met", "partial", "pending"})
PHASES = frozenset({"0", "1", "2", "3", "4", "5"})
FIELDS = frozenset({"status", "phases", "evals", "nodes", "issues", "note"})


def read_criteria() -> tuple[set[str], list[str]]:
    try:
        lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return set(), [f"cannot read {SPEC_PATH.relative_to(REPOSITORY_ROOT)}: {exc}"]

    keys = [f"24.{match.group(1)}" for line in lines if (match := ITEM_RE.match(line))]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    return set(keys), [f"duplicate §24 criterion: {key}" for key in duplicates]


def read_toml(path: Path, label: str) -> tuple[dict[str, Any], list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {}, [f"cannot read {path.relative_to(REPOSITORY_ROOT)}: {exc}"]

    errors: list[str] = []
    if path == LEDGER_PATH:
        table_keys = [
            match.group(1)
            for line in text.splitlines()
            if (match := TABLE_RE.fullmatch(line))
        ]
        errors.extend(
            f"duplicate acceptance-ledger key: {key}"
            for key, count in Counter(table_keys).items()
            if count > 1
        )
    try:
        parsed = tomllib.loads(text)
    except (ValueError, TypeError) as exc:
        return {}, [*errors, f"cannot parse {label}: {exc}"]
    if not isinstance(parsed, dict):
        return {}, [*errors, f"{label} must contain TOML tables"]
    return parsed, errors


def collect_test_nodes(*, include_all_markers: bool) -> tuple[set[str], list[str]]:
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    if include_all_markers:
        command.extend(["-m", ""])
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail_lines = [
            line.strip()
            for line in (result.stderr + result.stdout).splitlines()
            if line.strip()
        ]
        detail = detail_lines[-1] if detail_lines else "no pytest diagnostic"
        return set(), [f"pytest collection failed (exit {result.returncode}): {detail}"]
    nodes = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }
    return nodes, []


def string_list(entry: dict[str, Any], field: str) -> tuple[list[str], list[str]]:
    value = entry.get(field)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        return [], [f"{field} must be a list of non-empty strings"]
    if len(value) != len(set(value)):
        return value, [f"{field} contains duplicates"]
    return value, []


def validate_entry(
    key: str,
    entry: Any,
    coverage_map: dict[str, Any],
    collected: set[str],
    ci_collected: set[str],
) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{key}: entry must be a TOML table"]

    errors = [f"{key}: unknown field: {field}" for field in sorted(set(entry) - FIELDS)]
    status = entry.get("status")
    if status not in STATUSES:
        errors.append(f"{key}: status must be met, partial, or pending")

    phases, field_errors = string_list(entry, "phases")
    errors.extend(f"{key}: {error}" for error in field_errors)
    if not field_errors:
        if not phases:
            errors.append(f"{key}: phases must be non-empty")
        for phase in phases:
            if phase not in PHASES:
                errors.append(f"{key}: phase must be a string from 0 through 5: {phase}")

    evals, field_errors = string_list(entry, "evals")
    errors.extend(f"{key}: {error}" for error in field_errors)
    valid_evals: list[str] = []
    if not field_errors:
        for eval_key in evals:
            if not EVAL_RE.fullmatch(eval_key):
                errors.append(f"{key}: malformed eval key: {eval_key}")
            elif eval_key not in coverage_map:
                errors.append(f"{key}: eval does not exist in coverage map: {eval_key}")
            elif not isinstance(coverage_map[eval_key], dict):
                errors.append(f"{key}: coverage-map eval is not a TOML table: {eval_key}")
            else:
                valid_evals.append(eval_key)

    nodes: list[str] = []
    if "nodes" in entry:
        nodes, field_errors = string_list(entry, "nodes")
        errors.extend(f"{key}: {error}" for error in field_errors)
        if status == "pending":
            errors.append(f"{key}: pending must not contain nodes")
        if not field_errors:
            for node in nodes:
                if node not in collected:
                    errors.append(f"{key}: test node does not exist: {node}")

    _, field_errors = string_list(entry, "issues")
    errors.extend(f"{key}: {error}" for error in field_errors)

    note = entry.get("note")
    if "note" in entry and (not isinstance(note, str) or not note.strip()):
        errors.append(f"{key}: note must be a non-empty string")
    if status == "partial" and (not isinstance(note, str) or not note.strip()):
        errors.append(f"{key}: partial requires note")

    if status in {"met", "partial"} and not evals and not nodes:
        errors.append(f"{key}: {status} requires at least one eval or direct node")
    if status == "met":
        for eval_key in valid_evals:
            if coverage_map[eval_key].get("status") != "covered":
                errors.append(f"{key}: met references non-covered eval: {eval_key}")
        for node in nodes:
            if node in collected and node not in ci_collected:
                errors.append(f"{key}: met direct node is not selected by required CI: {node}")
    if status == "partial":
        has_nonpending_eval = any(
            coverage_map[eval_key].get("status") in {"covered", "partial"}
            for eval_key in valid_evals
        )
        has_existing_node = any(node in collected for node in nodes)
        if not has_nonpending_eval and not has_existing_node:
            errors.append(
                f"{key}: partial requires a covered/partial eval or an existing direct node"
            )
    return errors


def requirement_sort_key(key: str) -> tuple[int, str]:
    match = KEY_RE.fullmatch(key)
    return (int(key.split(".", 1)[1]), key) if match else (sys.maxsize, key)


def main() -> int:
    criteria, errors = read_criteria()
    ledger, ledger_errors = read_toml(LEDGER_PATH, "tests/acceptance_ledger.toml")
    coverage_map, coverage_errors = read_toml(
        COVERAGE_MAP_PATH, "tests/coverage_map.toml"
    )
    errors.extend(ledger_errors)
    errors.extend(coverage_errors)

    ledger_keys = set(ledger)
    malformed = sorted(key for key in ledger_keys if not KEY_RE.fullmatch(key))
    errors.extend(f"malformed acceptance-ledger key: {key}" for key in malformed)
    errors.extend(
        f"missing acceptance-ledger entry: {key}"
        for key in sorted(criteria - ledger_keys, key=requirement_sort_key)
    )
    errors.extend(
        f"orphaned acceptance-ledger entry: {key}"
        for key in sorted(ledger_keys - criteria, key=requirement_sort_key)
    )

    collected, collection_errors = collect_test_nodes(include_all_markers=True)
    ci_collected, ci_collection_errors = collect_test_nodes(include_all_markers=False)
    errors.extend(collection_errors)
    errors.extend(ci_collection_errors)
    if not ledger_errors and not coverage_errors and not collection_errors and not ci_collection_errors:
        for key in sorted(ledger_keys, key=requirement_sort_key):
            errors.extend(
                validate_entry(
                    key, ledger[key], coverage_map, collected, ci_collected
                )
            )

        direct_evidence_nodes = {
            node
            for entry in ledger.values()
            if isinstance(entry, dict) and entry.get("status") in {"met", "partial"}
            for node in entry.get("nodes", [])
            if isinstance(node, str)
        }
        errors.extend(execute_evidence_nodes(direct_evidence_nodes, set()))

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    counts = Counter(entry["status"] for entry in ledger.values())
    print(
        f"OK: acceptance ledger covers {len(criteria)} §24 criteria "
        f"({counts['met']} met, {counts['partial']} partial, "
        f"{counts['pending']} pending); {len(collected)} test nodes collected, "
        f"{len(ci_collected)} selected by required CI"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
