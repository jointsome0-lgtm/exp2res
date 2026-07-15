#!/usr/bin/env python3
"""Run all repository-owned offline checks."""

from pathlib import Path
import shlex
import subprocess
import sys
from typing import List


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def run_check(arguments: List[str]) -> bool:
    command = [sys.executable, *arguments]
    print(f"$ {shlex.join(command)}", flush=True)
    result = subprocess.run(command, cwd=REPOSITORY_ROOT)
    if result.returncode == 0:
        print("passed", flush=True)
        return True

    print(f"failed (exit {result.returncode})", flush=True)
    return False


def main() -> int:
    checks = [
        ["scripts/check_public_hygiene.py"],
        ["scripts/check_sdd_conventions.py", "check", "AGENTS.md"],
        [
            "scripts/check_decision_log.py",
            "--baseline",
            "2026-07-15",
            "DECISION-LOG.md",
        ],
    ]
    failed = False
    for arguments in checks:
        if not run_check(arguments):
            failed = True

    corpus_check = REPOSITORY_ROOT / "examples/vera/corpus.py"
    if corpus_check.is_file():
        if not run_check(["examples/vera/corpus.py", "check"]):
            failed = True
    else:
        print("examples/vera/corpus.py check: skipped (not present)", flush=True)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
