"""Raw-layer authority, owner deletion, and backup/restore tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from exp2res.services.capture import capture_daily
from exp2res.services.logs import delete_log, list_logs, show_log
from exp2res.domain.models import EvidenceItem, RawLog
from exp2res.storage.repository import insert_evidence_item, insert_raw_log
from exp2res.storage.workspace import initialize_workspace, writer_database

from conftest import FIXED_NOW, configure_timezone


def test_automation_cannot_rewrite_or_delete_but_owner_deletion_cascades(
    workspace: Path,
) -> None:
    """§21.11 / §21.14; §24.3 / §24.17: actor-scoped raw authority."""
    marker = "Vera Example immutable raw sentinel"
    bundle = capture_daily(workspace, raw_text=marker, clock=lambda: FIXED_NOW)
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "UPDATE raw_logs SET raw_text = ? WHERE id = ?",
                ("Vera Example forbidden rewrite", bundle.raw_log.id),
            )
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM raw_logs WHERE id = ?", (bundle.raw_log.id,))
    assert show_log(workspace, log_id=bundle.raw_log.id).raw_log.raw_text == marker

    outcome = delete_log(workspace, log_id=bundle.raw_log.id)
    assert outcome.residual_paths == ()
    assert list_logs(workspace) == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 0
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
    for path in (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    ):
        if path.exists():
            assert marker.encode("utf-8") not in path.read_bytes()


def test_delete_preserves_external_source_and_does_not_follow_backup_symlink(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.14; §24.17: database deletion commits despite safe cleanup residual."""
    external = tmp_path / "Vera Example source.md"
    external.write_text("Vera Example external source remains owner-controlled\n", encoding="utf-8")
    from exp2res.services.capture import capture_daily_file

    bundle = capture_daily_file(
        workspace, source_path=str(external), clock=lambda: FIXED_NOW
    )
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700)
    outside = tmp_path / "Vera Example outside backup.sqlite"
    outside.write_text("Vera Example outside target\n", encoding="utf-8")
    planted = backup_root / "exp2res-v1-Vera-Example.sqlite"
    planted.symlink_to(outside)

    outcome = delete_log(workspace, log_id=bundle.raw_log.id)
    assert list_logs(workspace) == ()
    assert external.exists()
    assert outside.read_text(encoding="utf-8") == "Vera Example outside target\n"
    assert planted.exists()
    assert outcome.residual_paths == (str(planted.absolute()),)


def test_owner_delete_re_roots_retained_correction_without_fk_block(
    workspace: Path,
) -> None:
    """§21.11; §24.3: ON DELETE SET NULL cannot block selected raw deletion."""
    target = capture_daily(
        workspace,
        raw_text="Vera Example original record",
        clock=lambda: FIXED_NOW,
    )
    correction = RawLog(
        id="log_vera_correction",
        recorded_at=FIXED_NOW.replace(hour=13),
        entry_type="correction",
        source_type="manual_entry",
        occurred=target.raw_log.occurred,
        raw_text="Vera Example self-contained corrected record",
        project=None,
        external_ref=None,
        corrects_log_id=target.raw_log.id,
        metadata={},
    )
    evidence = EvidenceItem(
        id="evi_vera_correction",
        created_at=correction.recorded_at,
        raw_log_id=correction.id,
        title=None,
        summary="Owner-authored manual claim.",
        uri=None,
        path=None,
        strength="manual_claim",
        metadata={},
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        insert_raw_log(connection, correction)
        insert_evidence_item(connection, evidence)
        connection.commit()

    delete_log(workspace, log_id=target.raw_log.id)
    retained = show_log(workspace, log_id=correction.id)
    assert retained.raw_log.corrects_log_id is None
    assert retained.raw_log.raw_text == correction.raw_text
    assert retained.evidence_items == (evidence,)


def test_sqlite_backup_restore_preserves_phase0_records(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.36; §24.39: WAL-aware SQLite backup/restore preserves Stage 1 rows."""
    bundle = capture_daily(
        workspace,
        raw_text="Vera Example backup and restore record",
        project="K8s Playbook",
        clock=lambda: FIXED_NOW,
    )
    backup = tmp_path / "Vera Example external backup.sqlite"
    source_db = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(source_db) as source, sqlite3.connect(backup) as target:
        source.backup(target)

    restored = tmp_path / "restored-private-workspace"
    restored.mkdir()
    initialize_workspace(restored, clock=lambda: FIXED_NOW)
    configure_timezone(restored)
    restored_db = restored / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(backup) as source, sqlite3.connect(restored_db) as target:
        source.backup(target)

    restored_bundle = show_log(restored, log_id=bundle.raw_log.id)
    assert restored_bundle.raw_log == bundle.raw_log
    assert restored_bundle.evidence_items == bundle.evidence_items
