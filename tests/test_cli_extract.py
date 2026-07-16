"""§14.6 Stage 3 CLI extraction and fact-inspection behavior."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

import exp2res.services.extraction as extraction_service
import exp2res.services.facts as facts_service
from exp2res.cli import app
from exp2res.config import DEFAULT_LLM_CONFIG
from exp2res.domain.models import ExperienceFact
from exp2res.llm.adapter import AdapterRuntime
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CodexCLIRunner
from exp2res.storage.repository import list_experience_facts
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import (
    SELECTION,
    add_log,
    budgets,
    exact_day,
    fact_response,
)


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
    )
    return result, json.loads(result.stdout)


def seed_lineage(workspace: Path, suffix: str = "cli") -> str:
    _log, items = add_log(
        workspace,
        log_id=f"log_vera_{suffix}",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example designed a provenance-aware local workflow.",
        occurred=exact_day(15),
        item_specs=((f"evi_vera_{suffix}", "manual_claim"),),
        project="Vera Example Project",
    )
    return items[0].id


def install_fake_execution(
    monkeypatch: pytest.MonkeyPatch, fake: FakeContractRunner
) -> None:
    monkeypatch.setattr(
        extraction_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )


def test_build_llm_execution_uses_workspace_selection_and_budget_defaults(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§15.13: the command builder uses selected config and registered runtime."""

    config = workspace / ".exp2res" / "config.toml"
    config.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        '[llm]\nadapter = "codex-cli"\nmodel = "gpt-5.6-sol"\n'
        'codex_home_env = "CODEX_HOME"\n\n'
        "[privacy]\nignore_paths = []\n",
        encoding="utf-8",
    )
    runtime = AdapterRuntime(
        codex_binary=tmp_path / "codex",
        bwrap_binary=tmp_path / "bwrap",
        codex_home=tmp_path / "codex-home",
        cli_version="0.144.4-test",
    )
    monkeypatch.setattr(
        extraction_service, "resolve_codex_home", lambda _config: runtime.codex_home
    )
    monkeypatch.setattr(
        extraction_service, "preflight_adapter", lambda **_kwargs: runtime
    )

    selection, resolved_budgets, selected_runner = (
        extraction_service.build_llm_execution(workspace)
    )
    assert selection == LLMSelection("codex-cli", "gpt-5.6-sol")
    assert resolved_budgets.transport_attempt_cap == (
        DEFAULT_LLM_CONFIG.transport_attempt_cap
    )
    assert (
        resolved_budgets.input_token_budget
        == DEFAULT_LLM_CONFIG.input_token_budget
    )
    assert (
        resolved_budgets.output_token_budget
        == DEFAULT_LLM_CONFIG.output_token_budget
    )
    assert resolved_budgets.planned_output_tokens == (
        DEFAULT_LLM_CONFIG.output_token_budget
    )
    assert isinstance(selected_runner, CodexCLIRunner)
    assert selected_runner.codex_binary == runtime.codex_binary
    assert selected_runner.bwrap_binary == runtime.bwrap_binary
    assert selected_runner.codex_home == runtime.codex_home


