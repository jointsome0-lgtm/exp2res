"""§14.9 assessment generation and inspection CLI behavior."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.pipeline.stage6 as stage6_module
import exp2res.services.assessment as assessment_service
import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
from exp2res.cli import app
from exp2res.storage.repository import (
    list_self_claims_for_snapshot,
    list_verification_findings,
)
from exp2res.storage.workspace import read_database

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets, fact_response
from test_stage4_detection import detector_response
from test_stage5_signals import prepare_facts, run_stage5, signal_response, SignalIds
from test_stage6_assessment import assessment_response
from test_stage7_verification import verifier_response


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


class InterruptAfterCommit:
    """Proxy one SQLite connection and interrupt after a selected durable commit."""

    def __init__(self, connection, selected_commit: int) -> None:
        self._connection = connection
        self._selected_commit = selected_commit
        self._commit_count = 0

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        self._commit_count += 1
        if self._commit_count == self._selected_commit:
            raise KeyboardInterrupt()


def interrupt_repair_commit(
    monkeypatch: pytest.MonkeyPatch, *, selected_commit: int
) -> None:
    real_writer_database = stage6_module.writer_database

    @contextmanager
    def interrupted_writer_database(*args, **kwargs):
        with real_writer_database(*args, **kwargs) as connection:
            yield InterruptAfterCommit(connection, selected_commit)

    monkeypatch.setattr(
        stage6_module, "writer_database", interrupted_writer_database
    )


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(app, ["--json", "--workspace", str(workspace), *arguments])
    return result, json.loads(result.stdout)


def prepare_graph(workspace: Path):
    ids = SignalIds()
    facts = prepare_facts(workspace, ids)
    signals = run_stage5(
        workspace, FakeContractRunner([signal_response(list(facts))]), ids
    ).current_signals
    return facts, tuple(item.id for item in signals)


def generate_snapshot(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    facts, signals = prepare_graph(workspace)
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
            ),
        ),
    )
    result, envelope = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert result.exit_code == 0, (result.stderr, envelope)
    return envelope["affected_ids"]["created"][0]["ids"][0], facts, signals


@pytest.mark.parametrize(
    "arguments",
    [
        ["--yes", "assess", "generate", "--scope", "unknown"],
        ["--yes", "assess", "generate", "--scope", "project"],
        ["--yes", "assess", "generate", "--scope", "project", "--project", "  "],
        ["--yes", "assess", "generate", "--project", "Vera Example"],
    ],
)
def test_scope_project_validation_matrix_is_class_2(
    workspace: Path, arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    result, envelope = invoke_json(workspace, arguments)
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "invalid_usage"


def test_consent_decline_precedes_adapter_construction(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    missing, envelope = invoke_json(workspace, ["assess", "generate"])
    assert missing.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"

    monkeypatch.setattr("exp2res.cli._noninteractive", lambda _controls: False)
    declined = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), "assess", "generate"],
        input="n\n",
    )
    assert declined.exit_code == 9
    assert json.loads(declined.stdout.splitlines()[-1])["diagnostic_class"] == "cancelled"


def test_verify_noninteractive_and_declined_consent_precede_adapter(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)

    def refuse_build(_workspace: Path):
        raise AssertionError("adapter construction ran before Vera Example consent")

    monkeypatch.setattr(assessment_service, "build_llm_execution", refuse_build)
    missing, envelope = invoke_json(
        workspace, ["assess", "verify", "--snapshot", snapshot_id]
    )
    assert missing.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"

    monkeypatch.setattr("exp2res.cli._noninteractive", lambda _controls: False)
    declined = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "assess",
            "verify",
            "--snapshot",
            snapshot_id,
        ],
        input="n\n",
    )
    assert declined.exit_code == 9
    assert json.loads(declined.stdout.splitlines()[-1])["diagnostic_class"] == "cancelled"


def test_verify_missing_selector_precedes_consent_and_adapter(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    result, envelope = invoke_json(
        workspace,
        ["assess", "verify", "--snapshot", "snapshot_vera_example_missing"],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "selector_not_found"


def test_verify_superseded_selector_precedes_consent_and_adapter(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    response = assessment_response(fact_ids=list(facts), signal_ids=list(signals))
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), FakeContractRunner([response, response])),
    )
    first_result, first = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    second_result, _second = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert first_result.exit_code == second_result.exit_code == 0
    superseded_id = first["affected_ids"]["created"][0]["ids"][0]
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    result, envelope = invoke_json(
        workspace, ["assess", "verify", "--snapshot", superseded_id]
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "snapshot_not_current"


def test_verify_happy_envelope_and_complete_human_rewrite_presentation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id, facts, _signals = generate_snapshot(workspace, monkeypatch)
    counterevidence = [
        {
            "statement": "The supplied counterevidence narrows the claim.",
            "source_ref_type": "experience_fact",
            "source_ref_id": facts[0],
        }
    ]
    responses = [
        verifier_response("partially_supported", counterevidence=counterevidence),
        verifier_response(),
    ]
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), FakeContractRunner(responses)),
    )
    result, envelope = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert result.exit_code == 0, (result.stderr, envelope)
    assert envelope["status"] == "ok"
    assert envelope["generation_ids"] == []
    assert envelope["result"] is None
    assert len(envelope["run_ids"]) == 1
    assert len(envelope["affected_ids"]["created"]) == 1
    created = envelope["affected_ids"]["created"][0]
    assert created["entity_type"] == "verification_finding"
    assert created["ids"] == [item["id"] for item in envelope["findings"]]
    with read_database(workspace) as connection:
        stored = list_verification_findings(
            connection, run_id=envelope["run_ids"][0]
        )
        run = connection.execute(
            "SELECT status FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
    assert envelope["findings"] == [
        item.model_dump(mode="json") for item in stored
    ]
    assert run["status"] == "completed"

    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [
                    verifier_response(
                        "partially_supported", counterevidence=counterevidence
                    ),
                    verifier_response(),
                ]
            ),
        ),
    )
    human = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "--yes",
            "assess",
            "verify",
            "--snapshot",
            snapshot_id,
        ],
    )
    assert human.exit_code == 0
    assert f"Snapshot {snapshot_id}: partially_supported" in human.stdout
    assert human.stdout.count("Finding ") == 2
    assert "Target claim:" in human.stdout
    assert "Status: partially_supported" in human.stdout
    assert "Reason: The supplied evidence" in human.stdout
    assert "unsupported scale wording" in human.stdout
    assert "Suggested rewrite: The supplied evidence" in human.stdout
    assert (
        f"[experience_fact:{facts[0]}]" in human.stdout
    )


def test_verify_blocked_is_completed_semantic_result(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("unsupported"), verifier_response()]
            ),
        ),
    )
    result, envelope = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert result.exit_code == 10
    assert envelope["status"] == "blocked"
    assert envelope["diagnostic_class"] == "verifier_gate_blocked"
    assert len(envelope["findings"]) == 2
    assert {item["status"] for item in envelope["findings"]} == {
        "supported",
        "unsupported",
    }
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
        claims = list_self_claims_for_snapshot(connection, snapshot_id)
    assert tuple(run) == ("completed", None)
    assert {item.verification_status for item in claims} == {
        "supported",
        "unsupported",
    }


def test_repair_envelope_requires_no_consent_and_reports_the_swap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.9/§14.14 rule 5: `assess repair` is promptless with a null result."""

    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)

    # Precondition surface: an unverified snapshot fails closed in class 2.
    early, early_envelope = invoke_json(
        workspace, ["assess", "repair", "--snapshot", snapshot_id]
    )
    assert early.exit_code == 2
    assert early_envelope["diagnostic_class"] == "snapshot_not_verified"

    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("rejected"), verifier_response()]
            ),
        ),
    )
    blocked, _ = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert blocked.exit_code == 10

    # No --yes: repair makes no cost-bearing call and prompts for nothing.
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("adapter built")),
    )
    result, envelope = invoke_json(
        workspace, ["assess", "repair", "--snapshot", snapshot_id]
    )
    assert result.exit_code == 0, (result.stderr, envelope)
    assert envelope["status"] == "ok"
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    superseded = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["superseded"]
    }
    new_snapshot_id = created["assessment_snapshot"][0]
    assert new_snapshot_id != snapshot_id
    assert len(created["self_claim"]) == 2
    assert superseded["assessment_snapshot"] == [snapshot_id]
    assert len(superseded["self_claim"]) == 2
    assert len(envelope["generation_ids"]) == 2
    assert len(envelope["run_ids"]) == 1
    assert envelope["findings"] == []
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT stage, status, provider FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
        calls = connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE run_id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()[0]
        claims = list_self_claims_for_snapshot(connection, new_snapshot_id)
    assert tuple(run) == ("13.6", "completed", None)
    assert calls == 0
    assert {item.verification_status for item in claims} == {"unverified"}

    human = runner.invoke(
        app,
        ["--workspace", str(workspace), "assess", "repair", "--snapshot", new_snapshot_id],
    )
    # The freshly repaired snapshot is unverified again: class 2, no prompt.
    assert human.exit_code == 2


