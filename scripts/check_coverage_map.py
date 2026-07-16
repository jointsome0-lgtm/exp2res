#!/usr/bin/env python3
"""Check that every §21 eval maps to existing pytest nodes or an owning phase."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import xml.etree.ElementTree as ET

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # exercised by the supported Python 3.10 environment
    import tomli as tomllib  # type: ignore[no-redef]


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPOSITORY_ROOT / "spec" / "21-evals.md"
MAP_PATH = REPOSITORY_ROOT / "tests" / "coverage_map.toml"
HEADER_RE = re.compile(r"^## §21\.([1-9]\d*)\b")
KEY_RE = re.compile(r"^21\.[1-9]\d*$")
STATUSES = frozenset({"covered", "partial", "pending"})
PHASES = frozenset({"1", "2", "3", "4", "5"})


def read_spec_headers() -> tuple[set[str], list[str]]:
    try:
        lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return set(), [f"cannot read {SPEC_PATH.relative_to(REPOSITORY_ROOT)}: {exc}"]

    keys = [f"21.{match.group(1)}" for line in lines if (match := HEADER_RE.match(line))]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    errors = [f"duplicate §21 header: {key}" for key in duplicates]
    return set(keys), errors


def read_coverage_map() -> tuple[dict[str, Any], list[str]]:
    try:
        parsed = tomllib.loads(MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return {}, [f"cannot parse {MAP_PATH.relative_to(REPOSITORY_ROOT)}: {exc}"]
    if not isinstance(parsed, dict):
        return {}, [f"{MAP_PATH.relative_to(REPOSITORY_ROOT)} must contain TOML tables"]
    return parsed, []


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
        detail_lines = [line.strip() for line in (result.stderr + result.stdout).splitlines() if line.strip()]
        detail = detail_lines[-1] if detail_lines else "no pytest diagnostic"
        return set(), [f"pytest collection failed (exit {result.returncode}): {detail}"]
    nodes = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }
    return nodes, []


def validate_entry(
    key: str,
    entry: Any,
    collected: set[str],
    ci_collected: set[str],
) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{key}: entry must be a TOML table"]

    errors: list[str] = []
    status = entry.get("status")
    if status not in STATUSES:
        errors.append(f"{key}: status must be covered, partial, or pending")

    nodes_present = "nodes" in entry
    nodes = entry.get("nodes")
    valid_nodes = isinstance(nodes, list) and all(isinstance(node, str) and node for node in nodes)
    if nodes_present and not valid_nodes:
        errors.append(f"{key}: nodes must be a list of non-empty pytest node IDs")
    elif valid_nodes and len(nodes) != len(set(nodes)):
        errors.append(f"{key}: nodes contains duplicates")

    if status in {"covered", "partial"} and (not valid_nodes or not nodes):
        errors.append(f"{key}: {status} requires non-empty nodes")
    if status == "pending" and nodes_present:
        errors.append(f"{key}: pending must not contain nodes")

    phase = entry.get("phase")
    if status in {"pending", "partial"} and phase not in PHASES:
        errors.append(f"{key}: {status} requires phase as a string from 1 through 5")
    elif "phase" in entry and phase not in PHASES:
        errors.append(f"{key}: phase must be a string from 1 through 5")

    note = entry.get("note")
    if "note" in entry and (not isinstance(note, str) or not note.strip()):
        errors.append(f"{key}: note must be a non-empty string")
    if status == "partial" and (not isinstance(note, str) or not note.strip()):
        errors.append(f"{key}: partial requires note")

    if status in {"covered", "partial"} and valid_nodes:
        for node in nodes:
            if node not in collected:
                errors.append(f"{key}: test node does not exist: {node}")
            elif node not in ci_collected:
                errors.append(
                    f"{key}: {status} test is not selected by required CI: {node}"
                )
    return errors


def junit_identity(node: str) -> tuple[str, str]:
    parts = node.split("::")
    module = parts[0].removesuffix(".py").replace("/", ".")
    return ".".join([module, *parts[1:-1]]), parts[-1]


def execute_covered_nodes(nodes: set[str]) -> list[str]:
    if not nodes:
        return []

    ordered_nodes = sorted(nodes)
    with tempfile.TemporaryDirectory(prefix="exp2res-coverage-") as temporary:
        report_path = Path(temporary) / "pytest.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=no",
                f"--junitxml={report_path}",
                *ordered_nodes,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
        )
        try:
            root = ET.parse(report_path).getroot()
        except (OSError, ET.ParseError) as exc:
            detail_lines = [
                line.strip()
                for line in (result.stderr + result.stdout).splitlines()
                if line.strip()
            ]
            detail = detail_lines[-1] if detail_lines else str(exc)
            return [f"covered-test execution produced no readable JUnit report: {detail}"]

    outcomes: dict[tuple[str, str], str] = {}
    for case in root.iter("testcase"):
        identity = (case.get("classname", ""), case.get("name", ""))
        outcome = "passed"
        for state in ("failure", "error", "skipped"):
            if case.find(state) is not None:
                outcome = state
                break
        outcomes[identity] = outcome

    errors: list[str] = []
    for node in ordered_nodes:
        outcome = outcomes.get(junit_identity(node), "not reported")
        if outcome != "passed":
            errors.append(
                f"covered test did not pass in required CI ({outcome}): {node}"
            )
    if result.returncode != 0 and not errors:
        detail_lines = [
            line.strip()
            for line in (result.stderr + result.stdout).splitlines()
            if line.strip()
        ]
        detail = detail_lines[-1] if detail_lines else "no pytest diagnostic"
        errors.append(
            f"covered-test execution failed (exit {result.returncode}): {detail}"
        )
    return errors


def requirement_sort_key(key: str) -> tuple[int, str]:
    match = KEY_RE.fullmatch(key)
    return (int(key.split(".", 1)[1]), key) if match else (sys.maxsize, key)


def main() -> int:
    headers, errors = read_spec_headers()
    coverage_map, map_errors = read_coverage_map()
    errors.extend(map_errors)
    if map_errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    map_keys = set(coverage_map)
    malformed = sorted(key for key in map_keys if not KEY_RE.fullmatch(key))
    errors.extend(f"malformed coverage-map key: {key}" for key in malformed)
    errors.extend(
        f"missing coverage-map entry: {key}"
        for key in sorted(headers - map_keys, key=requirement_sort_key)
    )
    errors.extend(
        f"orphaned coverage-map entry: {key}"
        for key in sorted(map_keys - headers, key=requirement_sort_key)
    )

    collected, collection_errors = collect_test_nodes(include_all_markers=True)
    errors.extend(collection_errors)
    ci_collected, ci_collection_errors = collect_test_nodes(include_all_markers=False)
    errors.extend(ci_collection_errors)
    if not collection_errors and not ci_collection_errors:
        for key in sorted(map_keys, key=requirement_sort_key):
            errors.extend(
                validate_entry(key, coverage_map[key], collected, ci_collected)
            )

        covered_nodes = {
            node
            for entry in coverage_map.values()
            if isinstance(entry, dict) and entry.get("status") == "covered"
            for node in entry.get("nodes", [])
            if isinstance(node, str)
        }
        errors.extend(execute_covered_nodes(covered_nodes))

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    counts = Counter(entry["status"] for entry in coverage_map.values())
    print(
        f"OK: coverage map covers {len(headers)} §21 items "
        f"({counts['covered']} covered, {counts['partial']} partial, "
        f"{counts['pending']} pending); {len(collected)} test nodes collected, "
        f"{len(ci_collected)} selected by required CI"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
