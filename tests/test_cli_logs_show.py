"""Offline §14.11 / §14.14 raw-log inspection CLI coverage."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from exp2res.cli import app
from exp2res.domain.models import EvidenceItem, OccurredAt, RawLog
from exp2res.services.correction import capture_correction
from exp2res.storage.repository import insert_evidence_item, insert_raw_log
from exp2res.storage.workspace import writer_database

from conftest import FIXED_NOW


pytestmark = [pytest.mark.unit, pytest.mark.lifecycle]
runner = CliRunner()


def _invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
    )
    return result, json.loads(result.stdout)


def _invoke_human(workspace: Path, arguments: list[str]):
    return runner.invoke(app, ["--workspace", str(workspace), *arguments])


def _occurred() -> OccurredAt:
    return OccurredAt(
        start=FIXED_NOW,
        end=None,
        precision="exact_day",
        confidence="high",
    )


def _seed_bundle(
    workspace: Path,
    *,
    suffix: str,
    raw_text: str,
    recorded_offset: int = 0,
) -> tuple[RawLog, tuple[EvidenceItem, ...]]:
    recorded_at = FIXED_NOW + timedelta(seconds=recorded_offset)
    raw_log = RawLog(
        id=f"log_vera_{suffix}",
        recorded_at=recorded_at,
        entry_type="manual_daily",
        source_type="manual_entry",
        occurred=_occurred(),
        raw_text=raw_text,
        project="Vera Example Inspection",
        external_ref=f"vera-example://logs/{suffix}",
        corrects_log_id=None,
        metadata={
            "raw_text": "Vera Example hidden raw metadata",
            "metadata": "Vera Example hidden log metadata",
        },
    )
    evidence_items = (
        EvidenceItem(
            id=f"evi_vera_{suffix}_a",
            created_at=recorded_at,
            raw_log_id=raw_log.id,
            title="Vera Example primary evidence",
            summary="Vera Example owner-authored summary.",
            uri=f"vera-example://evidence/{suffix}/a",
            path=f"examples/vera/{suffix}-a.md",
            strength="manual_claim",
            metadata={"metadata": "Vera Example hidden evidence metadata"},
        ),
        EvidenceItem(
            id=f"evi_vera_{suffix}_b",
            created_at=recorded_at + timedelta(microseconds=1),
            raw_log_id=raw_log.id,
            title=None,
            summary="Vera Example linked artifact summary.",
            uri=None,
            path=f"examples/vera/{suffix}-b.md",
            strength="artifact_reference",
            metadata={"raw_text": "Vera Example hidden evidence raw metadata"},
        ),
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        insert_raw_log(connection, raw_log)
        for item in evidence_items:
            insert_evidence_item(connection, item)
        connection.commit()
    return raw_log, evidence_items


def _assert_no_forbidden_result_keys(value: object) -> None:
    if isinstance(value, dict):
        assert "raw_text" not in value
        assert "metadata" not in value
        for child in value.values():
            _assert_no_forbidden_result_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_result_keys(child)


def test_logs_show_human_prints_only_selected_complete_raw_text(
    workspace: Path,
) -> None:
    """§14.14 rule 5: one selected raw_text follows the closed projection."""

    selected_text = "Vera Example selected first line.\nSelected second line."
    other_text = "Vera Example content from another retained record."
    selected, _ = _seed_bundle(
        workspace, suffix="selected", raw_text=selected_text
    )
    _seed_bundle(
        workspace,
        suffix="other",
        raw_text=other_text,
        recorded_offset=1,
    )

    shown = _invoke_human(
        workspace, ["logs", "show", "--log-id", selected.id]
    )

    assert shown.exit_code == 0
    projection_text, separator, raw_suffix = shown.stdout.partition(selected_text)
    assert separator == selected_text
    assert raw_suffix == "\n"
    projection = json.loads(projection_text)
    assert projection["log"]["id"] == selected.id
    assert [item["id"] for item in projection["evidence_items"]] == [
        "evi_vera_selected_a",
        "evi_vera_selected_b",
    ]
    assert other_text not in shown.stdout + shown.stderr


def test_logs_show_json_is_closed_raw_free_and_includes_linked_evidence(
    workspace: Path,
) -> None:
    """§14.14 rule 5: exact log/evidence projections exclude private fields."""

    raw_text = "Vera Example private selected record."
    selected, evidence_items = _seed_bundle(
        workspace, suffix="closed", raw_text=raw_text
    )

    shown, envelope = _invoke_json(
        workspace, ["logs", "show", "--log-id", selected.id]
    )

    assert shown.exit_code == 0
    assert envelope["command"] == "logs show"
    assert set(envelope["result"]) == {"log", "evidence_items"}
    assert set(envelope["result"]["log"]) == {
        "id",
        "recorded_at",
        "entry_type",
        "source_type",
        "occurred",
        "project",
        "external_ref",
        "corrects_log_id",
    }
    assert [item["id"] for item in envelope["result"]["evidence_items"]] == [
        item.id for item in evidence_items
    ]
    assert all(
        set(item)
        == {
            "id",
            "created_at",
            "raw_log_id",
            "title",
            "summary",
            "uri",
            "path",
            "strength",
        }
        for item in envelope["result"]["evidence_items"]
    )
    _assert_no_forbidden_result_keys(envelope["result"])
    assert raw_text not in shown.stdout + shown.stderr
    assert "Vera Example hidden" not in shown.stdout + shown.stderr


def test_logs_show_inspects_displaced_record_by_id_in_both_modes(
    workspace: Path,
) -> None:
    """§14.11: a correction-displaced retained record remains inspectable."""

    displaced_text = "Vera Example displaced original record."
    displaced, _ = _seed_bundle(
        workspace, suffix="displaced", raw_text=displaced_text
    )
    correction_text = "Vera Example replacement correction record."
    capture_correction(
        workspace,
        log_id=displaced.id,
        raw_text=correction_text,
        occurred=displaced.occurred,
        project=displaced.project,
        clock=lambda: FIXED_NOW + timedelta(seconds=1),
        id_factory=lambda kind: {
            "raw_log": "log_vera_correction",
            "evidence_item": "evi_vera_correction",
        }[kind],
    )

    human = _invoke_human(
        workspace, ["logs", "show", "--log-id", displaced.id]
    )
    machine, envelope = _invoke_json(
        workspace, ["logs", "show", "--log-id", displaced.id]
    )

    assert human.exit_code == machine.exit_code == 0
    assert displaced_text in human.stdout
    assert correction_text not in human.stdout + human.stderr
    assert envelope["result"]["log"]["id"] == displaced.id
    assert envelope["result"]["log"]["corrects_log_id"] is None
    _assert_no_forbidden_result_keys(envelope["result"])


def test_logs_show_unknown_id_uses_stable_selector_failure_envelope(
    workspace: Path,
) -> None:
    """§14.11 / §14.14 rule 4: unknown IDs fail like other show commands."""

    shown, envelope = _invoke_json(
        workspace,
        ["logs", "show", "--log-id", "log_vera_unknown"],
    )

    assert shown.exit_code == 2
    assert envelope["command"] == "logs show"
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "selector_not_found"
    assert envelope["result"] is None
