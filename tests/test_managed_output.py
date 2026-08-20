"""Offline §13.14 atomic publication, recovery, mode, and containment tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat

import pytest

from exp2res.errors import (
    IntegrityFailureError,
    ManagedOutputIncompleteError,
    SchemaCompatibilityError,
)
from exp2res.exports import managed
import exp2res.services.privacy as privacy_service
from conftest import anchor_locked_database
from exp2res.services.privacy import remove_managed_backups
import exp2res.storage.workspace as workspace_module
from exp2res.storage.workspace import writer_database

from export_helpers import (
    assessment_graph,
    graph_with_gap_answered,
    graph_with_gap_answered_after_export,
)


pytestmark = [pytest.mark.lifecycle, pytest.mark.golden]
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def _reconcile(workspace: Path) -> tuple[str, ...]:
    """Run §13.14 rule 5's preamble under an anchor, as `writer_database` does."""

    with anchor_locked_database(workspace):
        return managed.reconcile_managed_outputs(workspace)


def _publish(workspace: Path, graph, *, clock=None):
    """Publish under an anchor, as every §13.14 writer does behind the lock."""

    with anchor_locked_database(workspace):
        return managed.publish_assessment(
            workspace, graph, clock=clock or (lambda: NOW)
        )


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
    second_manifest, second_paths = _publish(
        workspace, graph, clock=lambda: datetime(2026, 7, 20, 13, tzinfo=timezone.utc)
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
    manifest, _paths = _publish(
        workspace, answered, clock=lambda: datetime(2026, 7, 20, 13, tzinfo=timezone.utc)
    )
    answer_log_id = answered.supplemental_raw_logs[-1].id
    assert answer_log_id in manifest.source_ids.raw_log_ids
    second_bytes = _bytes(final)
    assert second_bytes != first_bytes
    assert final.is_dir()

    # Unchanged re-export of the answered graph still reuses the set.
    reused, _reused_paths = _publish(
        workspace, answered, clock=lambda: datetime(2026, 7, 20, 14, tzinfo=timezone.utc)
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
    assert _reconcile(workspace) == ()
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
    assert _reconcile(workspace) == ()
    parent = workspace / "out" / "assessment"
    candidate = parent / (
        f".exp2res-candidate-{graph.snapshot.value.id}-{'a' * 32}"
    )
    candidate.mkdir(mode=0o700)
    assert _reconcile(workspace) == ()
    assert not candidate.exists()

    _publish(workspace, graph)
    final = parent / graph.snapshot.value.id
    rollback = parent / (
        f".exp2res-rollback-{graph.snapshot.value.id}-{'b' * 32}"
    )
    os.rename(final, rollback)
    assert _reconcile(workspace) == ()
    assert final.is_dir() and not rollback.exists()

    os.rename(final, rollback)
    _publish(workspace, graph)
    assert final.is_dir() and rollback.is_dir()
    assert _reconcile(workspace) == ()
    assert final.is_dir() and not rollback.exists()

    os.rename(final, rollback)
    second = parent / (
        f".exp2res-rollback-{graph.snapshot.value.id}-{'c' * 32}"
    )
    shutil.copytree(rollback, second, copy_function=shutil.copy2)
    residuals = _reconcile(workspace)
    assert residuals == tuple(sorted((str(rollback), str(second))))
    assert rollback.is_dir() and second.is_dir() and not final.exists()


def test_preamble_planted_symlink_candidate_is_reported_once(workspace: Path) -> None:
    graph = assessment_graph(all_sections=False)
    assert _reconcile(workspace) == ()
    parent = workspace / "out" / "assessment"
    outside = workspace.parent / "Vera Example candidate target"
    outside.mkdir()
    candidate = parent / (
        f".exp2res-candidate-{graph.snapshot.value.id}-{'d' * 32}"
    )
    candidate.symlink_to(outside, target_is_directory=True)
    residuals = _reconcile(workspace)
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

    assert _reconcile(workspace) == ()
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

    def refuse_the_parent(path: Path):
        if path == second.parent:
            raise PermissionError(13, "unsearchable")
        return real_lstat(path)

    monkeypatch.setattr(managed, "_lstat", refuse_the_parent)

    assert managed.remove_branch_sets(workspace, ["branch_vera_0002"]) == (
        str(second),
    )
    assert second.is_dir()


def test_an_unflushed_removal_is_never_banked(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removal is banked only once the directory entry is durable.

    The unlink itself is visible immediately, but neither a cancellation nor a
    failure inside the flush proves anything against a crash — and the returned
    residual is lost whenever a later half of the same pass is cancelled — so
    the caller must keep reporting the set rather than subtract it.
    """

    assert _reconcile(workspace) == ()
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
    # The set joins its parent in the report: un-banked and unnamed would leave
    # a set a crash may restore counted as neither removed nor residual.
    assert residuals == (str(failed.parent), str(failed))
    assert banked == []
    assert not failed.exists()



def _plant_assessment_set(workspace: Path, entity_id: str) -> Path:
    (workspace / "out" / "branch").mkdir(mode=0o700, parents=True, exist_ok=True)
    path = workspace / "out" / "assessment" / entity_id
    path.mkdir(mode=0o700, parents=True)
    (path / "manifest.json").write_text("{}\n", encoding="utf-8")
    return path


def _replace_workspace(workspace: Path, tmp_path: Path, *, name: str) -> Path:
    """Rename `workspace` aside and leave a bare replacement at its pathname."""

    moved = tmp_path / name
    shutil.move(str(workspace), str(moved))
    (workspace / ".exp2res").mkdir(mode=0o700, parents=True)
    (workspace / ".exp2res" / "exp2res.sqlite").write_bytes(b"")
    return moved


def test_a_replaced_workspace_reports_every_set_instead_of_removing_it(
    workspace: Path, tmp_path: Path
) -> None:
    """§13.14 rule 9: a foreign tree is reported, never cleaned.

    The anchor is established while the pathname still holds this workspace's
    own database, exactly as `writer_lock` does, and the tree is then replaced
    beneath it.
    """

    snapshot_set = _plant_assessment_set(workspace, "snapshot_vera_0001")
    branch_set = _plant_branch_set(workspace, "branch_vera_0001")

    banked: list[str] = []
    with anchor_locked_database(workspace):
        replacement = _replace_workspace(workspace, tmp_path, name="replacement")
        foreign_snapshot = _plant_assessment_set(workspace, "snapshot_vera_0001")
        foreign_branch = _plant_branch_set(workspace, "branch_vera_0001")
        residuals = managed.remove_managed_sets_for_locked_database(
            workspace,
            snapshot_ids=["snapshot_vera_0001"],
            branch_ids=["branch_vera_0001"],
            removed_ledger=banked,
        )

    assert residuals == (str(foreign_snapshot), str(foreign_branch))
    assert banked == []
    assert foreign_snapshot.is_dir() and foreign_branch.is_dir()
    assert (replacement / "out" / "assessment" / "snapshot_vera_0001").is_dir()
    assert snapshot_set == workspace / "out" / "assessment" / "snapshot_vera_0001"
    assert branch_set == workspace / "out" / "branch" / "branch_vera_0001"


def test_a_replacement_without_matching_sets_still_reports_them_all(
    workspace: Path, tmp_path: Path
) -> None:
    """Rule 9 reports the stale sets, not what the foreign pathname holds.

    The replacement owns none of the selected IDs. Filtering the report by what
    exists under the pathname would answer "nothing left to invalidate" about a
    tree this command never wrote to, while the sets it actually stranded live
    on in the renamed original.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")
    _plant_branch_set(workspace, "branch_vera_0001")

    with anchor_locked_database(workspace):
        moved = _replace_workspace(workspace, tmp_path, name="empty-replacement")
        (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
        (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
        residuals = managed.remove_managed_sets_for_locked_database(
            workspace,
            snapshot_ids=["snapshot_vera_0001"],
            branch_ids=["branch_vera_0001"],
        )

    assert residuals == (
        str(workspace / "out" / "assessment" / "snapshot_vera_0001"),
        str(workspace / "out" / "branch" / "branch_vera_0001"),
    )
    assert (moved / "out" / "assessment" / "snapshot_vera_0001").is_dir()
    assert (moved / "out" / "branch" / "branch_vera_0001").is_dir()


def test_a_workspace_replaced_mid_pass_keeps_the_rest_of_the_sets(
    workspace: Path, tmp_path: Path
) -> None:
    """Rule 9 is re-asked per entry, not once for the whole pass.

    The replacement lands after the first removal, so a single check at the
    entry would authorize every later unlink against the foreign tree. The
    parent joins the report because the closing flush would reopen it through
    the replaced pathname, which banks nothing about the first removal.
    """

    banked: list[str] = []
    first = _plant_assessment_set(workspace, "snapshot_vera_0001")
    _plant_assessment_set(workspace, "snapshot_vera_0002")
    real_remove_entry = managed._remove_entry
    moved: list[Path] = []

    def replace_after_first(path: Path, out_root: Path, still_live=None) -> bool:
        removed = real_remove_entry(path, out_root, still_live)
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="mid-pass"))
            _plant_assessment_set(workspace, "snapshot_vera_0002")
        return removed

    with anchor_locked_database(workspace):
        managed._remove_entry = replace_after_first
        try:
            residuals = managed.remove_managed_sets_for_locked_database(
                workspace,
                snapshot_ids=["snapshot_vera_0001", "snapshot_vera_0002"],
                removed_ledger=banked,
            )
        finally:
            managed._remove_entry = real_remove_entry

    assert residuals == (
        str(workspace / "out" / "assessment"),
        str(workspace / "out" / "assessment" / "snapshot_vera_0001"),
        str(workspace / "out" / "assessment" / "snapshot_vera_0002"),
    )
    assert banked == []
    assert not (moved[0] / "out" / "assessment" / "snapshot_vera_0001").exists()
    assert first == workspace / "out" / "assessment" / "snapshot_vera_0001"
    assert (workspace / "out" / "assessment" / "snapshot_vera_0002").is_dir()


def test_a_workspace_replaced_between_two_members_keeps_the_rest_of_the_set(
    workspace: Path, tmp_path: Path
) -> None:
    """One set is many pathname-resolved unlinks, and each is bound.

    A per-ID recheck alone stops at the set boundary, so a replacement landing
    after an early member would have the remainder of the tree — and its
    closing directory — removed from the foreign set of the same name.
    """

    planted = _plant_assessment_set(workspace, "snapshot_vera_0001")
    for name in ("report.html", "report.md", "self_claims.json"):
        (planted / name).write_text("{}\n", encoding="utf-8")
    real_open_directory_fd = managed._open_directory_fd
    moved: list[Path] = []

    def replace_after_first_member(path: Path, out_root: Path) -> int:
        descriptor = real_open_directory_fd(path, out_root)
        if not moved and path.name == "snapshot_vera_0001":
            moved.append(_replace_workspace(workspace, tmp_path, name="mid-set"))
            foreign = _plant_assessment_set(workspace, "snapshot_vera_0001")
            for name in ("report.html", "report.md", "self_claims.json"):
                (foreign / name).write_text("{}\n", encoding="utf-8")
        return descriptor

    with anchor_locked_database(workspace):
        managed._open_directory_fd = replace_after_first_member
        try:
            residuals = managed.remove_managed_sets_for_locked_database(
                workspace, snapshot_ids=["snapshot_vera_0001"]
            )
        finally:
            managed._open_directory_fd = real_open_directory_fd

    foreign_set = workspace / "out" / "assessment" / "snapshot_vera_0001"
    assert residuals == (str(foreign_set),)
    assert sorted(path.name for path in foreign_set.iterdir()) == [
        "manifest.json",
        "report.html",
        "report.md",
        "self_claims.json",
    ]


def test_the_writer_preamble_is_bound_to_the_locked_database(
    workspace: Path, tmp_path: Path
) -> None:
    """§13.14 rule 9 covers rule 5's preamble, removals and promotion alike.

    Reconciliation clears abandoned candidates and promotes a surviving
    rollback, all by pathname. Under a replacement it would tidy another
    workspace's half-published sets while this one's stayed abandoned.
    """

    parent = workspace / "out" / "assessment"
    parent.mkdir(mode=0o700, parents=True)

    with anchor_locked_database(workspace):
        moved = _replace_workspace(workspace, tmp_path, name="before-preamble")
        foreign_parent = workspace / "out" / "assessment"
        foreign_parent.mkdir(mode=0o700, parents=True)
        candidate = foreign_parent / ".exp2res-candidate-snapshot_vera_0001-abcd"
        candidate.mkdir(mode=0o700)
        (candidate / "manifest.json").write_text("{}\n", encoding="utf-8")
        residuals = managed.reconcile_managed_outputs(workspace)

    assert residuals == (str((workspace / "out").absolute()),)
    assert candidate.is_dir()
    assert not (workspace / "out" / "branch").exists()
    assert (moved / "out" / "assessment").is_dir()


def test_the_total_sweep_is_bound_to_the_locked_database(
    workspace: Path, tmp_path: Path
) -> None:
    """Rule 9 binds the widest removal too — it takes whatever it finds.

    A privacy deletion sweeping a replacement would empty another workspace's
    managed output entirely while the tree it is deleting from kept all of it.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")

    with anchor_locked_database(workspace):
        moved = _replace_workspace(workspace, tmp_path, name="before-sweep")
        foreign = _plant_assessment_set(workspace, "snapshot_vera_0001")
        residuals = managed.remove_all_managed_output_entries(workspace)

    assert residuals == (str((workspace / "out").absolute()),)
    assert foreign.is_dir()
    assert (moved / "out" / "assessment" / "snapshot_vera_0001").is_dir()


def test_the_backup_sweep_takes_its_anchor_from_the_lock(
    workspace: Path, tmp_path: Path
) -> None:
    """Rule 9 supplies `remove_managed_backups` the anchor it never took.

    `jd delete` already passed one explicitly for its partial purge; the
    whole-store sweep read none, so a replacement's backups were purged while
    this workspace's own survived.
    """

    store = workspace / ".exp2res" / "backup"
    store.mkdir(mode=0o700, exist_ok=True)
    (store / "schema-10.sqlite").write_bytes(b"Vera Example migration backup")

    with anchor_locked_database(workspace):
        moved = _replace_workspace(workspace, tmp_path, name="before-backup-sweep")
        foreign_store = workspace / ".exp2res" / "backup"
        foreign_store.mkdir(mode=0o700)
        foreign_backup = foreign_store / "schema-10.sqlite"
        foreign_backup.write_bytes(b"Vera Example replacement backup")
        residuals = remove_managed_backups(workspace)

    assert residuals == (str(foreign_store.absolute()),)
    assert foreign_backup.read_bytes() == b"Vera Example replacement backup"
    assert (moved / ".exp2res" / "backup" / "schema-10.sqlite").is_file()


def test_publication_never_removes_a_stale_set_from_a_replacement(
    workspace: Path, tmp_path: Path
) -> None:
    """Rule 9 reaches publication, not only invalidation.

    Rule 5 replaces every prior same-view set before publishing the new one and
    removes its own rollback afterwards, all by pathname. Under a replacement
    that would delete sets the other workspace still owns, while this export's
    transaction stayed attached to the original database. The refusal precedes
    the managed parents, so it leaves nothing behind in the foreign tree.
    """

    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    replacement_graph = assessment_graph(
        all_sections=False, snapshot_id="snapshot_vera_export_0002"
    )

    with anchor_locked_database(workspace):
        moved = _replace_workspace(workspace, tmp_path, name="before-publication")
        # A complete replacement, so nothing but rule 9 can refuse: publication
        # would otherwise sweep this valid same-view set and publish over it.
        (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
        foreign = workspace / "out" / "assessment" / graph.snapshot.value.id
        foreign.parent.mkdir(mode=0o700, parents=True)
        shutil.copytree(moved / "out" / "assessment" / graph.snapshot.value.id, foreign)
        foreign_bytes = _bytes(foreign)
        with pytest.raises(ManagedOutputIncompleteError) as caught:
            managed.publish_assessment(
                workspace, replacement_graph, clock=lambda: NOW
            )

    assert caught.value.residual_paths == (str((workspace / "out").absolute()),)
    assert _bytes(foreign) == foreign_bytes
    assert not (
        workspace / "out" / "assessment" / replacement_graph.snapshot.value.id
    ).exists()
    assert (moved / "out" / "assessment" / graph.snapshot.value.id).is_dir()


def test_an_empty_replacement_never_reads_as_a_finished_total_sweep(
    workspace: Path, tmp_path: Path
) -> None:
    """The sweep's own enumeration is bound, not just the removals it feeds.

    A replacement with neither managed parent makes both read as absent, which
    would report nothing left to clean while the tree the caller is deleting
    from kept all of its managed output.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")
    real_canonical_roots = managed._canonical_roots
    moved: list[Path] = []

    def replace_after_roots(target: Path):
        roots = real_canonical_roots(target)
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="empty-sweep"))
            (workspace / ".exp2res").mkdir(mode=0o700, exist_ok=True)
        return roots

    with anchor_locked_database(workspace):
        managed._canonical_roots = replace_after_roots
        try:
            residuals = managed.remove_all_managed_output_entries(workspace)
        finally:
            managed._canonical_roots = real_canonical_roots

    assert residuals == (str((workspace / "out").absolute()),)
    assert (moved[0] / "out" / "assessment" / "snapshot_vera_0001").is_dir()


def test_the_backup_sweep_without_an_anchor_removes_nothing(
    workspace: Path,
) -> None:
    """An anchor that was never established is refusal, not permission."""

    store = workspace / ".exp2res" / "backup"
    store.mkdir(mode=0o700, exist_ok=True)
    backup = store / "schema-10.sqlite"
    backup.write_bytes(b"Vera Example migration backup")

    residuals = remove_managed_backups(workspace)

    assert residuals == (str(store.absolute()),)
    assert backup.read_bytes() == b"Vera Example migration backup"


def test_an_anchor_that_was_never_established_removes_nothing(
    workspace: Path,
) -> None:
    """Rule 9: an absent anchor is refusal, not permission."""

    planted = _plant_assessment_set(workspace, "snapshot_vera_0001")

    residuals = managed.remove_managed_sets_for_locked_database(
        workspace, snapshot_ids=["snapshot_vera_0001"]
    )

    assert residuals == (str(planted),)
    assert planted.is_dir()


def test_the_live_workspace_still_has_its_sets_removed(workspace: Path) -> None:
    """The guard must not become a blanket refusal to clean up."""

    snapshot_set = _plant_assessment_set(workspace, "snapshot_vera_0001")
    branch_set = _plant_branch_set(workspace, "branch_vera_0001")

    banked: list[str] = []
    with anchor_locked_database(workspace):
        residuals = managed.remove_managed_sets_for_locked_database(
            workspace,
            snapshot_ids=["snapshot_vera_0001"],
            branch_ids=["branch_vera_0001"],
            removed_ledger=banked,
        )

    assert residuals == ()
    assert banked == [str(snapshot_set), str(branch_set)]
    assert not snapshot_set.exists() and not branch_set.exists()


def test_the_anchor_belongs_to_the_lock_not_to_the_cleanup_frame(
    workspace: Path, tmp_path: Path
) -> None:
    """§13.14 rule 9 anchors where the §8.1 authority is acquired.

    A stage handed an already-open connection reaches its cleanup a whole LLM
    invocation after the lock was taken. An identity read there would describe
    the replacement and authorize cleaning it, while every mutation stayed in
    the original open database.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")

    with writer_database(workspace):
        moved = _replace_workspace(workspace, tmp_path, name="after-acquisition")
        foreign = _plant_assessment_set(workspace, "snapshot_vera_0001")
        residuals = managed.remove_managed_sets_for_locked_database(
            workspace, snapshot_ids=["snapshot_vera_0001"]
        )

    assert residuals == (str(foreign),)
    assert foreign.is_dir()
    assert (moved / "out" / "assessment" / "snapshot_vera_0001").is_dir()


def test_the_anchor_is_read_beside_the_lock_not_through_the_pathname(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§8.1 rule 30: the identity comes from the entry the lock was taken on.

    A replacement landing between the lock and the anchor would otherwise be
    installed as the identity every later check trusts, so the workspace whose
    authority this command never acquired would answer "still live" to it.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")
    real_flock = workspace_module.fcntl.flock
    swapped: list[Path] = []

    def flock_then_replace(descriptor: int, operation: int) -> None:
        real_flock(descriptor, operation)
        if not swapped:
            swapped.append(_replace_workspace(workspace, tmp_path, name="mid-lock"))

    monkeypatch.setattr(workspace_module.fcntl, "flock", flock_then_replace)

    with workspace_module.writer_lock(workspace):
        foreign = _plant_assessment_set(workspace, "snapshot_vera_0001")
        residuals = managed.remove_managed_sets_for_locked_database(
            workspace, snapshot_ids=["snapshot_vera_0001"]
        )

    assert residuals == (str(foreign),)
    assert foreign.is_dir()
    assert (swapped[0] / "out" / "assessment" / "snapshot_vera_0001").is_dir()


def test_a_first_time_publication_is_bound_after_its_entry_gate(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 9 reaches the candidate and its promotion, not only the cleanups.

    A first-time export has no prior set and no rollback, so every removal that
    consults the predicate is skipped: without a check of its own, the pass
    would write the candidate and rename it into the replacement and report a
    successful export assembled from the original database.
    """

    graph = assessment_graph(all_sections=False)
    real_parents = managed._ensure_managed_parents
    moved: list[Path] = []

    def parents_then_replace(target: Path, *, still_live=None):
        roots = real_parents(target, still_live=still_live)
        moved.append(_replace_workspace(workspace, tmp_path, name="after-gate"))
        (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
        (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
        return roots

    monkeypatch.setattr(managed, "_ensure_managed_parents", parents_then_replace)

    with anchor_locked_database(workspace):
        with pytest.raises(ManagedOutputIncompleteError):
            managed.publish_assessment(workspace, graph, clock=lambda: NOW)

    assert list((workspace / "out" / "assessment").iterdir()) == []
    assert not (moved[0] / "out" / "assessment" / graph.snapshot.value.id).exists()


def test_an_absent_parent_in_a_replacement_is_not_a_finished_cleanup(
    workspace: Path, tmp_path: Path
) -> None:
    """Absence proves a removal only in the tree the pass is bound to.

    An empty replacement makes the managed parent read as absent, which the
    selective helper would otherwise report as nothing left to clean while
    every selected set survives in the tree the caller committed to.
    """

    planted = _plant_assessment_set(workspace, "snapshot_vera_0001")

    with anchor_locked_database(workspace):
        moved = _replace_workspace(workspace, tmp_path, name="empty-replacement")
        residuals = managed.remove_managed_sets_for_locked_database(
            workspace, snapshot_ids=["snapshot_vera_0001"]
        )

    assert residuals == (str(workspace / "out" / "assessment" / "snapshot_vera_0001"),)
    assert (moved / "out" / "assessment" / "snapshot_vera_0001").is_dir()
    assert not planted.exists()


def test_a_backup_purge_notices_the_workspace_root_changing_hands(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The purge chain matches each level to its parent; the root has none.

    Every path it reports is built from the pathname, so a rename of the
    workspace root itself would leave it unlinking the detached original store
    while naming files in the untouched replacement.
    """

    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, parents=True)
    (backup_root / "pre-migration.sqlite").write_bytes(b"")
    real_scandir = privacy_service.os.scandir
    moved: list[Path] = []

    def scandir_then_replace(descriptor):
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="root-renamed"))
        return real_scandir(descriptor)

    with anchor_locked_database(workspace):
        monkeypatch.setattr(privacy_service.os, "scandir", scandir_then_replace)
        removed, residuals = privacy_service.purge_managed_backups(
            workspace,
            expected_database=privacy_service.locked_database_anchor(),
        )

    assert removed == ()
    assert residuals == (str(backup_root.absolute()),)
    assert (moved[0] / ".exp2res" / "backup" / "pre-migration.sqlite").is_file()


def test_a_mismatch_arriving_mid_pass_reports_through_the_unproven_channel(
    workspace: Path, tmp_path: Path
) -> None:
    """A late mismatch reports like an early one, not like an ordinary residual.

    The entry check held, so these paths leave by the ordinary return. They
    still name a workspace the command never wrote to, and §14.14 rule 4's
    existence re-check would drop every one of them for being absent there.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")
    _plant_assessment_set(workspace, "snapshot_vera_0002")
    real_remove_entry = managed._remove_entry
    moved: list[Path] = []

    def replace_after_first(path: Path, out_root: Path, still_live=None) -> bool:
        removed = real_remove_entry(path, out_root, still_live)
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="late-mismatch"))
        return removed

    unproven: list[str] = []
    with anchor_locked_database(workspace):
        with privacy_service.collect_unproven_residuals(unproven):
            managed._remove_entry = replace_after_first
            try:
                residuals = managed.remove_managed_sets_for_locked_database(
                    workspace, snapshot_ids=["snapshot_vera_0001", "snapshot_vera_0002"]
                )
            finally:
                managed._remove_entry = real_remove_entry

    assert residuals
    assert set(unproven) == set(residuals)
    assert not (workspace / "out").exists()


def test_the_preamble_creates_no_parents_in_a_replacement(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating a managed parent mutates the tree exactly as removing one does.

    The gate runs before `_canonical_roots` resolves anything, so a
    replacement landing in between would have both parents created and made
    private in a workspace whose writer lock this command never took.
    """

    (workspace / "out").mkdir(mode=0o700, parents=True, exist_ok=True)
    for parent in ("assessment", "branch"):
        shutil.rmtree(workspace / "out" / parent, ignore_errors=True)
    real_roots = managed._canonical_roots
    moved: list[Path] = []

    def roots_then_replace(target: Path):
        roots = real_roots(target)
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="before-mkdir"))
            (workspace / "out").mkdir(mode=0o700, parents=True)
        return roots

    monkeypatch.setattr(managed, "_canonical_roots", roots_then_replace)

    unproven: list[str] = []
    with anchor_locked_database(workspace):
        with privacy_service.collect_unproven_residuals(unproven):
            residuals = managed.reconcile_managed_outputs(workspace)

    assert residuals == (str((workspace / "out").absolute()),)
    assert unproven == list(residuals)
    assert list((workspace / "out").iterdir()) == []
    assert not (moved[0] / "out" / "assessment").exists()


def test_a_candidate_half_written_when_the_workspace_changes_hands(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every member write reopens the candidate through its absolute path.

    One check before the first write authorizes a pass that spans the whole
    set, so the remaining members would land in a replacement holding the same
    candidate name — and the cleanup would then recurse into that foreign tree
    instead of reporting the half-written set it actually left behind.
    """

    graph = assessment_graph(all_sections=False)
    real_write = managed._write_private_file
    moved: list[Path] = []

    def write_then_replace(path: Path, data: bytes, out_root: Path) -> None:
        real_write(path, data, out_root)
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="mid-candidate"))
            (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
            (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)

    monkeypatch.setattr(managed, "_write_private_file", write_then_replace)

    with anchor_locked_database(workspace):
        with pytest.raises(ManagedOutputIncompleteError) as caught:
            managed.publish_assessment(workspace, graph, clock=lambda: NOW)

    stranded = Path(caught.value.residual_paths[0])
    assert stranded.name.startswith(".exp2res-candidate-")
    assert list((workspace / "out" / "assessment").iterdir()) == []
    surviving = list((moved[0] / "out" / "assessment").glob(".exp2res-candidate-*"))
    assert [path.name for path in surviving] == [stranded.name]


def test_a_refusal_after_the_rollback_move_names_both_stranded_entries(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prior set is already aside, so a refusal here strands two entries.

    Without the rollback in the report the original tree is left with no
    current set and nothing naming what holds its contents.
    """

    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    changed = graph_with_gap_answered(graph, True)
    real_rename = managed._rename
    moved: list[Path] = []

    def rename_then_replace(source: Path, destination: Path) -> None:
        real_rename(source, destination)
        if not moved and ".exp2res-rollback-" in destination.name:
            moved.append(_replace_workspace(workspace, tmp_path, name="after-rollback"))
            (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
            (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)

    monkeypatch.setattr(managed, "_rename", rename_then_replace)

    with anchor_locked_database(workspace):
        with pytest.raises(ManagedOutputIncompleteError) as caught:
            managed.publish_assessment(workspace, changed, clock=lambda: NOW)

    names = [Path(path).name for path in caught.value.residual_paths]
    assert any(name.startswith(".exp2res-rollback-") for name in names)
    assert any(name.startswith(".exp2res-candidate-") for name in names)
    stranded = moved[0] / "out" / "assessment"
    assert [path.name for path in sorted(stranded.iterdir())] == sorted(names)


def test_an_idempotent_reexport_never_reports_success_from_a_replacement(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The closing flush reopens the parent by pathname.

    A replacement landing between the candidate cleanup and that flush means
    the flush succeeded against another tree: the cleanup was never made
    durable, and the paths about to be returned name a set this command did
    not put there.
    """

    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    real_fsync_directory = managed._fsync_directory
    moved: list[Path] = []

    def flush_after_replacement(path: Path, out_root: Path) -> None:
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="before-flush"))
            shutil.copytree(
                moved[0] / "out", workspace / "out", symlinks=False
            )
        real_fsync_directory(path, out_root)

    monkeypatch.setattr(managed, "_fsync_directory", flush_after_replacement)

    with anchor_locked_database(workspace):
        with pytest.raises(ManagedOutputIncompleteError):
            managed.publish_assessment(workspace, graph, clock=lambda: NOW)


def test_a_nonconforming_id_is_refused_even_on_the_mismatch_report(
    workspace: Path, tmp_path: Path
) -> None:
    """§13.14 rule 1's revalidation holds on both arms, not only the live one.

    Skipping the ID would let the mutation commit with nothing naming the set
    it left stale — and the mismatch report is the only warning there is.
    """

    with anchor_locked_database(workspace):
        _replace_workspace(workspace, tmp_path, name="invalid-id")
        with pytest.raises(IntegrityFailureError):
            managed.remove_managed_sets_for_locked_database(
                workspace, snapshot_ids=["Snapshot/Vera/0001"]
            )


def test_a_first_time_export_never_validates_a_replacement_into_success(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing asks again after the promotion when there is no rollback.

    Both flushes reopen the parent by pathname and the closing validation reads
    the final set the same way, so a replacement holding a matching set answers
    all three — and the export would report a workspace whose lock it never
    held while its own published set stayed stranded.
    """

    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    donor = tmp_path / "donor"
    shutil.copytree(workspace / "out", donor)
    shutil.rmtree(workspace / "out" / "assessment" / graph.snapshot.value.id)

    real_fsync_directory = managed._fsync_directory
    promoted: list[Path] = []

    def flush_after_promotion(path: Path, out_root: Path) -> None:
        final = workspace / "out" / "assessment" / graph.snapshot.value.id
        if not promoted and final.is_dir():
            promoted.append(_replace_workspace(workspace, tmp_path, name="post-rename"))
            shutil.copytree(donor, workspace / "out")
        real_fsync_directory(path, out_root)

    monkeypatch.setattr(managed, "_fsync_directory", flush_after_promotion)

    with anchor_locked_database(workspace):
        with pytest.raises(ManagedOutputIncompleteError) as caught:
            managed.publish_assessment(workspace, graph, clock=lambda: NOW)

    assert caught.value.residual_paths == (
        str(workspace / "out" / "assessment" / graph.snapshot.value.id),
    )
    assert (
        promoted[0] / "out" / "assessment" / graph.snapshot.value.id
    ).is_dir()


def test_a_replaced_managed_root_is_a_mismatch_even_with_the_database_in_place(
    workspace: Path, tmp_path: Path
) -> None:
    """The database identity alone leaves one substitution uncovered.

    `out/` renamed and replaced while the database stays put would have the
    pass remove from and publish into the replacement, and unlike a
    whole-workspace replacement no later check would notice.
    """

    planted = _plant_assessment_set(workspace, "snapshot_vera_0001")
    _plant_assessment_set(workspace, "snapshot_vera_0002")

    with anchor_locked_database(workspace):
        still_live = managed.locked_workspace_predicate(workspace)
        assert still_live()
        moved = tmp_path / "detached-out"
        shutil.move(str(workspace / "out"), str(moved))
        (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
        (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
        assert not still_live()
        residuals = managed.remove_managed_sets_for_locked_database(
            workspace, snapshot_ids=["snapshot_vera_0001", "snapshot_vera_0002"]
        )

    assert residuals == (
        str(workspace / "out" / "assessment" / "snapshot_vera_0001"),
        str(workspace / "out" / "assessment" / "snapshot_vera_0002"),
    )
    assert (moved / "assessment" / "snapshot_vera_0001").is_dir()
    assert (moved / "assessment" / "snapshot_vera_0002").is_dir()
    assert not planted.exists()


def test_a_candidate_hidden_by_a_replacement_is_reported_not_assumed_gone(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absence is a finished cleanup only where the pass actually built.

    Under a failed binding the pathname reaches a tree that never held this
    candidate, so the fast path would let the write failure escape with
    nothing naming the partial candidate left in the original.
    """

    graph = assessment_graph(all_sections=False)
    real_write = managed._write_private_file
    moved: list[Path] = []

    def write_then_replace_and_fail(path: Path, data: bytes, out_root: Path) -> None:
        real_write(path, data, out_root)
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="hidden"))
            (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
            (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
            raise OSError("Vera Example injected write failure")

    monkeypatch.setattr(managed, "_write_private_file", write_then_replace_and_fail)

    with anchor_locked_database(workspace):
        with pytest.raises(ManagedOutputIncompleteError) as caught:
            managed.publish_assessment(workspace, graph, clock=lambda: NOW)

    stranded = Path(caught.value.residual_paths[0])
    assert stranded.name.startswith(".exp2res-candidate-")
    surviving = list((moved[0] / "out" / "assessment").glob(".exp2res-candidate-*"))
    assert [path.name for path in surviving] == [stranded.name]


def test_a_parent_the_lock_never_saw_is_not_adopted_by_appearing(
    workspace: Path, tmp_path: Path
) -> None:
    """A reserved parent absent at the lock binds to this command's own mkdir.

    Adopting whatever answers to the name at the next check would hand the
    binding to any directory that appeared meanwhile — the same substitution
    one level down, carried out with this command's authority.
    """

    for parent in ("assessment", "branch"):
        if (workspace / "out" / parent).is_dir():
            shutil.rmtree(workspace / "out" / parent)

    with anchor_locked_database(workspace):
        still_live = managed.locked_workspace_predicate(workspace)
        assert still_live()
        (workspace / "out" / "assessment").mkdir(mode=0o700)
        assert not still_live()

    with anchor_locked_database(workspace):
        # The same directory, now established by the lock that found it.
        assert managed.locked_workspace_predicate(workspace)()


def test_the_parents_this_command_creates_are_the_ones_it_binds_to(
    workspace: Path, tmp_path: Path
) -> None:
    """A first publication into a workspace without reserved parents proceeds.

    The refusal above is about an entry this command did not make; its own
    creation step records what it made, so the export that follows is bound to
    those entries rather than blocked by their absence at the lock.
    """

    for parent in ("assessment", "branch"):
        if (workspace / "out" / parent).is_dir():
            shutil.rmtree(workspace / "out" / parent)
    graph = assessment_graph(all_sections=False)

    with anchor_locked_database(workspace):
        manifest, members = managed.publish_assessment(
            workspace, graph, clock=lambda: NOW
        )

    published = workspace / "out" / "assessment" / manifest.entity_id
    assert published.is_dir()
    assert {str(path) for path in published.iterdir()} == set(members)


def test_a_parent_replaced_between_its_creation_and_the_record_binds_nothing(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The creation records the entry it held open, not the name it was given.

    A pathname restatted after the descriptor closes answers about whatever
    holds the name by then, so a replacement landing in that window would be
    installed as the very identity the binding trusts.
    """

    for parent in ("assessment", "branch"):
        if (workspace / "out" / parent).is_dir():
            shutil.rmtree(workspace / "out" / parent)
    real_open = managed.os.open
    swapped: list[Path] = []
    assessment = workspace / "out" / "assessment"

    def replace_after_the_descriptor_closes(*arguments, **keywords):
        descriptor = real_open(*arguments, **keywords)
        if not swapped and arguments[0] == "assessment":
            aside = tmp_path / "detached-assessment"
            shutil.move(str(assessment), str(aside))
            assessment.mkdir(mode=0o700)
            swapped.append(aside)
        return descriptor

    monkeypatch.setattr(managed.os, "open", replace_after_the_descriptor_closes)

    with anchor_locked_database(workspace):
        still_live = managed.locked_workspace_predicate(workspace)
        with pytest.raises(ManagedOutputIncompleteError):
            managed._ensure_managed_parents(workspace, still_live=still_live)
        assert not still_live()


def test_a_candidate_stranded_by_its_own_creation_is_reported(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The creation arm validates the pathname after making the entry.

    A binding that failed in between leaves the made candidate in the tree this
    pass built in, while the name reaches the other one — so an existence probe
    there is no more proof of a finished cleanup than it is further down.
    """

    graph = assessment_graph(all_sections=False)
    real_validate = managed._validate_existing_path
    moved: list[Path] = []

    def replace_then_refuse(path: Path, out_root: Path, *, directory: bool) -> None:
        if not moved and ".exp2res-candidate-" in path.name:
            moved.append(_replace_workspace(workspace, tmp_path, name="mid-mkdir"))
            (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
            (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
            raise OSError("Vera Example injected validation failure")
        real_validate(path, out_root, directory=directory)

    monkeypatch.setattr(managed, "_validate_existing_path", replace_then_refuse)

    with anchor_locked_database(workspace):
        with pytest.raises(ManagedOutputIncompleteError) as caught:
            managed.publish_assessment(workspace, graph, clock=lambda: NOW)

    stranded = Path(caught.value.residual_paths[0])
    assert stranded.name.startswith(".exp2res-candidate-")
    surviving = list((moved[0] / "out" / "assessment").glob(".exp2res-candidate-*"))
    assert [path.name for path in surviving] == [stranded.name]


def test_a_rollback_move_survives_a_flush_that_never_reaches_the_next_gate(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename is a fact; the probes that follow it are not.

    A replacement landing between the rollback move and its flush leaves the
    prior set aside in the original tree under a name nothing points at, and
    every later existence probe answers about the replacement instead.
    """

    graph = assessment_graph(all_sections=False)
    _publish(workspace, graph)
    changed = graph_with_gap_answered(graph, True)
    real_flush = managed._fsync_directory
    moved: list[Path] = []

    def replace_then_fail(path: Path, out_root: Path) -> None:
        rollbacks = list(path.glob(".exp2res-rollback-*")) if path.is_dir() else []
        if not moved and rollbacks:
            moved.append(_replace_workspace(workspace, tmp_path, name="mid-rollback"))
            (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
            (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
            raise OSError("Vera Example injected flush failure")
        real_flush(path, out_root)

    monkeypatch.setattr(managed, "_fsync_directory", replace_then_fail)

    unproven: list[str] = []
    with anchor_locked_database(workspace):
        with privacy_service.collect_unproven_residuals(unproven):
            with pytest.raises(ManagedOutputIncompleteError) as caught:
                managed.publish_assessment(workspace, changed, clock=lambda: NOW)

    names = [Path(path).name for path in caught.value.residual_paths]
    assert any(name.startswith(".exp2res-rollback-") for name in names)
    assert any(name.startswith(".exp2res-candidate-") for name in names)
    # §13.14 rule 9: a mismatch report is never withdrawn by a later existence
    # check against the tree the pathname now reaches.
    assert {Path(path).name for path in unproven} == set(names)
    stranded = moved[0] / "out" / "assessment"
    assert set(names) <= {path.name for path in stranded.iterdir()}


def test_an_interrupted_removal_still_names_the_sets_a_mismatch_stranded(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled pass reports through the unwithdrawable channel too.

    The tail that marks a mid-pass mismatch unproven is never reached when the
    pass is interrupted, and the CLI would then existence-filter the pending
    report against the replacement and drop every stranded set.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")
    _plant_assessment_set(workspace, "snapshot_vera_0002")
    real_remove = managed._remove_entry
    moved: list[Path] = []

    def replace_then_cancel(*arguments, **keywords):
        if not moved:
            moved.append(_replace_workspace(workspace, tmp_path, name="cancelled"))
            (workspace / "out" / "assessment").mkdir(mode=0o700, parents=True)
            (workspace / "out" / "branch").mkdir(mode=0o700, parents=True)
            raise KeyboardInterrupt()
        return real_remove(*arguments, **keywords)

    unproven: list[str] = []
    with anchor_locked_database(workspace):
        with privacy_service.collect_unproven_residuals(unproven):
            monkeypatch.setattr(managed, "_remove_entry", replace_then_cancel)
            with pytest.raises(KeyboardInterrupt):
                managed.remove_managed_sets_for_locked_database(
                    workspace,
                    snapshot_ids=["snapshot_vera_0001", "snapshot_vera_0002"],
                )

    assert unproven == [
        str(workspace / "out" / "assessment" / "snapshot_vera_0001"),
        str(workspace / "out" / "assessment" / "snapshot_vera_0002"),
    ]
    stranded = moved[0] / "out" / "assessment"
    assert sorted(path.name for path in stranded.iterdir()) == [
        "snapshot_vera_0001",
        "snapshot_vera_0002",
    ]


def test_an_unreadable_managed_root_is_a_failed_binding_not_a_raise(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The predicate runs inside handlers that owe a residual report.

    An ancestor that becomes unsearchable mid-pass would otherwise raise from
    here and replace that report — or a cancellation — with an internal error
    over a transaction the caller has already committed.
    """

    _plant_assessment_set(workspace, "snapshot_vera_0001")
    real_lstat = managed._lstat
    refused = [False]

    def refuse_the_parent(path: Path):
        if not refused[0] and path.name == "assessment":
            refused[0] = True
            raise PermissionError(13, "Permission denied")
        return real_lstat(path)

    with anchor_locked_database(workspace):
        still_live = managed.locked_workspace_predicate(workspace)
        assert still_live()
        monkeypatch.setattr(managed, "_lstat", refuse_the_parent)
        assert not still_live()


def test_a_backup_store_replaced_under_the_lock_is_never_purged(
    workspace: Path, tmp_path: Path
) -> None:
    """§13.14 rule 9 covers the backup store the same way it covers `out/`.

    Matching the open descriptor back to its name proves only that the name
    still reaches it, which a replacement satisfies as readily as the original.
    """

    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = backup_root / "exp2res-v11-20260817T000000.000000Z.sqlite"
    original.write_bytes(b"Vera Example migration backup")
    identity = privacy_service.locked_database_identity(workspace)

    with anchor_locked_database(workspace):
        detached = tmp_path / "detached-backup"
        shutil.move(str(backup_root), str(detached))
        backup_root.mkdir(mode=0o700)
        planted = backup_root / "exp2res-v11-20260818T000000.000000Z.sqlite"
        planted.write_bytes(b"Vera Example replacement backup")
        removed, residuals = privacy_service.purge_managed_backups(
            workspace, expected_database=identity
        )

    assert removed == ()
    assert residuals == (str(backup_root.absolute()),)
    survivor = detached / original.name
    assert survivor.read_bytes() == b"Vera Example migration backup"
    assert planted.read_bytes() == b"Vera Example replacement backup"


def test_a_backup_store_moved_away_under_the_lock_is_reported_residual(
    workspace: Path, tmp_path: Path
) -> None:
    """A store the lock recorded never reads as absence.

    Renamed aside with nothing put back, the pathname answers `ENOENT`; taking
    that as nothing left to do would commit the deletion and report success
    while the detached store still holds the backups it had to remove.
    """

    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = backup_root / "exp2res-v11-20260817T000000.000000Z.sqlite"
    original.write_bytes(b"Vera Example migration backup")
    identity = privacy_service.locked_database_identity(workspace)

    with anchor_locked_database(workspace):
        detached = tmp_path / "detached-backup"
        shutil.move(str(backup_root), str(detached))
        removed, residuals = privacy_service.purge_managed_backups(
            workspace, expected_database=identity
        )

    assert removed == ()
    assert residuals == (str(backup_root.absolute()),)
    survivor = detached / original.name
    assert survivor.read_bytes() == b"Vera Example migration backup"


def test_a_backup_store_absent_at_the_lock_is_still_nothing_to_purge(
    workspace: Path,
) -> None:
    """The reverse case stays quiet: no store recorded, none to report."""

    identity = privacy_service.locked_database_identity(workspace)
    backup_root = workspace / ".exp2res" / "backup"
    assert not backup_root.exists()

    with anchor_locked_database(workspace):
        removed, residuals = privacy_service.purge_managed_backups(
            workspace, expected_database=identity
        )

    assert removed == ()
    assert residuals == ()


def test_a_parent_appearing_after_the_gate_is_refused_before_its_mode_changes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller's gate cannot cover what appears after it ran.

    A reserved parent the lock never saw, created by something else in that
    window, would otherwise be adopted as pre-existing and made private by a
    command holding no authority over it — the refusal landing only after the
    mutation it was meant to prevent.
    """

    parent = workspace / "out" / "assessment"
    if parent.is_dir():
        shutil.rmtree(parent)
    real_open = managed._open_directory_fd
    intruded: list[bool] = []

    def create_then_open(path: Path, out_root: Path) -> int:
        descriptor = real_open(path, out_root)
        if not intruded and path.name == "out":
            intruded.append(True)
            os.mkdir(parent, 0o755)
        return descriptor

    with anchor_locked_database(workspace):
        still_live = managed.locked_workspace_predicate(workspace)
        monkeypatch.setattr(managed, "_open_directory_fd", create_then_open)
        with pytest.raises(ManagedOutputIncompleteError):
            managed._ensure_managed_parents(workspace, still_live=still_live)

    assert intruded == [True]
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755


def test_a_symlinked_marker_never_hands_back_a_lock(
    workspace: Path, tmp_path: Path
) -> None:
    """§8.1 rule 37 reads the database beside the entry the lock was taken on.

    A `.exp2res` swapped for a link to another workspace would otherwise hand
    back that workspace's lock and database while the managed roots still came
    from the requested pathname, so the business mutation would land in a tree
    this command never locked.
    """

    marker = workspace / ".exp2res"
    detached = tmp_path / "detached-marker"
    shutil.move(str(marker), str(detached))
    marker.symlink_to(detached, target_is_directory=True)

    with pytest.raises(SchemaCompatibilityError):
        with workspace_module.writer_lock(workspace):
            pass


def test_a_purge_that_loses_its_binding_reports_nothing_removed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removed names are pathnames, and a substitution re-points them.

    Reporting the ones already unlinked would tell the owner that files still
    sitting untouched in the tree the command now looks at had been deleted.
    """

    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    names = (
        "exp2res-v11-20260817T000000.000000Z.sqlite",
        "exp2res-v11-20260818T000000.000000Z.sqlite",
    )
    for name in names:
        (backup_root / name).write_bytes(b"Vera Example migration backup")
    identity = privacy_service.locked_database_identity(workspace)
    ledger = ["out/assessment/snapshot_vera_0001"]
    real_fsync = os.fsync
    swapped: list[Path] = []

    def swap_after_the_first_flush(descriptor: int) -> None:
        real_fsync(descriptor)
        if swapped:
            return
        detached = tmp_path / "detached-backup"
        shutil.move(str(backup_root), str(detached))
        swapped.append(detached)
        backup_root.mkdir(mode=0o700)
        for name in names:
            (backup_root / name).write_bytes(b"Vera Example replacement backup")

    with anchor_locked_database(workspace):
        monkeypatch.setattr(privacy_service.os, "fsync", swap_after_the_first_flush)
        removed, residuals = privacy_service.purge_managed_backups(
            workspace, expected_database=identity, removed_ledger=ledger
        )

    assert swapped
    assert removed == ()
    assert residuals == (str(backup_root.absolute()),)
    assert ledger == ["out/assessment/snapshot_vera_0001"]
    for name in names:
        assert (backup_root / name).read_bytes() == b"Vera Example replacement backup"
