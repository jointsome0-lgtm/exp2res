"""Offline §14.5 / §14.14 import CLI coverage."""

from __future__ import annotations

from contextlib import contextmanager
import functools
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.services.capture as capture_service
import exp2res.services.imports as imports_service
from exp2res.cli import app
from exp2res.errors import IdCollisionError
from exp2res.storage.repository import insert_evidence_item, insert_raw_log

from test_imports_phase5 import (
    atlas_record,
    ephemeris_record,
    github_record,
    write_payload,
)


pytestmark = [pytest.mark.unit, pytest.mark.lifecycle]
runner = CliRunner()


def _invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app, ["--json", "--workspace", str(workspace), *arguments]
    )
    return result, json.loads(result.stdout)


def _invoke_human(workspace: Path, arguments: list[str]):
    return runner.invoke(app, ["--workspace", str(workspace), *arguments])


def test_each_source_form_reports_its_own_command_and_record(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.5: three §19-backed forms, each reading one local payload."""
    payloads = {
        "ephemeris": write_payload(
            tmp_path, "ephemeris.jsonl", [ephemeris_record()]
        ),
        "atlas": write_payload(tmp_path, "atlas.json", atlas_record()),
        "github": write_payload(tmp_path, "github.json", github_record()),
    }
    identities = {
        "ephemeris": "vera-ephemeris-0001",
        "atlas": atlas_record()["record_id"],
        "github": f"vera-example/playbook@{github_record()['commit_sha']}",
    }
    for source_system, payload in payloads.items():
        result, envelope = _invoke_json(
            workspace, ["import", source_system, payload]
        )

        assert result.exit_code == 0
        assert envelope["command"] == f"import {source_system}"
        assert envelope["status"] == "ok"
        assert envelope["diagnostic_class"] is None
        assert envelope["result"]["counts"] == {
            "accepted": 1,
            "duplicate": 0,
            "rejected": 0,
        }
        accepted = envelope["result"]["records"]["accepted"]
        assert len(accepted) == 1
        assert accepted[0]["record_number"] == 1
        assert accepted[0]["source_record_id"] == identities[source_system]
        assert accepted[0]["raw_log_id"] is not None
        assert envelope["result"]["records"]["duplicate"] == []
        assert envelope["result"]["records"]["rejected"] == []
        # §13.1 rule 5's created pair reaches the envelope's own id report.
        created = {
            group["entity_type"]: group["ids"]
            for group in envelope["affected_ids"]["created"]
        }
        assert created["raw_log"] == [accepted[0]["raw_log_id"]]
        assert len(created["evidence_item"]) == 1


def test_a_repeated_record_is_a_duplicate_with_no_raw_log_id(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 5: `raw_log_id` is non-null exactly for a committed record."""
    payload = write_payload(tmp_path, "replay.jsonl", [ephemeris_record()])
    first, _ = _invoke_json(workspace, ["import", "ephemeris", payload])
    assert first.exit_code == 0

    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 0
    assert envelope["result"]["counts"] == {
        "accepted": 0,
        "duplicate": 1,
        "rejected": 0,
    }
    duplicate = envelope["result"]["records"]["duplicate"][0]
    assert duplicate == {
        "record_number": 1,
        "source_record_id": "vera-ephemeris-0001",
        "raw_log_id": None,
    }
    # A counted no-op creates nothing, so it reports no created entity group.
    assert envelope["affected_ids"]["created"] == []


def test_a_classified_rejection_exits_class_two_with_its_complete_result(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 5: a nonzero completed report still carries its result."""
    payload = write_payload(
        tmp_path,
        "mixed.jsonl",
        [
            ephemeris_record("vera-ephemeris-0011"),
            ephemeris_record("vera-ephemeris-0012", domain="knowledge_state"),
            ephemeris_record("vera-ephemeris-0013"),
        ],
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 2
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "import_records_rejected"
    assert envelope["result"]["counts"] == {
        "accepted": 2,
        "duplicate": 0,
        "rejected": 1,
    }
    rejected = envelope["result"]["records"]["rejected"]
    assert [record["record_number"] for record in rejected] == [2]
    # The closed projection carries no reason field.
    assert set(rejected[0]) == {"record_number", "source_record_id", "raw_log_id"}
    assert rejected[0]["raw_log_id"] is None
    # The records that committed keep their outcome and their created rows.
    assert [
        record["record_number"]
        for record in envelope["result"]["records"]["accepted"]
    ] == [1, 3]
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 2


def test_more_than_a_hundred_records_are_reported_untruncated(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 5: local stdout is not one of §11's bounded boundaries."""
    total = 130
    records = [
        ephemeris_record(f"vera-ephemeris-1{index:04d}") for index in range(total)
    ]
    payload = write_payload(tmp_path, "many.jsonl", records)
    _invoke_json(workspace, ["import", "ephemeris", payload])
    # The second run replays the first 40 and adds 90 new identities, so all
    # three lists are exercised at once without a rejection.
    extended = records[:40] + [
        ephemeris_record(f"vera-ephemeris-2{index:04d}") for index in range(90)
    ]
    replay = write_payload(tmp_path, "many-again.jsonl", extended)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", replay])

    assert result.exit_code == 0
    counts = envelope["result"]["counts"]
    groups = envelope["result"]["records"]
    assert counts == {"accepted": 90, "duplicate": 40, "rejected": 0}
    for name, count in counts.items():
        assert len(groups[name]) == count
    numbers = [
        record["record_number"]
        for name in ("accepted", "duplicate", "rejected")
        for record in groups[name]
    ]
    # The three lists partition every one-based record number exactly once.
    assert sorted(numbers) == list(range(1, len(extended) + 1))
    for name in ("accepted", "duplicate"):
        assert [record["record_number"] for record in groups[name]] == sorted(
            record["record_number"] for record in groups[name]
        )


def test_a_payload_without_a_record_boundary_reports_a_null_result(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 5: too early for a record means no primary result at all."""
    path = tmp_path / "atlas.json"
    path.write_text("Vera Example not JSON\n", encoding="utf-8")
    result, envelope = _invoke_json(workspace, ["import", "atlas", str(path)])

    assert result.exit_code == 2
    assert envelope["command"] == "import atlas"
    assert envelope["diagnostic_class"] == "import_payload_invalid"
    assert envelope["result"] is None


def test_human_mode_prints_one_line_for_every_record(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 5: human mode may format the primary result as text."""
    payload = write_payload(
        tmp_path,
        "human.jsonl",
        [
            ephemeris_record("vera-ephemeris-0021"),
            ephemeris_record("vera-ephemeris-0022", project="   "),
        ],
    )
    result = _invoke_human(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 2
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "accepted 1, duplicate 0, rejected 1"
    assert lines[1].startswith("1\taccepted\tvera-ephemeris-0021\t")
    assert lines[2] == "2\trejected\tvera-ephemeris-0022\t-"


def test_an_empty_multi_record_payload_is_a_complete_zero_classification(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 5: zero established records is a completed classification.

    An exporter with nothing to hand over for its window establishes no
    record boundary and no failure: counts equal their list lengths, the
    three lists partition every established record, and rule 4's
    same-payload retry unit converges. Turning that into an error would
    fail an honest empty export, so it stays exit 0 with a complete result.
    """
    path = tmp_path / "empty.jsonl"
    path.write_text("\n   \n", encoding="utf-8")
    result, envelope = _invoke_json(
        workspace, ["import", "ephemeris", str(path)]
    )

    assert result.exit_code == 0
    assert envelope["diagnostic_class"] is None
    assert envelope["result"] == {
        "counts": {"accepted": 0, "duplicate": 0, "rejected": 0},
        "records": {"accepted": [], "duplicate": [], "rejected": []},
    }
    assert envelope["affected_ids"]["created"] == []
    # A single-record form has no such boundary: an empty file decodes to
    # nothing at all and keeps §19.4 rule 5's null result.
    single = tmp_path / "empty.json"
    single.write_text("", encoding="utf-8")
    failed, failed_envelope = _invoke_json(
        workspace, ["import", "atlas", str(single)]
    )
    assert failed.exit_code == 2
    assert failed_envelope["diagnostic_class"] == "import_payload_invalid"
    assert failed_envelope["result"] is None


def test_created_entity_groups_are_ordered_by_their_own_id_identity(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 5: entity groups order by stable identity, not input order."""
    payload = write_payload(
        tmp_path,
        "ordered.jsonl",
        [ephemeris_record(f"vera-ephemeris-3{index:04d}") for index in range(12)],
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 0
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    for entity_type in ("raw_log", "evidence_item"):
        ids = created[entity_type]
        assert len(ids) == 12
        assert ids == sorted(ids, key=lambda value: value.encode("utf-8"))
    # The result records keep their own identity — the input record_number —
    # so the two orderings are genuinely independent.
    accepted = envelope["result"]["records"]["accepted"]
    assert [record["record_number"] for record in accepted] == list(range(1, 13))


def test_a_completed_report_with_a_residual_still_reaches_class_eight(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 4: class 8 covers every non-cancelled completion."""
    candidate = (
        workspace
        / "out"
        / "assessment"
        / (".exp2res-candidate-snapshot_vera_0001-" + "0" * 32)
    )
    candidate.parent.mkdir(parents=True, exist_ok=True)
    # The writer preamble refuses to follow a symlink out of the managed
    # root, so this candidate is reported instead of removed.
    candidate.symlink_to(tmp_path, target_is_directory=True)
    payload = write_payload(
        tmp_path,
        "rejected.jsonl",
        [
            ephemeris_record("vera-ephemeris-0031"),
            ephemeris_record("vera-ephemeris-0032", project="   "),
        ],
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "managed_output_incomplete"
    assert envelope["residual_paths"] == [str(candidate)]
    # The promotion changes the class, never the completed classification.
    assert envelope["result"]["counts"] == {
        "accepted": 1,
        "duplicate": 0,
        "rejected": 1,
    }


def test_an_interrupted_import_reports_the_records_it_already_committed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: a committed §19.4 rule 4 transaction is reported."""
    payload = write_payload(
        tmp_path,
        "interrupted.jsonl",
        [ephemeris_record(f"vera-ephemeris-4{index:04d}") for index in range(4)],
    )
    persist = imports_service._persist
    calls = {"count": 0}

    def interrupt_after_two(*args, **kwargs):
        if calls["count"] == 2:
            raise KeyboardInterrupt()
        calls["count"] += 1
        return persist(*args, **kwargs)

    monkeypatch.setattr(imports_service, "_persist", interrupt_after_two)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["diagnostic_class"] == "cancelled"
    # The classification never completed, so rule 5 emits no partial result.
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 2
    assert len(created["evidence_item"]) == 2
    monkeypatch.undo()
    # The committed rows are durable, so replaying the payload converges.
    replayed, replayed_envelope = _invoke_json(
        workspace, ["import", "ephemeris", payload]
    )
    assert replayed.exit_code == 0
    assert replayed_envelope["result"]["counts"] == {
        "accepted": 2,
        "duplicate": 2,
        "rejected": 0,
    }


def test_a_commit_the_signal_outran_is_still_reported(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the workspace, not the classifier, is the authority."""
    payload = write_payload(
        tmp_path,
        "outran.jsonl",
        [ephemeris_record(f"vera-ephemeris-5{index:04d}") for index in range(3)],
    )
    persist = imports_service._persist

    def interrupt_after_the_commit_returns(*args, **kwargs):
        persist(*args, **kwargs)
        # The narrowest window there is: the record is durable, and the
        # classifier has not filed it anywhere yet.
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        imports_service, "_persist", interrupt_after_the_commit_returns
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 9
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 1
    assert len(created["evidence_item"]) == 1
    monkeypatch.undo()
    replayed, replayed_envelope = _invoke_json(
        workspace, ["import", "ephemeris", payload]
    )
    # The reported row is exactly the one that survived the interruption.
    assert replayed_envelope["result"]["counts"] == {
        "accepted": 2,
        "duplicate": 1,
        "rejected": 0,
    }
    assert replayed.exit_code == 0


def test_a_rolled_back_candidate_is_never_reported_as_committed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate whose transaction rolled back left no lifecycle boundary."""
    payload = write_payload(
        tmp_path,
        "rolled-back.jsonl",
        [ephemeris_record(f"vera-ephemeris-6{index:04d}") for index in range(3)],
    )
    persist = imports_service._persist
    calls = {"count": 0}

    def interrupt_inside_the_transaction(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return persist(*args, **kwargs)
        # The candidate was registered before this call, and its transaction
        # never opened: it must not be reported as a committed boundary.
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        imports_service, "_persist", interrupt_inside_the_transaction
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 9
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    # Exactly the first record: the second never reached a commit.
    assert len(created["raw_log"]) == 1


def test_human_mode_renders_the_committed_records_of_a_cancelled_import(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: human mode reads no `affected_ids`, so it needs lines."""
    payload = write_payload(
        tmp_path,
        "cancelled-human.jsonl",
        [ephemeris_record(f"vera-ephemeris-7{index:04d}") for index in range(3)],
    )
    persist = imports_service._persist
    calls = {"count": 0}

    def interrupt_after_two(*args, **kwargs):
        if calls["count"] == 2:
            raise KeyboardInterrupt()
        calls["count"] += 1
        return persist(*args, **kwargs)

    monkeypatch.setattr(imports_service, "_persist", interrupt_after_two)
    result = _invoke_human(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 9
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "cancelled, 2 committed"
    assert [line.split("\t")[:3] for line in lines[1:]] == [
        ["1", "accepted", "vera-ephemeris-70000"],
        ["2", "accepted", "vera-ephemeris-70001"],
    ]
    for line in lines[1:]:
        assert line.split("\t")[3].startswith("log_")


def test_an_interrupt_in_result_assembly_keeps_the_complete_result(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5 nulls a result only when none exists yet."""
    payload = write_payload(
        tmp_path,
        "assembly.jsonl",
        [ephemeris_record(f"vera-ephemeris-8{index:04d}") for index in range(2)],
    )

    def interrupt_projection(_imported):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_module, "_import_outcome", interrupt_projection)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["result"]["counts"] == {
        "accepted": 2,
        "duplicate": 0,
        "rejected": 0,
    }
    assert len(envelope["affected_ids"]["created"]) == 2


def test_a_collided_candidate_never_borrows_the_retained_row_as_its_commit(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retried ID belongs to a row that already existed, not to this run."""
    seeded = write_payload(
        tmp_path, "seed.jsonl", [ephemeris_record("vera-ephemeris-9001")]
    )
    _, seed_envelope = _invoke_json(workspace, ["import", "ephemeris", seeded])
    taken = seed_envelope["result"]["records"]["accepted"][0]["raw_log_id"]

    payload = write_payload(
        tmp_path, "collide.jsonl", [ephemeris_record("vera-ephemeris-9002")]
    )
    issued = {"count": 0}

    def collide_once(kind: str) -> str:
        if kind == "raw_log":
            issued["count"] += 1
            if issued["count"] == 1:
                return taken
        return capture_service.new_id(kind)

    monkeypatch.setattr(cli_module, "import_payload", functools.partial(
        imports_service.import_payload, id_factory=collide_once
    ))
    persist = imports_service._persist

    def interrupt_after_the_collision(*args, **kwargs):
        if issued["count"] > 1:
            raise KeyboardInterrupt()
        return persist(*args, **kwargs)

    monkeypatch.setattr(imports_service, "_persist", interrupt_after_the_collision)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])

    assert result.exit_code == 9
    # The retained row is not this payload's commit, so nothing is reported.
    assert envelope["affected_ids"]["created"] == []


def test_an_interrupt_before_the_commit_lands_reports_no_row(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An open transaction is uncommitted, whatever the connection can see."""
    payload = write_payload(
        tmp_path, "uncommitted.jsonl", [ephemeris_record("vera-ephemeris-9101")]
    )

    def insert_then_interrupt(connection, *, raw_log, evidence_items):
        # The rows exist on this connection and are visible to it, and the
        # transaction is still open when the signal escapes.
        connection.execute("BEGIN IMMEDIATE")
        insert_raw_log(connection, raw_log)
        for evidence_item in evidence_items:
            insert_evidence_item(connection, evidence_item)
        raise KeyboardInterrupt()

    monkeypatch.setattr(imports_service, "_persist", insert_then_interrupt)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 9
    assert envelope["affected_ids"]["created"] == []
    # Nothing durable survived, so a replay accepts the record outright.
    replayed, replayed_envelope = _invoke_json(
        workspace, ["import", "ephemeris", payload]
    )
    assert replayed.exit_code == 0
    assert replayed_envelope["result"]["counts"]["accepted"] == 1


def test_an_interrupt_in_writer_teardown_still_reports_the_committed_rows(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: lock release is outside the loop, not outside the report."""
    payload = write_payload(
        tmp_path,
        "teardown.jsonl",
        [ephemeris_record(f"vera-ephemeris-92{index:02d}") for index in range(2)],
    )
    writer = imports_service.writer_database

    @contextmanager
    def interrupt_on_release(*args, **kwargs):
        with writer(*args, **kwargs) as connection:
            yield connection
        # Connection close and §8.1 lock release have both happened.
        raise KeyboardInterrupt()

    monkeypatch.setattr(imports_service, "writer_database", interrupt_on_release)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 9
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 2
    # The classification completed before teardown, so it is reported in full.
    assert envelope["result"]["counts"] == {
        "accepted": 2,
        "duplicate": 0,
        "rejected": 0,
    }


def test_a_classified_failure_keeps_the_records_committed_behind_it(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§19.4 rule 4: a later failure never withdraws an accepted record."""
    payload = write_payload(
        tmp_path,
        "failing.jsonl",
        [ephemeris_record(f"vera-ephemeris-93{index:02d}") for index in range(3)],
    )
    persist = imports_service._persist
    calls = {"count": 0}

    def fail_after_two(*args, **kwargs):
        if calls["count"] == 2:
            raise IdCollisionError()
        calls["count"] += 1
        return persist(*args, **kwargs)

    monkeypatch.setattr(imports_service, "_persist", fail_after_two)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 7
    assert envelope["diagnostic_class"] == "id_collision"
    # The classification never completed, so no result — but the durable rows
    # behind the failure are still reported.
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 2
    replayed, replayed_envelope = _invoke_json(
        workspace, ["import", "ephemeris", payload]
    )
    assert replayed_envelope["result"]["counts"] == {
        "accepted": 1,
        "duplicate": 2,
        "rejected": 0,
    }
    assert replayed.exit_code == 0


def test_a_non_busy_database_failure_keeps_the_records_committed_behind_it(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§19.4 rule 4: a full disk fails its own record, not the durable ones."""
    payload = write_payload(
        tmp_path,
        "full-disk.jsonl",
        [ephemeris_record(f"vera-ephemeris-94{index:02d}") for index in range(3)],
    )
    persist = imports_service._persist
    calls = {"count": 0}

    def fail_after_two(*args, **kwargs):
        if calls["count"] == 2:
            raise sqlite3.OperationalError("database or disk is full")
        calls["count"] += 1
        return persist(*args, **kwargs)

    monkeypatch.setattr(imports_service, "_persist", fail_after_two)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    # The exit taxonomy is unchanged: a non-busy operational failure stays
    # §14.14 class 1, and the incomplete classification carries no result.
    assert result.exit_code == 1
    assert envelope["diagnostic_class"] == "internal_error"
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 2
    replayed, replayed_envelope = _invoke_json(
        workspace, ["import", "ephemeris", payload]
    )
    assert replayed.exit_code == 0
    assert replayed_envelope["result"]["counts"] == {
        "accepted": 1,
        "duplicate": 2,
        "rejected": 0,
    }


def test_an_interrupt_before_the_writer_opens_reports_no_classification(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: a cancel before any record is read has no result."""
    payload = write_payload(
        tmp_path,
        "unopened.jsonl",
        [ephemeris_record(f"vera-ephemeris-95{index:02d}") for index in range(2)],
    )

    @contextmanager
    def interrupt_on_entry(*args, **kwargs):
        # The §8.1 lock wait and the §13.14 preamble both run here, before
        # the first record is classified.
        raise KeyboardInterrupt()
        yield  # pragma: no cover - unreachable, keeps the generator shape

    monkeypatch.setattr(imports_service, "writer_database", interrupt_on_entry)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 9
    # No record was ever classified, so a complete zero-count result would be
    # a claim the run never earned.
    assert envelope["result"] is None
    assert envelope["affected_ids"]["created"] == []


def test_a_signal_on_the_final_classification_still_carries_the_result(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: completeness is a property of the outcome, not of timing."""
    records = [
        ephemeris_record(f"vera-ephemeris-96{index:02d}") for index in range(3)
    ]
    payload = write_payload(tmp_path, "last-record.jsonl", records)
    classify = imports_service._classify

    def interrupt_after_the_last_classification(*args, **kwargs):
        outcome, record = classify(*args, **kwargs)
        if record.record_number == len(records):
            # The last record is classified and its row is committed; the
            # signal lands before the loop can record either fact.
            raise KeyboardInterrupt()
        return outcome, record

    monkeypatch.setattr(
        imports_service, "_classify", interrupt_after_the_last_classification
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 9
    # Nothing is unreported, so the run keeps the result it earned in full.
    assert envelope["result"]["counts"] == {
        "accepted": 3,
        "duplicate": 0,
        "rejected": 0,
    }
    assert [
        record["record_number"]
        for record in envelope["result"]["records"]["accepted"]
    ] == [1, 2, 3]


def test_a_candidate_that_only_shares_an_id_is_never_reported_as_committed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§19.4 rule 2: a collided ID names a different record, not this commit."""
    counts: dict[str, int] = {}

    def colliding_ids(kind: str) -> str:
        counts[kind] = counts.get(kind, 0) + 1
        # Every raw log this factory names takes the same ID, so the second
        # import's candidate collides with the first import's stored row.
        return (
            "log_shared_0001"
            if kind == "raw_log"
            else f"evi_shared_{counts[kind]:04d}"
        )

    monkeypatch.setattr(
        cli_module,
        "import_payload",
        functools.partial(imports_service.import_payload, id_factory=colliding_ids),
    )
    stored = write_payload(
        tmp_path, "collide-stored.jsonl", [ephemeris_record("vera-ephemeris-9700")]
    )
    first, _ = _invoke_json(workspace, ["import", "ephemeris", stored])
    assert first.exit_code == 0

    def interrupt_while_the_collision_unwinds(*args, **kwargs):
        # The signal arrives inside `_persist`, as its rollback unwinds the
        # collision, so `_classify` never reaches its `attempted.pop()`.
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        imports_service, "_persist", interrupt_while_the_collision_unwinds
    )
    payload = write_payload(
        tmp_path, "collide-new.jsonl", [ephemeris_record("vera-ephemeris-9701")]
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 9
    # The stored row carries the first record's identity and hash, so it is
    # not this candidate's commit however its ID reads.
    assert envelope["affected_ids"]["created"] == []
    assert envelope["result"] is None


def test_a_final_duplicate_survives_the_signal_that_follows_it(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: a classification leaving no row is banked where it is made."""
    stored = write_payload(
        tmp_path, "banked-stored.jsonl", [ephemeris_record("vera-ephemeris-9800")]
    )
    seeded, _ = _invoke_json(workspace, ["import", "ephemeris", stored])
    assert seeded.exit_code == 0

    records = [
        ephemeris_record("vera-ephemeris-9801", domain="knowledge_state"),
        ephemeris_record("vera-ephemeris-9800"),
    ]
    payload = write_payload(tmp_path, "banked.jsonl", records)
    classify = imports_service._classify

    def interrupt_after_the_last_classification(*args, **kwargs):
        outcome, record = classify(*args, **kwargs)
        if record.record_number == len(records):
            raise KeyboardInterrupt()
        return outcome, record

    monkeypatch.setattr(
        imports_service, "_classify", interrupt_after_the_last_classification
    )
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 9
    # Neither class creates a row, so neither is recoverable from the
    # workspace; both are still reported because both were decided.
    assert envelope["result"] is not None
    assert envelope["result"]["counts"] == {
        "accepted": 0,
        "duplicate": 1,
        "rejected": 1,
    }
    assert envelope["result"]["records"]["duplicate"][0]["record_number"] == 2


def test_a_repeated_candidate_id_never_hides_the_earlier_commit(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the ID is shared, so the verdict cannot be keyed by it."""
    counts: dict[str, int] = {}

    def colliding_ids(kind: str) -> str:
        counts[kind] = counts.get(kind, 0) + 1
        # Both records in this payload generate the same raw-log ID, so the
        # second collides with the row the first just wrote.
        return (
            "log_shared_0002"
            if kind == "raw_log"
            else f"evi_shared_{counts[kind]:04d}"
        )

    monkeypatch.setattr(
        cli_module,
        "import_payload",
        functools.partial(imports_service.import_payload, id_factory=colliding_ids),
    )
    payload = write_payload(
        tmp_path,
        "shared-id.jsonl",
        [
            ephemeris_record("vera-ephemeris-9900"),
            ephemeris_record("vera-ephemeris-9901"),
        ],
    )
    persist = imports_service._persist
    calls = {"count": 0}

    def interrupt_on_the_second_record(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return persist(*args, **kwargs)
        # The signal lands while the second record's collision unwinds, so
        # both candidates stay registered under the one shared ID.
        raise KeyboardInterrupt()

    monkeypatch.setattr(imports_service, "_persist", interrupt_on_the_second_record)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 9
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    # The first record's commit is durable and belongs in the report; the
    # second candidate shares only its ID.
    assert "raw_log" in created
    assert created["raw_log"] == ["log_shared_0002"]
    assert envelope["result"] is None


def test_a_teardown_fault_still_reports_the_completed_classification(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: lock release can fail without un-committing a record."""
    payload = write_payload(
        tmp_path,
        "teardown-fault.jsonl",
        [ephemeris_record(f"vera-ephemeris-97{index:02d}") for index in range(2)],
    )
    writer = imports_service.writer_database

    @contextmanager
    def fault_on_release(*args, **kwargs):
        with writer(*args, **kwargs) as connection:
            yield connection
        # Not an interrupt: the §8.1 lock release itself fails.
        raise OSError("lock release failed")

    monkeypatch.setattr(imports_service, "writer_database", fault_on_release)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    assert result.exit_code == 1
    assert envelope["diagnostic_class"] == "internal_error"
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 2
    # Every record was classified before teardown ran, so the result stands.
    assert envelope["result"] is not None
    assert envelope["result"]["counts"] == {
        "accepted": 2,
        "duplicate": 0,
        "rejected": 0,
    }


def test_a_signal_during_failure_reporting_keeps_the_same_boundary(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the ending changes, the committed records do not."""
    payload = write_payload(
        tmp_path,
        "failing-report.jsonl",
        [ephemeris_record(f"vera-ephemeris-98{index:02d}") for index in range(3)],
    )
    persist = imports_service._persist
    calls = {"count": 0}

    def fail_after_two(*args, **kwargs):
        if calls["count"] == 2:
            raise IdCollisionError()
        calls["count"] += 1
        return persist(*args, **kwargs)

    monkeypatch.setattr(imports_service, "_persist", fail_after_two)
    created_ids = cli_module._import_created
    reports = {"count": 0}

    def interrupt_the_first_report(*args, **kwargs):
        reports["count"] += 1
        if reports["count"] == 1:
            # The signal lands while the failure's boundary is being rendered.
            raise KeyboardInterrupt()
        return created_ids(*args, **kwargs)

    monkeypatch.setattr(cli_module, "_import_created", interrupt_the_first_report)
    result, envelope = _invoke_json(workspace, ["import", "ephemeris", payload])
    monkeypatch.undo()

    # The signal wins the ending, but not the report.
    assert result.exit_code == 9
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert len(created["raw_log"]) == 2
