"""Shared synthetic setup for the Vera Example Phase 0 tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from exp2res.storage.workspace import initialize_workspace


FIXED_NOW = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
VERA_CORPUS = REPOSITORY_ROOT / "examples" / "vera" / "corpus"


def configure_timezone(workspace: Path, timezone_name: str = "Etc/UTC") -> None:
    config = workspace / ".exp2res" / "config.toml"
    config.write_text(
        f'[workspace]\ntimezone = "{timezone_name}"\n\n'
        "[privacy]\nignore_paths = []\n",
        encoding="utf-8",
    )
    config.chmod(0o600)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "private-workspace"
    root.mkdir()
    initialize_workspace(root, clock=lambda: FIXED_NOW)
    configure_timezone(root)
    return root
