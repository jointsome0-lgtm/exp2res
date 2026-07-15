#!/usr/bin/env python3
"""Sync or check the shared SDD-conventions block in a consuming repository.

The canonical template lives next to this script in the sdd plugin
(`../conventions/SDD-CONVENTIONS.md`). A consuming repository embeds the
template body in one of its Markdown files (typically `AGENTS.md` or
`SDD.md`) between two markers that record the template version and a sha256
of the block body:

    <!-- BEGIN SDD-CONVENTIONS v1.0.0 sha256:<64 hex> -->
    ...template body...
    <!-- END SDD-CONVENTIONS -->

`sync` inserts or refreshes the block and touches nothing outside the
markers. `check` always validates the local block offline (markers well
formed and unique, recorded sha256 matching the block body); when a template
is available — passed with --template, or found next to this script — it
also compares the block against the template and fails on a stale version or
a changed body.

This file is deliberately a single stdlib-only script so a consuming
repository can vendor it (for example as `scripts/check_sdd_conventions.py`)
and validate a fresh checkout offline, with no plugin installed and no
network access.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

SCRIPT_VERSION = "1.0.0"

DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "conventions" / "SDD-CONVENTIONS.md"
TEMPLATE_HEADER_RE = re.compile(r"^<!-- sdd-conventions-template v(\d+\.\d+\.\d+) -->$")
BEGIN_RE = re.compile(r"^<!-- BEGIN SDD-CONVENTIONS v(\d+\.\d+\.\d+) sha256:([0-9a-f]{64}) -->$")
BEGIN_PREFIX = "<!-- BEGIN SDD-CONVENTIONS"
END_MARKER = "<!-- END SDD-CONVENTIONS -->"


class ConventionsError(Exception):
    """A failure to report on stderr before exiting with status 1."""


def body_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_text(path: Path, role: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConventionsError(f"{path}: {role} does not exist")
    except (OSError, UnicodeError) as exc:
        raise ConventionsError(f"{path}: cannot read UTF-8 {role}: {exc}")


def load_template(path: Path) -> tuple[str, str]:
    """Return (version, body) for the canonical template file."""
    lines = read_text(path, "template").splitlines()
    if not lines:
        raise ConventionsError(f"{path}: template is empty")
    header = TEMPLATE_HEADER_RE.match(lines[0])
    if header is None:
        raise ConventionsError(
            f"{path}:1: template must open with '<!-- sdd-conventions-template vX.Y.Z -->'"
        )
    body = "\n".join(lines[1:]).strip("\n") + "\n"
    if not body.strip():
        raise ConventionsError(f"{path}: template body is empty")
    for line in body.splitlines():
        if line.startswith(BEGIN_PREFIX) or line.strip() == END_MARKER:
            raise ConventionsError(f"{path}: template body must not contain the embed markers")
    return header.group(1), body


def render_block(version: str, body: str) -> str:
    begin = f"<!-- BEGIN SDD-CONVENTIONS v{version} sha256:{body_digest(body)} -->"
    return begin + "\n" + body + END_MARKER + "\n"


def find_block(lines: list[str], where: str) -> tuple[int, int, str, str]:
    """Locate the managed block; return (begin_index, end_index, version, digest)."""
    begins = [index for index, line in enumerate(lines) if line.startswith(BEGIN_PREFIX)]
    ends = [index for index, line in enumerate(lines) if line.strip() == END_MARKER]
    if len(begins) > 1 or len(ends) > 1:
        raise ConventionsError(f"{where}: multiple SDD-CONVENTIONS markers; keep exactly one block")
    if not begins and not ends:
        raise ConventionsError(
            f"{where}: no SDD-CONVENTIONS block found; run sync to insert it"
        )
    if not begins or not ends or ends[0] < begins[0]:
        raise ConventionsError(
            f"{where}: SDD-CONVENTIONS markers are unpaired or out of order"
        )
    begin_match = BEGIN_RE.match(lines[begins[0]])
    if begin_match is None:
        raise ConventionsError(
            f"{where}:{begins[0] + 1}: malformed BEGIN marker; expected "
            f"'<!-- BEGIN SDD-CONVENTIONS vX.Y.Z sha256:<64 hex> -->'"
        )
    return begins[0], ends[0], begin_match.group(1), begin_match.group(2)


def block_body(lines: list[str], begin: int, end: int) -> str:
    if end == begin + 1:
        return ""
    return "\n".join(lines[begin + 1 : end]) + "\n"


def write_atomically(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def cmd_sync(target: Path, template_path: Path | None) -> int:
    if template_path is None:
        raise ConventionsError(
            "sync requires a template: pass --template pointing at a selfos-skills "
            "checkout's plugins/sdd/conventions/SDD-CONVENTIONS.md"
        )
    version, body = load_template(template_path)
    block = render_block(version, body)

    if not target.exists():
        write_atomically(target, block)
        print(f"Created {target} with conventions block v{version}.")
        return 0

    text = read_text(target, "target")
    lines = text.splitlines()
    has_any_marker = any(
        line.startswith(BEGIN_PREFIX) or line.strip() == END_MARKER for line in lines
    )
    if not has_any_marker:
        prefix = text if text.endswith("\n") else text + "\n"
        updated = prefix + "\n" + block if prefix.strip() else block
    else:
        begin, end, _, _ = find_block(lines, str(target))
        updated_lines = lines[:begin] + block.splitlines() + lines[end + 1 :]
        updated = "\n".join(updated_lines) + "\n"

    if updated == text:
        print(f"{target} is already up to date (conventions block v{version}).")
        return 0
    write_atomically(target, updated)
    print(f"Updated {target} to conventions block v{version}.")
    return 0


def cmd_check(target: Path, template_path: Path | None) -> int:
    lines = read_text(target, "target").splitlines()
    begin, end, version, digest = find_block(lines, str(target))
    actual = body_digest(block_body(lines, begin, end))
    if actual != digest:
        raise ConventionsError(
            f"{target}:{begin + 1}: block body does not match its recorded sha256 — "
            f"local edits inside the markers? rerun sync to regenerate the block"
        )

    if template_path is None:
        print(f"OK: {target}: conventions block v{version} intact (local check only; no template available).")
        return 0

    template_version, template_body = load_template(template_path)
    if version != template_version:
        raise ConventionsError(
            f"{target}: conventions block v{version} is stale against template "
            f"v{template_version}; rerun sync --template {template_path}"
        )
    if digest != body_digest(template_body):
        raise ConventionsError(
            f"{template_path}: template body changed without a version bump "
            f"(both are v{version}); bump the template version"
        )
    print(f"OK: {target}: conventions block matches template v{template_version}.")
    return 0


def resolve_template(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    if DEFAULT_TEMPLATE.is_file():
        return DEFAULT_TEMPLATE
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"sync_conventions {SCRIPT_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("sync", "insert or refresh the conventions block in TARGET"),
        ("check", "validate the conventions block in TARGET"),
    ):
        subparser = subparsers.add_parser(name, help=help_text)
        subparser.add_argument("target", type=Path)
        subparser.add_argument(
            "--template",
            type=Path,
            default=None,
            help="canonical SDD-CONVENTIONS.md (default: the copy shipped next to this script, if present)",
        )
    args = parser.parse_args()

    try:
        template_path = resolve_template(args.template)
        if args.command == "sync":
            return cmd_sync(args.target, template_path)
        return cmd_check(args.target, template_path)
    except ConventionsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
