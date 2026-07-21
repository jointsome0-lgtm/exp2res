"""Issue #79 offline Vera Example first-mirror acceptance replay."""

from pathlib import Path

import pytest

from scripts.demo import ROOT, run_demo, verify_demo


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle, pytest.mark.golden]


def test_vera_first_mirror_demo_is_closed_current_blocking_and_deterministic(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "Vera Example demo workspace"
    run_demo(workspace, emit=False)
    verify_demo(workspace, check_golden=True, determinism=True)

    transcript = workspace / "demo-transcript.txt"
    transcript.write_bytes(transcript.read_bytes() + b"/Users/vera/private\n")
    with pytest.raises(AssertionError, match="absolute private path"):
        verify_demo(workspace, check_golden=False, determinism=False)

    inside_checkout = ROOT / "demo" / "workspace"
    with pytest.raises(ValueError, match="outside the public checkout"):
        run_demo(inside_checkout, emit=False)
    assert not inside_checkout.exists()
