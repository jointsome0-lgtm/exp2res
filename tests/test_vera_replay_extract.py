"""Vera E1 replays the manual-capture lineages through the Stage 3 CLI.

The replay intentionally skips ``import`` and ``jd_add`` steps: they seed no
manual-capture lineage needed by E1, and their integration command paths are a
later phase. This module's declared E1 scope is the replayed daily/retro/
correction subset plus one canned §15.2 response per planned lineage.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.capture as capture_service
import exp2res.services.extraction as extraction_service
from exp2res.cli import app
from exp2res.domain.models import EvidenceItem, OccurredAt, RawLog
from exp2res.services.capture import capture_daily_file, capture_retro
from exp2res.storage.repository import (
    get_raw_log,
    insert_evidence_item,
    insert_raw_log,
)
from exp2res.storage.workspace import read_database, writer_database

from conftest import VERA_CORPUS, configure_timezone
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


class ReplayIds:
    """Stable typed IDs shared by capture and Stage 3 for canned responses."""

    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        self.counts[kind] += 1
        prefix = {
            "raw_log": "log",
            "evidence_item": "evi",
            "fact": "fact",
            "gap": "gap",
            "contradiction": "contradiction",
            "signal": "signal",
            "run": "run",
            "gen": "gen",
        }[kind]
        return f"{prefix}_vera_{self.counts[kind]:04d}"


def parse_clock(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None
    return parsed


def parse_occurred(value: object) -> OccurredAt:
    return OccurredAt.model_validate_json(json.dumps(value))


def add_correction(
    workspace: Path,
    *,
    script: dict[str, object],
    target: RawLog,
    recorded_at: datetime,
    id_factory: ReplayIds,
) -> RawLog:
    """Persist the future §14.4 CLI capture shape at the repository seam.

    Correction CLI capture belongs to a later phase. This harness performs
    only its specified copy rules and atomic RawLog/manual_claim insertion so
    E1 can exercise the already-implemented Stage 3 correction lineage.
    """

    occurred_value = script.get("occurred", "copy")
    occurred = (
        target.occurred
        if occurred_value == "copy"
        else parse_occurred(occurred_value)
    )
    project_value = script.get("project", "copy")
    project = target.project if project_value == "copy" else project_value
    raw_log = RawLog(
        id=id_factory("raw_log"),
        recorded_at=recorded_at,
        entry_type="correction",
        source_type="manual_entry",
        occurred=occurred,
        raw_text=str(script["text"]),
        project=project,
        external_ref=None,
        corrects_log_id=target.id,
        metadata={},
    )
    evidence = EvidenceItem(
        id=id_factory("evidence_item"),
        created_at=recorded_at,
        raw_log_id=raw_log.id,
        title=None,
        summary="Owner-authored manual claim.",
        uri=None,
        path=None,
        strength="manual_claim",
        metadata={},
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        insert_raw_log(connection, raw_log)
        insert_evidence_item(connection, evidence)
        connection.commit()
    return raw_log


def replay_manual_capture(workspace: Path, ids: ReplayIds) -> dict[str, RawLog]:
    contract = json.loads((VERA_CORPUS / "replay.json").read_text(encoding="utf-8"))
    by_story_key: dict[str, RawLog] = {}
    for step in contract["steps"]:
        kind = step["kind"]
        if kind in {"import", "jd_add"}:
            continue
        clock = parse_clock(step["clock"])
        source = VERA_CORPUS / step["file"]
        if kind == "log_daily":
            bundle = capture_daily_file(
                workspace,
                source_path=str(source),
                project=step["project"],
                clock=lambda clock=clock: clock,
                id_factory=ids,
            )
            by_story_key[source.stem] = bundle.raw_log
        elif kind == "log_retro":
            script = json.loads(source.read_text(encoding="utf-8"))
            answers = script["answers"]
            bundle = capture_retro(
                workspace,
                occurred=parse_occurred(answers["period"]),
                raw_text=answers["text"],
                project=answers["project"],
                clock=lambda clock=clock: clock,
                id_factory=ids,
            )
            by_story_key[script["story_key"]] = bundle.raw_log
        elif kind == "correction_add":
            script = json.loads(source.read_text(encoding="utf-8"))
            target = by_story_key[script["target_story_key"]]
            correction = add_correction(
                workspace,
                script=script,
                target=target,
                recorded_at=clock,
                id_factory=ids,
            )
            by_story_key[script["story_key"]] = correction
        else:
            raise AssertionError(f"unexpected manual E1 step: {kind}")
    return by_story_key


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
    )
    return result, json.loads(result.stdout)


def test_vera_e1_cli_replay_preserves_provenance_and_one_current_generation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E1 coarse outcome plus §13.3 project/placement and rerun invariants."""

    configure_timezone(workspace, "Europe/Berlin")
    ids = ReplayIds()
    monkeypatch.setattr(capture_service, "new_id", ids)
    replay_manual_capture(workspace, ids)

    response_paths = sorted((VERA_CORPUS / "llm").glob("extract-call-*.json"))
    responses = [path.read_bytes() for path in response_paths]
    assert len(responses) == 8
    fake = FakeContractRunner([*responses, *responses])
    monkeypatch.setattr(
        extraction_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    real_run_fact_extraction = extraction_service.run_fact_extraction
    e1 = next(
        step
        for step in json.loads(
            (VERA_CORPUS / "replay.json").read_text(encoding="utf-8")
        )["derived_steps"]
        if step["step"] == "E1"
    )
    e1_clock = parse_clock(e1["clock"])

    def deterministic_stage3(selected_workspace: Path, **kwargs):
        # run_extract supplies its own run-tracking id_factory; the replay
        # pins ids and the clock instead, so drop the conflicting keys.
        kwargs.pop("id_factory", None)
        kwargs.pop("clock", None)
        return real_run_fact_extraction(
            selected_workspace,
            **kwargs,
            id_factory=ids,
            clock=lambda: e1_clock,
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )

    monkeypatch.setattr(
        extraction_service, "run_fact_extraction", deterministic_stage3
    )

    first_result, first = invoke_json(workspace, ["--yes", "extract"])
    assert first_result.exit_code == 0
    assert first["status"] == e1["expect"]["status"] == "ok"
    first_created = first["affected_ids"]["created"][0]["ids"]
    assert len(first_created) == 8
    assert len(first["run_ids"]) == 1
    assert first["result"] is None

    listed_result, listed = invoke_json(workspace, ["facts", "list"])
    assert listed_result.exit_code == 0
    facts = listed["result"]["facts"]
    assert {fact["id"] for fact in facts} == set(first_created)
    with read_database(workspace) as connection:
        for fact in facts:
            reached = [
                get_raw_log(connection, raw_log_id)
                for raw_log_id in fact["source_log_ids"]
            ]
            assert all(log is not None for log in reached)
            governing = max(
                reached,
                key=lambda log: (
                    log.recorded_at,
                    log.id.encode("utf-8"),
                ),
            )
            assert fact["project"] == governing.project
            assert fact["occurred"] == governing.occurred.model_dump(mode="json")

    second_result, second = invoke_json(workspace, ["--yes", "extract"])
    assert second_result.exit_code == 0
    second_created = second["affected_ids"]["created"][0]["ids"]
    assert second["affected_ids"]["superseded"] == [
        {"entity_type": "experience_fact", "ids": first_created}
    ]
    assert set(second_created).isdisjoint(first_created)
    assert len(second["run_ids"]) == 1

    with read_database(workspace) as connection:
        current = connection.execute(
            """
            SELECT generation_id, COUNT(*)
            FROM experience_facts
            WHERE superseded_at IS NULL
            GROUP BY generation_id
            """
        ).fetchall()
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN superseded_at IS NULL THEN 1 ELSE 0 END),
                COUNT(*)
            FROM experience_facts
            """
        ).fetchone()
    assert len(current) == 8
    assert all(row[1] == 1 for row in current)
    assert tuple(counts) == (8, 16)
    assert len(fake.calls) == 16

    planned_evidence = [
        json.loads(call.serialized_input)["evidence_items"][0]["id"]
        for call in fake.calls[:8]
    ]
    assert planned_evidence == [
        "evi_vera_0001",
        "evi_vera_0002",
        "evi_vera_0003",
        "evi_vera_0004",
        "evi_vera_0005",
        "evi_vera_0008",
        "evi_vera_0007",
        "evi_vera_0009",
    ]