def test_repeated_repair_human_result_counts_only_current_adoptions(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14: retained repair provenance does not inflate this swap's count."""

    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("rejected"), verifier_response()]
            ),
        ),
    )
    blocked, _ = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert blocked.exit_code == 10
    first, first_envelope = invoke_json(
        workspace, ["assess", "repair", "--snapshot", snapshot_id]
    )
    assert first.exit_code == 0
    first_snapshot_id = first_envelope["affected_ids"]["created"][0]["ids"][0]

    with read_database(workspace) as connection:
        claims = sorted(
            list_self_claims_for_snapshot(connection, first_snapshot_id),
            key=lambda claim: claim.id.encode("utf-8"),
        )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [
                    verifier_response(
                        "supported"
                        if "adopted_rewrite_of_claim_id" in claim.metadata
                        else "rejected"
                    )
                    for claim in claims
                ]
            ),
        ),
    )
    blocked, _ = invoke_json(
        workspace,
        ["--yes", "assess", "verify", "--snapshot", first_snapshot_id],
    )
    assert blocked.exit_code == 10

    second = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "assess",
            "repair",
            "--snapshot",
            first_snapshot_id,
        ],
    )
    assert second.exit_code == 0
    assert "adopted 1 of 2 claims" in second.stdout


def test_run_creation_commit_interrupt_reports_and_finalizes_the_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§12.13/§14.14: an ambiguous run-row commit is discovered and cancelled."""

    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("rejected"), verifier_response()]
            ),
        ),
    )
    blocked, _ = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert blocked.exit_code == 10
    interrupt_repair_commit(monkeypatch, selected_commit=1)

    result, envelope = invoke_json(
        workspace, ["assess", "repair", "--snapshot", snapshot_id]
    )
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert len(envelope["run_ids"]) == 1
    assert envelope["affected_ids"] == {
        "created": [],
        "superseded": [],
        "deleted": [],
    }
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
        current = connection.execute(
            "SELECT id FROM assessment_snapshots WHERE superseded_at IS NULL"
        ).fetchall()
    assert tuple(run) == ("failed", "cancelled")
    assert [row["id"] for row in current] == [snapshot_id]


def test_business_commit_interrupt_reports_the_committed_swap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14: an ambiguous swap commit carries its exact durable result."""

    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("rejected"), verifier_response()]
            ),
        ),
    )
    blocked, _ = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert blocked.exit_code == 10
    interrupt_repair_commit(monkeypatch, selected_commit=2)

    result, envelope = invoke_json(
        workspace, ["assess", "repair", "--snapshot", snapshot_id]
    )
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    superseded = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["superseded"]
    }
    assert len(created["assessment_snapshot"]) == 1
    assert len(created["self_claim"]) == 2
    assert superseded["assessment_snapshot"] == [snapshot_id]
    assert len(superseded["self_claim"]) == 2
    assert len(envelope["generation_ids"]) == 2
    assert len(envelope["run_ids"]) == 1
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
        current = connection.execute(
            "SELECT id FROM assessment_snapshots WHERE superseded_at IS NULL"
        ).fetchall()
    assert tuple(run) == ("completed", None)
    assert [row["id"] for row in current] == created["assessment_snapshot"]


