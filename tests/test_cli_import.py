"""Offline §14.5 / §14.14 import CLI coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from exp2res.cli import app

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