def test_extract_noninteractive_requires_yes_before_runner_construction(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 3: no consent means no provider-side construction or call."""

    def refuse_build(_workspace: Path):
        raise AssertionError("LLM execution was built before cost consent")

    monkeypatch.setattr(extraction_service, "build_llm_execution", refuse_build)
    result, envelope = invoke_json(workspace, ["extract"])
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"
    assert envelope["run_ids"] == []


def test_extract_success_reports_standard_fields_and_contract_warnings(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.3 rules 11/12 and §14.14 rule 5: extraction has no result object."""

    evidence_id = seed_lineage(workspace, "happy")
    warning = {
        "type": "limited_support",
        "message": "Vera Example has only one owner-authored source for this fact.",
    }
    fake = FakeContractRunner([fact_response([evidence_id], warnings=[warning])])
    install_fake_execution(monkeypatch, fake)

    result, envelope = invoke_json(workspace, ["--yes", "extract"])
    assert result.exit_code == 0
    assert envelope["status"] == "ok"
    assert envelope["result"] is None
    created_groups = envelope["affected_ids"]["created"]
    assert len(created_groups) == 1
    assert created_groups[0]["entity_type"] == "experience_fact"
    assert len(created_groups[0]["ids"]) == 1
    assert envelope["generation_ids"]
    assert len(envelope["run_ids"]) == 1
    assert envelope["warnings"] == [warning]
    assert len(fake.calls) == 1


def test_extract_unknown_selector_has_no_run_row(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.6/§14.14 rule 3: selector failure precedes consent and preflight."""

    def refuse_build(_workspace: Path):
        raise AssertionError("adapter preflight ran for an invalid selector")

    monkeypatch.setattr(extraction_service, "build_llm_execution", refuse_build)
    result, envelope = invoke_json(
        workspace, ["--yes", "extract", "--log-id", "log_vera_missing"]
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "selector_not_found"
    assert envelope["run_ids"] == []
    # In `logs delete` order, the selector resolves before consent: a
    # non-interactive call without --yes still reports the selector class.
    unconsented, unconsented_envelope = invoke_json(
        workspace, ["extract", "--log-id", "log_vera_missing"]
    )
    assert unconsented.exit_code == 2
    assert unconsented_envelope["diagnostic_class"] == "selector_not_found"
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
            == 0
        )


def test_extract_invalid_after_retry_reports_failure_and_durable_telemetry(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§15.1: failed response validation commits telemetry but no business rows."""

    seed_lineage(workspace, "invalid")
    invalid = fact_response(["evi_vera_out_of_context"])
    fake = FakeContractRunner([invalid, invalid])
    install_fake_execution(monkeypatch, fake)
    result, envelope = invoke_json(workspace, ["--yes", "extract"])
    # §14.14 rule 4 names §15.1 invalid-after-retry in exit class 7.
    assert result.exit_code == 7
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "response_validation_failed"
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()
        run = connection.execute(
            "SELECT id, status, failure_code FROM processing_runs"
        ).fetchone()
    assert tuple(run[1:]) == ("failed", "response_validation_failed")
    # §14.14 rule 5: the failed run's durable telemetry stays addressable —
    # the envelope carries the committed processing-run ID for `runs show`.
    assert envelope["run_ids"] == [run[0]]


def test_facts_list_show_round_trip_complete_values_via_read_seam(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rules 5/7: list/show expose complete facts and stay read-only."""

    evidence_id = seed_lineage(workspace, "inspect")
    install_fake_execution(
        monkeypatch,
        FakeContractRunner([fact_response([evidence_id])]),
    )
    extracted, extraction_envelope = invoke_json(workspace, ["--yes", "extract"])
    assert extracted.exit_code == 0
    fact_id = extraction_envelope["affected_ids"]["created"][0]["ids"][0]

    real_read_database = facts_service.read_database
    read_calls: list[Path] = []

    @contextmanager
    def tracked_read_database(selected: Path, **kwargs):
        read_calls.append(selected)
        with real_read_database(selected, **kwargs) as connection:
            yield connection

    monkeypatch.setattr(facts_service, "read_database", tracked_read_database)

    listed, list_envelope = invoke_json(workspace, ["facts", "list"])
    shown, show_envelope = invoke_json(
        workspace, ["facts", "show", "--fact-id", fact_id]
    )
    assert listed.exit_code == shown.exit_code == 0
    listed_facts = list_envelope["result"]["facts"]
    shown_facts = show_envelope["result"]["facts"]
    assert len(listed_facts) == 1
    assert shown_facts == listed_facts
    assert set(shown_facts[0]) == set(ExperienceFact.model_fields)
    assert read_calls == [workspace, workspace]

    missing, missing_envelope = invoke_json(
        workspace, ["facts", "show", "--fact-id", "fact_vera_missing"]
    )
    assert missing.exit_code == 2
    assert missing_envelope["diagnostic_class"] == "selector_not_found"


def test_facts_show_rejects_superseded_fact_ids(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 7: current-facts inspection never browses history."""

    evidence_id = seed_lineage(workspace, "history")
    install_fake_execution(
        monkeypatch,
        FakeContractRunner(
            [fact_response([evidence_id]), fact_response([evidence_id])]
        ),
    )
    first, first_envelope = invoke_json(workspace, ["--yes", "extract"])
    assert first.exit_code == 0
    superseded_id = first_envelope["affected_ids"]["created"][0]["ids"][0]
    second, second_envelope = invoke_json(workspace, ["--yes", "extract"])
    assert second.exit_code == 0
    current_id = second_envelope["affected_ids"]["created"][0]["ids"][0]

    shown, _ = invoke_json(workspace, ["facts", "show", "--fact-id", current_id])
    assert shown.exit_code == 0
    stale, stale_envelope = invoke_json(
        workspace, ["facts", "show", "--fact-id", superseded_id]
    )
    assert stale.exit_code == 2
    assert stale_envelope["diagnostic_class"] == "selector_not_found"
    listed, list_envelope = invoke_json(workspace, ["facts", "list"])
    assert listed.exit_code == 0
    assert [fact["id"] for fact in list_envelope["result"]["facts"]] == [current_id]


def test_extract_interrupt_is_cancelled_without_partial_facts(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6 / §15.10: a foreground interrupt rolls back Stage 3."""

    seed_lineage(workspace, "interrupt")

    def interrupt(_call):
        raise KeyboardInterrupt()

    fake = FakeContractRunner([interrupt])
    install_fake_execution(monkeypatch, fake)
    result, envelope = invoke_json(workspace, ["--yes", "extract"])
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["diagnostic_class"] == "cancelled"
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()
        run = connection.execute(
            "SELECT id, status, failure_code FROM processing_runs"
        ).fetchone()
    assert tuple(run[1:]) == ("failed", "cancelled")
    # §14.14 rules 5/6: the committed cancellation telemetry is reported in
    # the cancelled envelope rather than dropped.
    assert envelope["run_ids"] == [run[0]]
