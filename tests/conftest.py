"""Shared synthetic setup for the Vera Example Phase 0 tests."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from exp2res.storage.workspace import initialize_workspace


FIXED_NOW = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
VERA_CORPUS = REPOSITORY_ROOT / "examples" / "vera" / "corpus"


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Require both an explicit `-m live` selection and an environment opt-in."""
    live_selected = str(config.getoption("markexpr", "")).strip() == "live"
    if live_selected and os.environ.get("EXP2RES_LIVE_LLM") == "1":
        return
    skip_live = pytest.mark.skip(
        reason="live tests require both -m live and EXP2RES_LIVE_LLM=1"
    )
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip_live)


def configure_timezone(workspace: Path, timezone_name: str = "Etc/UTC") -> None:
    # Keep the §15.13 [llm] selection a fresh init writes: every configured
    # workspace has one, and the §13.13 lifecycle resolves it eagerly even
    # when zero planned calls keep the rebuild offline.
    config = workspace / ".exp2res" / "config.toml"
    config.write_text(
        f'[workspace]\ntimezone = "{timezone_name}"\n\n'
        '[llm]\nadapter = "codex-cli"\nmodel = "gpt-5.6-sol"\n\n'
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
