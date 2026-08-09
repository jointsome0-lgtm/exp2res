"""Vera's declared §19 import steps replayed through the §14.5 CLI.

``replay.json`` is a claim about what each corpus payload does; this module
is its proof. Every ``kind: import`` step whose form exists — the three
§19-backed ones — is replayed in declared order against one workspace, and
each declared count, evidence strength, and rejection reason is asserted.

Two declared values are read where they actually live. The closed §14.14
rule 5 projection carries no rejection reason, so a failure step's declared
reason is checked against the service classification behind the envelope.
The step clock pins ``recorded_at``, which the CLI takes from the wall
clock, so the harness injects it exactly like the sibling capture replay.
"""

from __future__ import annotations

from datetime import datetime
import functools
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.cli as cli_module
from exp2res.cli import app
from exp2res.services.imports import ImportOutcome, import_payload

from conftest import VERA_CORPUS, configure_timezone


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]
runner = CliRunner()

REPLAY = json.loads((VERA_CORPUS / "replay.json").read_text(encoding="utf-8"))
# §14.5's fourth form lands in a later phase; naming it keeps the skip an
# explicit statement about scope rather than a silently short replay.
DEFERRED_FORMS = {"file"}
# The declared prose reason for each failure step, mapped to the reason code
# the classifier actually assigns. Both directions matter: the fixture must
# fail, and it must fail for the stated reason and no other.
FAILURE_REASONS = {
    "retained identity, different content hash (§19.4 rule 2)": (
        "content_hash_conflict"
    ),
    "commit_sha is not 40 lowercase hex digits (§19.3)": "record_invalid",
    "summary not byte-exact in text (§19.2)": "atlas_text_fidelity",
}


def import_steps(steps: list[dict]) -> list[dict]:
    return [step for step in steps if step["kind"] == "import"]


def parse_clock(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None
    return parsed


def run_step(
    workspace: Path, step: dict, monkeypatch: pytest.MonkeyPatch
) -> dict:
    """Run one declared import step with its declared clock pinned."""

    monkeypatch.setattr(
        cli_module,
        "import_payload",
        functools.partial(import_payload, clock=lambda: parse_clock(step["clock"])),
    )
    result = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "import",
            step["importer"],
            str(VERA_CORPUS / step["file"]),
        ],
    )
    monkeypatch.undo()
    assert result.exit_code in (0, 2), result.stdout
    return json.loads(result.stdout)


def classify(workspace: Path, step: dict) -> ImportOutcome:
    """Re-run one step at the service seam for its per-record reason codes."""

    return import_payload(
        workspace,
        source_system=step["importer"],
        payload_path=str(VERA_CORPUS / step["file"]),
        clock=lambda: parse_clock(step["clock"]),
    )


def show_log(workspace: Path, raw_log_id: str) -> dict:
    result = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "logs",
            "show",
            "--log-id",
            raw_log_id,
        ],
    )
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)["result"]


@pytest.fixture
def replay_workspace(workspace: Path) -> Path:
    # §14.14 forbids a default timezone; the corpus declares its own.
    configure_timezone(workspace, REPLAY["setup"]["workspace_timezone"])
    return workspace


def test_every_declared_import_step_produces_its_declared_counts(
    replay_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The corpus is only a fixture if the importer agrees with replay.json."""
    steps = import_steps(REPLAY["steps"])
    replayed = [step for step in steps if step["importer"] not in DEFERRED_FORMS]
    # A shrinking corpus must not silently shrink the replay.
    assert [step["step"] for step in replayed] == [9, 10, 11, 12]

    for step in replayed:
        envelope = run_step(replay_workspace, step, monkeypatch)

        expected = step["expect"]
        assert envelope["command"] == f"import {step['importer']}", step
        assert envelope["result"]["counts"] == {
            name: expected[name] for name in ("accepted", "duplicate", "rejected")
        }, step
        accepted = envelope["result"]["records"]["accepted"]
        for record in accepted:
            log = show_log(replay_workspace, record["raw_log_id"])
            # The pinned step clock is what reached the stored row.
            assert parse_clock(log["log"]["recorded_at"]) == parse_clock(
                step["clock"]
            ), step
            if "evidence_strength" in expected:
                assert [item["strength"] for item in log["evidence_items"]] == [
                    expected["evidence_strength"]
                ], step


def test_the_deferred_file_form_is_the_only_unreplayed_step(
    replay_workspace: Path,
) -> None:
    """§14.5's `import file` is declared but has no command yet."""
    deferred = [
        step
        for step in import_steps(REPLAY["steps"])
        if step["importer"] in DEFERRED_FORMS
    ]
    assert [step["importer"] for step in deferred] == ["file"]
    result = runner.invoke(
        app,
        [
            "--workspace",
            str(replay_workspace),
            "import",
            "file",
            str(VERA_CORPUS / deferred[0]["file"]),
        ],
    )
    assert result.exit_code != 0


def test_every_declared_failure_step_fails_for_its_declared_reason(
    replay_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§19.4 rule 4: each invalid fixture rejects its own record, alone."""
    prerequisite = {
        step["step"]: step for step in import_steps(REPLAY["steps"])
    }
    seeded: set[int] = set()
    for step in REPLAY["failure_steps"]:
        assert step["kind"] == "import"
        after = step["after_step"]
        if after not in seeded:
            run_step(replay_workspace, prerequisite[after], monkeypatch)
            seeded.add(after)

        envelope = run_step(replay_workspace, step, monkeypatch)

        expected = step["expect"]
        assert envelope["result"]["counts"] == {
            name: expected[name] for name in ("accepted", "duplicate", "rejected")
        }, step
        assert envelope["diagnostic_class"] == "import_records_rejected", step
        # A rejected payload commits nothing, so the re-run behind the
        # envelope classifies the same record the same way.
        outcome = classify(replay_workspace, step)
        assert [record.reason for record in outcome.rejected] == [
            FAILURE_REASONS[expected["reason"]]
        ], step
