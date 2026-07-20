"""Offline §13.14 atomic publication, recovery, mode, and containment tests."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil

import pytest

from exp2res.errors import ManagedOutputIncompleteError
from exp2res.exports import managed

from export_helpers import assessment_graph, graph_with_gap_answered


pytestmark = [pytest.mark.lifecycle, pytest.mark.golden]
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def _publish(workspace: Path, graph):
    return managed.publish_assessment(workspace, graph, clock=lambda: NOW)


def _bytes(final: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in final.iterdir() if path.is_file()}


def test_private_modes_idempotent_reexport_and_same_view_stale_replacement(
    workspace: Path,
) -> None:
    graph = assessment_graph(all_sections=False)
    prior_umask = os.umask(0)
    try:
        first_manifest, first_paths = _publish(workspace, graph)
    finally:
        os.umask(prior_umask)
    final = workspace / "out" / "assessment" / graph.snapshot.value.id
    first_bytes = _bytes(final)
    second_manifest, second_paths = managed.publish_assessment(
        workspace,
        graph,
        clock=lambda: datetime(2026, 7, 20, 13, tzinfo=timezone.utc),
    )
    assert _bytes(final) == first_bytes
    assert first_manifest == second_manifest
    assert first_paths == second_paths
    assert [Path(path).name for path in first_paths] == [
        "evidence_map.json",
        "manifest.json",
        "report.md",
        "self_claims.json",
    ]
    for directory in (
        workspace / "out" / "assessment",
        workspace / "out" / "branch",
        final,
    ):
        assert directory.stat().st_mode & 0o777 == 0o700
    for path in final.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600

    replacement = assessment_graph(
        all_sections=False, snapshot_id="snapshot_vera_export_0002"
    )
    _publish(workspace, replacement)
    assert not final.exists()
    assert (workspace / "out" / "assessment" / replacement.snapshot.value.id).is_dir()


@pytest.mark.parametrize("failure_name", ["report.md", "manifest.json"])
def test_candidate_write_failure_never_publishes_partial_current_set(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, failure_name: str
) -> None:
    graph = assessment_graph(all_sections=False)
    original = managed._write_private_file

    def fail_selected(path: Path, data: bytes, out_root: Path) -> None:
        if path.name == failure_name:
            raise OSError("Vera Example injected write failure")
        original(path, data, out_root)

    monkeypatch.setattr(managed, "_write_private_file", fail_selected)
    with pytest.raises(OSError, match="injected write failure"):
        _publish(workspace, graph)
    parent = workspace / "out" / "assessment"
    assert not (parent / graph.snapshot.value.id).exists()
    assert not list(parent.glob(".exp2res-candidate-*"))


def test_rename_failures_before_and_after_rollback_move_preserve_prior(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    final = workspace / "out" / "assessment" / graph.snapshot.value.id
    prior = _bytes(final)
    changed = graph_with_gap_answered(graph, True)
    original = managed._rename

    def fail_before(source: Path, destination: Path) -> None:
        if source == final:
            raise OSError("Vera Example pre-rollback rename failure")
        original(source, destination)

    monkeypatch.setattr(managed, "_rename", fail_before)
    with pytest.raises(OSError, match="pre-rollback"):
        _publish(workspace, changed)
    assert _bytes(final) == prior
    monkeypatch.setattr(managed, "_rename", original)

    calls = 0

    def fail_candidate(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Vera Example candidate rename failure")
        original(source, destination)

    monkeypatch.setattr(managed, "_rename", fail_candidate)
    with pytest.raises(OSError, match="candidate rename failure"):
        _publish(workspace, changed)
    assert calls == 3
    assert _bytes(final) == prior
    assert not list(final.parent.glob(".exp2res-rollback-*"))


def test_failed_restoration_reports_rollback_residual_and_no_current_set(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    final = workspace / "out" / "assessment" / graph.snapshot.value.id
    changed = graph_with_gap_answered(graph, True)
    original = managed._rename
    calls = 0

    def fail_candidate_and_restore(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("Vera Example rename failure")
        original(source, destination)

    monkeypatch.setattr(managed, "_rename", fail_candidate_and_restore)
    with pytest.raises(ManagedOutputIncompleteError) as caught:
        _publish(workspace, changed)
    assert not final.exists()
    assert len(caught.value.residual_paths) == 1
    assert ".exp2res-rollback-" in caught.value.residual_paths[0]
    assert Path(caught.value.residual_paths[0]).is_dir()


def test_symlink_final_is_left_untouched_and_reported(workspace: Path) -> None:
    graph = assessment_graph(all_sections=False)
    assert managed.reconcile_managed_outputs(workspace) == ()
    outside = workspace.parent / "Vera Example outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("Vera Example untouched\n", encoding="utf-8")
    final = workspace / "out" / "assessment" / graph.snapshot.value.id
    final.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ManagedOutputIncompleteError) as caught:
        _publish(workspace, graph)
    assert caught.value.residual_paths == (str(final),)
    assert final.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "Vera Example untouched\n"


def test_preamble_candidate_restore_remove_and_ambiguous_matrix(
    workspace: Path,
) -> None:
    graph = assessment_graph(all_sections=False)
    assert managed.reconcile_managed_outputs(workspace) == ()
    parent = workspace / "out" / "assessment"
    candidate = parent / (
        f".exp2res-candidate-{graph.snapshot.value.id}-{'a' * 32}"
    )
    candidate.mkdir(mode=0o700)
    assert managed.reconcile_managed_outputs(workspace) == ()
    assert not candidate.exists()

    _publish(workspace, graph)
    final = parent / graph.snapshot.value.id
    rollback = parent / (
        f".exp2res-rollback-{graph.snapshot.value.id}-{'b' * 32}"
    )
    os.rename(final, rollback)
    assert managed.reconcile_managed_outputs(workspace) == ()
    assert final.is_dir() and not rollback.exists()

    os.rename(final, rollback)
    _publish(workspace, graph)
    assert final.is_dir() and rollback.is_dir()
    assert managed.reconcile_managed_outputs(workspace) == ()
    assert final.is_dir() and not rollback.exists()

    os.rename(final, rollback)
    second = parent / (
        f".exp2res-rollback-{graph.snapshot.value.id}-{'c' * 32}"
    )
    shutil.copytree(rollback, second, copy_function=shutil.copy2)
    residuals = managed.reconcile_managed_outputs(workspace)
    assert residuals == tuple(sorted((str(rollback), str(second))))
    assert rollback.is_dir() and second.is_dir() and not final.exists()


def test_preamble_planted_symlink_candidate_is_reported_once(workspace: Path) -> None:
    graph = assessment_graph(all_sections=False)
    assert managed.reconcile_managed_outputs(workspace) == ()
    parent = workspace / "out" / "assessment"
    outside = workspace.parent / "Vera Example candidate target"
    outside.mkdir()
    candidate = parent / (
        f".exp2res-candidate-{graph.snapshot.value.id}-{'d' * 32}"
    )
    candidate.symlink_to(outside, target_is_directory=True)
    residuals = managed.reconcile_managed_outputs(workspace)
    assert residuals == (str(candidate),)
    assert candidate.is_symlink() and outside.is_dir()

