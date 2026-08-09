"""Vera's declared import steps replayed through the §14.5 CLI.

``replay.json`` is a claim about what each corpus payload does; this module
is its proof. Every ``kind: import`` step is replayed in declared order
against one workspace: the three §19-backed forms against their declared
counts, evidence strengths, and rejection reasons, and the fourth `file`
form — which §14.5 states is not a §19 record — against the record shape
that section gives it instead.

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
from exp2res.services.imports import (
    ImportOutcome,
    import_design_document,
    import_payload,
)

from conftest import VERA_CORPUS, configure_timezone


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]
runner = CliRunner()

REPLAY = json.loads((VERA_CORPUS / "replay.json").read_text(encoding="utf-8"))
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

    def clock() -> datetime:
        return parse_clock(step["clock"])

    arguments = [str(VERA_CORPUS / step["file"])]
    if step["importer"] == "file":
        # Not a §19 record (§14.5), so it is pinned at its own service seam
        # and carries the declared project label the other forms never take.
        monkeypatch.setattr(
            cli_module,
            "import_design_document",
            functools.partial(import_design_document, clock=clock),
        )
        arguments += ["--project", step["project"]]
    else:
        monkeypatch.setattr(
            cli_module,
            "import_payload",
            functools.partial(import_payload, clock=clock),
        )
    result = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "import",
            step["importer"],
            *arguments,
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
    counted = [step for step in steps if "accepted" in step["expect"]]
    # A shrinking corpus must not silently shrink the replay: every declared
    # step is claimed here or by the `file` test below, and no other.
    assert [step["step"] for step in steps] == [9, 10, 11, 12, 13]
    assert [step["step"] for step in counted] == [9, 10, 11, 12]

    for step in counted:
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


def test_the_declared_file_step_records_its_design_document(
    replay_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.5's fourth form, replayed against the record shape it declares."""
    declared = [
        step
        for step in import_steps(REPLAY["steps"])
        if step["importer"] == "file"
    ]
    assert [step["step"] for step in declared] == [13]
    step = declared[0]

    envelope = run_step(replay_workspace, step, monkeypatch)

    assert envelope["command"] == "import file"
    assert envelope["status"] == step["expect"]["status"]
    # §14.14 rule 5 lists only the §19-backed forms' `{counts, records}`.
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    log = show_log(replay_workspace, created["raw_log"][0])
    assert parse_clock(log["log"]["recorded_at"]) == parse_clock(step["clock"])
    assert log["log"]["entry_type"] == "design_doc"
    assert log["log"]["source_type"] == "imported_artifact"
    assert log["log"]["project"] == step["project"]
    # §14.5 with §14.2: the canonical real path, in both places, and no
    # managed copy of a document that stays where the owner put it.
    canonical = (VERA_CORPUS / step["file"]).resolve().as_posix()
    assert log["log"]["external_ref"] == canonical
    assert [item["strength"] for item in log["evidence_items"]] == ["design_doc"]
    assert [item["path"] for item in log["evidence_items"]] == [canonical]


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
