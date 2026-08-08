"""Offline §13.14 atomic publication, recovery, mode, and containment tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

import pytest

from exp2res.errors import ManagedOutputIncompleteError
from exp2res.exports import managed

from export_helpers import (
    assessment_graph,
    graph_with_gap_answered,
    graph_with_gap_answered_after_export,
)


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
        "report.html",
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


def test_noncanonical_prior_manifest_is_not_accepted_for_idempotent_reexport(
    workspace: Path,
) -> None:
    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    final = workspace / "out" / "assessment" / graph.snapshot.value.id
    manifest_path = final / "manifest.json"
    parsed = json.loads(manifest_path.read_bytes())
    manifest_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)

    with pytest.raises(ManagedOutputIncompleteError) as caught:
        _publish(workspace, graph)
    assert caught.value.residual_paths == (str(final),)
    assert manifest_path.read_bytes().startswith(b"{\n  ")


def test_superseded_manifest_version_is_never_matching_or_overwritten(
    workspace: Path,
) -> None:
    """§13.14 rule 2: `report.html` joined the fixed members at version 2, so a
    version-1 set is not matching and publication reports it rather than
    rewriting bytes whose manifest the writer has already rejected."""

    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    final = workspace / "out" / "assessment" / graph.snapshot.value.id
    manifest_path = final / "manifest.json"
    parsed = json.loads(manifest_path.read_bytes())
    parsed["manifest_version"] = 1
    parsed["members"] = [
        member for member in parsed["members"] if member["name"] != "report.html"
    ]
    (final / "report.html").unlink()
    manifest_path.write_bytes(
        json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    manifest_path.chmod(0o600)
    before = _bytes(final)

    with pytest.raises(ManagedOutputIncompleteError) as caught:
        _publish(workspace, graph)
    assert caught.value.residual_paths == (str(final),)
    assert _bytes(final) == before
    assert not any(
        path.name.startswith(".exp2res-")
        for path in (workspace / "out" / "assessment").iterdir()
    )


def test_reexport_after_gap_answer_replaces_prior_set(workspace: Path) -> None:
    """§14.7: answering a listed gap after export widens the source closure;
    the same current snapshot must replace its prior set, not report it as a
    residual."""

    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    final = workspace / "out" / "assessment" / graph.snapshot.value.id
    first_bytes = _bytes(final)

    answered = graph_with_gap_answered_after_export(graph)
    manifest, _paths = managed.publish_assessment(
        workspace,
        answered,
        clock=lambda: datetime(2026, 7, 20, 13, tzinfo=timezone.utc),
    )
    answer_log_id = answered.supplemental_raw_logs[-1].id
    assert answer_log_id in manifest.source_ids.raw_log_ids
    second_bytes = _bytes(final)
    assert second_bytes != first_bytes
    assert final.is_dir()

    # Unchanged re-export of the answered graph still reuses the set.
    reused, _reused_paths = managed.publish_assessment(
        workspace,
        answered,
        clock=lambda: datetime(2026, 7, 20, 14, tzinfo=timezone.utc),
    )
    assert reused == manifest
    assert _bytes(final) == second_bytes


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


def _plant_branch_set(workspace: Path, entity_id: str) -> Path:
    path = workspace / "out" / "branch" / entity_id
    path.mkdir(mode=0o700)
    (path / "manifest.json").write_text("{}\n", encoding="utf-8")
    return path


def test_an_unstattable_set_is_a_residual_and_never_an_exception(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: this pass runs after the commit, so it never raises.

    An entry that turns unreadable between the pre-commit report and the
    removal would otherwise cost the caller the committed result it is
    holding for the envelope.
    """

    assert managed.reconcile_managed_outputs(workspace) == ()
    first = _plant_branch_set(workspace, "branch_vera_0001")
    second = _plant_branch_set(workspace, "branch_vera_0002")
    real_lstat = managed._lstat

    def refuse_the_second(path: Path):
        if path == second:
            raise PermissionError(13, "unreadable")
        return real_lstat(path)

    monkeypatch.setattr(managed, "_lstat", refuse_the_second)

    residuals = managed.remove_branch_sets(
        workspace, ["branch_vera_0001", "branch_vera_0002"]
    )
    assert residuals == (str(second),)
    assert not first.exists() and second.is_dir()


def test_an_unflushed_removal_is_never_banked(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removal is banked only once the directory entry is durable.

    The unlink itself is visible immediately, but neither a cancellation nor a
    failure inside the flush proves anything against a crash — and the returned
    residual is lost whenever a later half of the same pass is cancelled — so
    the caller must keep reporting the set rather than subtract it.
    """

    assert managed.reconcile_managed_outputs(workspace) == ()
    interrupted = _plant_branch_set(workspace, "branch_vera_0001")

    def interrupt_flush(*_arguments, **_keywords):
        raise KeyboardInterrupt()

    monkeypatch.setattr(managed, "_fsync_directory", interrupt_flush)

    banked: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        managed.remove_branch_sets(
            workspace, ["branch_vera_0001"], removed_ledger=banked
        )
    assert banked == []
    assert not interrupted.exists()

    failed = _plant_branch_set(workspace, "branch_vera_0002")

    def fail_flush(*_arguments, **_keywords):
        raise OSError(5, "flush failed")

    monkeypatch.setattr(managed, "_fsync_directory", fail_flush)

    residuals = managed.remove_branch_sets(
        workspace, ["branch_vera_0002"], removed_ledger=banked
    )
    assert residuals == (str(failed.parent),)
    assert banked == []
    assert not failed.exists()

