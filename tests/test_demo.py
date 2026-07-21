"""Issue #79 offline Vera Example first-mirror acceptance replay."""

from pathlib import Path

import pytest

from scripts.demo import run_demo, verify_demo


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle, pytest.mark.golden]


def test_vera_first_mirror_demo_is_closed_current_blocking_and_deterministic(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "Vera Example demo workspace"
    run_demo(workspace, emit=False)
    verify_demo(workspace, check_golden=True, determinism=True)