def test_repair_post_commit_interrupt_reports_the_committed_swap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rules 5/6: a cleanup interrupt keeps the repair envelope full."""

    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(
                [verifier_response("rejected"), verifier_response()]
            ),
        ),
    )
    blocked, _ = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert blocked.exit_code == 10

    def interrupt_cleanup(_workspace, _snapshot_ids):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        stage6_module, "remove_assessment_sets", interrupt_cleanup
    )
    result, envelope = invoke_json(
        workspace, ["assess", "repair", "--snapshot", snapshot_id]
    )
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    # The committed swap survives into the cancelled envelope instead of
    # vanishing with the Outcome: created, superseded, generations, run.
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    superseded = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["superseded"]
    }
    assert superseded["assessment_snapshot"] == [snapshot_id]
    assert len(created["self_claim"]) == 2
    new_snapshot_id = created["assessment_snapshot"][0]
    assert new_snapshot_id != snapshot_id
    assert envelope["generation_ids"]
    assert len(envelope["run_ids"]) == 1
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT stage, status FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
        current = connection.execute(
            "SELECT id FROM assessment_snapshots WHERE superseded_at IS NULL"
        ).fetchall()
    assert tuple(run) == ("13.6", "completed")
    assert [row[0] for row in current] == [new_snapshot_id]


def test_verify_invalid_after_retry_reports_failed_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id, _facts, _signals = generate_snapshot(workspace, monkeypatch)
    invalid = verifier_response(include_reason=False)
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([invalid, invalid]),
        ),
    )
    result, envelope = invoke_json(
        workspace, ["--yes", "assess", "verify", "--snapshot", snapshot_id]
    )
    assert result.exit_code == 7
    assert envelope["diagnostic_class"] == "response_validation_failed"
    assert len(envelope["run_ids"]) == 1
    assert envelope["findings"] == []
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
    assert tuple(run) == ("failed", "response_validation_failed")


def test_generate_list_show_and_current_only_replacement(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    response = assessment_response(fact_ids=list(facts), signal_ids=list(signals))
    fake = FakeContractRunner([response, response])
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )

    first_result, first = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert first_result.exit_code == 0, (first_result.stderr, first)
    assert first["result"] is None
    assert [item["entity_type"] for item in first["affected_ids"]["created"]] == [
        "assessment_snapshot",
        "self_claim",
    ]
    assert first["invalidated_views"] == []
    snapshot_id = first["affected_ids"]["created"][0]["ids"][0]

    listed_result, listed = invoke_json(workspace, ["assess", "list"])
    assert listed_result.exit_code == 0
    assert listed["result"]["snapshots"][0]["id"] == snapshot_id
    assert set(listed["result"]["snapshots"][0]) == {
        "id",
        "scope",
        "scope_target",
        "verification_status",
        "created_at",
    }

    shown_result, shown = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert shown_result.exit_code == 0
    assert shown["result"]["snapshot"]["id"] == snapshot_id
    assert [item["id"] for item in shown["result"]["claims"]] == sorted(
        (item["id"] for item in shown["result"]["claims"]), key=str.encode
    )

    second_result, second = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert second_result.exit_code == 0
    assert [item["entity_type"] for item in second["affected_ids"]["superseded"]] == [
        "self_claim",
        "assessment_snapshot",
    ]
    assert second["invalidated_views"] == []

    missing_result, missing = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert missing_result.exit_code == 2
    assert missing["diagnostic_class"] == "selector_not_found"


def test_project_selector_persists_canonical_prefold_value(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    fake = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    generated, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "assess",
            "generate",
            "--scope",
            "project",
            "--project",
            "  Vera Example Project  ",
        ],
    )
    assert generated.exit_code == 0, (generated.stderr, envelope)
    snapshot_id = envelope["affected_ids"]["created"][0]["ids"][0]
    _shown_result, shown = invoke_json(
        workspace, ["assess", "show", "--snapshot", snapshot_id]
    )
    assert shown["result"]["snapshot"]["scope_target"] == "Vera Example Project"


def test_validate_assessment_selection_canonicalizes_for_direct_callers() -> None:
    scope, target = assessment_service.validate_assessment_selection(
        scope="project", project="  Verá Example  "
    )
    assert scope == "project"
    assert target == "Verá Example"


def test_logs_delete_reports_purged_assessment_groups_and_view(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    fake = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    generated_result, generated = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert generated_result.exit_code == 0
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    deleted_result, deleted = invoke_json(
        workspace, ["--yes", "logs", "delete", "--log-id", "log_vera_signal_0"]
    )
    assert deleted_result.exit_code == 0
    groups = {item["entity_type"]: item["ids"] for item in deleted["affected_ids"]["deleted"]}
    assert snapshot_id in groups["assessment_snapshot"]
    assert groups["self_claim"]
    assert deleted["invalidated_views"][0] == {
        "scope": "global",
        "scope_target": None,
        "snapshot_id": snapshot_id,
        "regeneration_command": "exp2res assess generate",
    }


def test_extract_envelope_reports_invalidated_assessment_view(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    assessment_runner = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), assessment_runner),
    )
    generated_result, generated = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert generated_result.exit_code == 0
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    extractor = FakeContractRunner([fact_response(["evi_vera_signal_0"])])
    monkeypatch.setattr(
        extraction_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), extractor),
    )
    extracted_result, extracted = invoke_json(
        workspace, ["--yes", "extract", "--log-id", "log_vera_signal_0"]
    )
    assert extracted_result.exit_code == 0
    superseded_types = {
        item["entity_type"] for item in extracted["affected_ids"]["superseded"]
    }
    assert {"self_claim", "assessment_snapshot"}.issubset(superseded_types)
    assert extracted["invalidated_views"][0]["snapshot_id"] == snapshot_id


def test_detection_replacement_envelope_reports_invalidated_view(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts, signals = prepare_graph(workspace)
    assessment_runner = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
    )
    monkeypatch.setattr(
        assessment_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), assessment_runner),
    )
    generated_result, generated = invoke_json(
        workspace, ["--yes", "assess", "generate"]
    )
    assert generated_result.exit_code == 0
    snapshot_id = generated["affected_ids"]["created"][0]["ids"][0]

    detector = FakeContractRunner(
        [
            detector_response(
                target_id=facts[0],
                left=("experience_fact", facts[0]),
                right=("raw_log", "log_vera_signal_0"),
            )
        ]
    )
    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), detector),
    )
    detected_result, detected = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert detected_result.exit_code == 0
    assert detected["invalidated_views"][0]["snapshot_id"] == snapshot_id
    superseded_types = {
        item["entity_type"] for item in detected["affected_ids"]["superseded"]
    }
    assert {"self_claim", "assessment_snapshot"}.issubset(superseded_types)
