"""§14.6 Stage 3 CLI extraction and fact-inspection behavior."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.exports.managed as managed_module
import exp2res.pipeline.stage3 as stage3_module
import exp2res.services.extraction as extraction_service
import exp2res.services.facts as facts_service
from exp2res.cli import app
from exp2res.config import DEFAULT_LLM_CONFIG, load_workspace_config
from exp2res.domain.models import ExperienceFact
from exp2res.errors import LLMCancelledError, LLMInvocationError
from exp2res.llm.codex import AdapterRuntime, CodexCLIRunner
from exp2res.llm.registry import ADAPTER_REGISTRY, AdapterRegistration, LLMSelection
from exp2res.storage.repository import list_experience_facts
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import (
    SELECTION,
    add_log,
    budgets,
    displaced_lineage,
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
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeContractRunner,
    *,
    selection: LLMSelection = SELECTION,
) -> None:
    monkeypatch.setattr(
        extraction_service,
        "build_llm_execution",
        lambda _workspace: (selection, budgets(), fake),
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
    registered = ADAPTER_REGISTRY["codex-cli"]
    build_calls: list[tuple[object, Path]] = []

    def build_runner(config, repository_root):
        build_calls.append((config, repository_root))
        return CodexCLIRunner(
            codex_binary=runtime.codex_binary,
            bwrap_binary=runtime.bwrap_binary,
            codex_home=runtime.codex_home,
        )

    monkeypatch.setitem(
        ADAPTER_REGISTRY,
        "codex-cli",
        AdapterRegistration(
            adapter_id=registered.adapter_id,
            declaration=registered.declaration,
            build_runner=build_runner,
            classify_failure=registered.classify_failure,
        ),
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
    # Preflight is deferred to first use; materializing yields the
    # registered runner over the preflighted runtime.
    assert isinstance(selected_runner, extraction_service.LazyPreflightRunner)
    materialized = selected_runner.materialize()
    assert isinstance(materialized, CodexCLIRunner)
    assert materialized.codex_binary == runtime.codex_binary
    assert materialized.bwrap_binary == runtime.bwrap_binary
    assert materialized.codex_home == runtime.codex_home
    assert build_calls == [
        (load_workspace_config(workspace).llm, Path(__file__).resolve().parent.parent)
    ]
    assert selected_runner.materialize() is materialized


def test_extract_runs_without_yes_or_confirmation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 3: an explicit cost-bearing command does not prompt."""

    evidence_id = seed_lineage(workspace, "promptless")
    fake = FakeContractRunner([fact_response([evidence_id])])
    install_fake_execution(monkeypatch, fake)
    monkeypatch.setattr(
        cli_module.typer,
        "confirm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("routine model work prompted")
        ),
    )

    result, envelope = invoke_json(workspace, ["extract"])
    assert result.exit_code == 0, (result.stderr, envelope)
    assert len(envelope["run_ids"]) == 1
    assert len(fake.calls) == 1


def test_extract_success_reports_standard_fields_and_contract_warnings(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.3 rules 11/12 and §14.14 rule 5: extraction has no result object."""

    evidence_id = seed_lineage(workspace, "happy")
    warning = {
        "type": "limited_support",
        "message": "This fact has only one owner-authored source.",
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


def test_extract_human_mode_prints_each_warning_on_stderr(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: warnings reach the owner without --json, one stderr line."""

    root, _root_items, _correction, correction_items = displaced_lineage(workspace)
    install_fake_execution(
        monkeypatch,
        FakeContractRunner([fact_response([correction_items[0].id])]),
    )
    result = runner.invoke(
        app,
        ["--yes", "--workspace", str(workspace), "extract", "--log-id", root.id],
    )
    assert result.exit_code == 0
    assert (
        f"Warning (displaced_support_unselected): Lineage {root.id}: "
        "no replacement fact selects its displaced-record support "
        "(1 descriptor)." in result.stderr
    )
    assert "Warning (" not in result.stdout


def test_human_mode_warning_escapes_line_breaks_onto_one_line(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: a multiline message stays one visibly escaped line."""

    evidence_id = seed_lineage(workspace, "multiline")
    warning = {
        "type": "limited_support",
        "message": "First line.\nSecond line.\rCarriage.",
    }
    install_fake_execution(
        monkeypatch,
        FakeContractRunner([fact_response([evidence_id], warnings=[warning])]),
    )
    result = runner.invoke(
        app, ["--yes", "--workspace", str(workspace), "extract"]
    )
    assert result.exit_code == 0
    assert (
        "Warning (limited_support): First line.\\nSecond line.\\rCarriage."
        in result.stderr
    )
    assert result.stderr.count("Warning (") == 1


def test_post_commit_interrupt_keeps_the_committed_result_and_warning(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rules 5/6: cleanup interrupt after the swap drops no warning."""

    root, _root_items, _correction, correction_items = displaced_lineage(workspace)
    install_fake_execution(
        monkeypatch,
        FakeContractRunner([fact_response([correction_items[0].id])]),
    )

    def interrupt_cleanup(*_arguments, **_keywords):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        managed_module, "remove_assessment_sets", interrupt_cleanup
    )
    result, envelope = invoke_json(
        workspace, ["--yes", "extract", "--log-id", root.id]
    )
    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    # The committed replacement generation and its rule-14 warning survive
    # into the cancelled envelope instead of vanishing with the Outcome.
    created = envelope["affected_ids"]["created"]
    assert created and created[0]["entity_type"] == "experience_fact"
    assert [item["type"] for item in envelope["warnings"]] == [
        "displaced_support_unselected"
    ]
    assert envelope["generation_ids"]
    assert len(envelope["run_ids"]) == 1
    with read_database(workspace) as connection:
        assert len(list_experience_facts(connection)) == 1


def test_extract_unknown_selector_has_no_run_row(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.6/§14.14 rule 4: selector failure precedes adapter preflight."""

    def refuse_build(_workspace: Path):
        raise AssertionError("adapter preflight ran for an invalid selector")

    monkeypatch.setattr(extraction_service, "build_llm_execution", refuse_build)
    result, envelope = invoke_json(
        workspace, ["--yes", "extract", "--log-id", "log_vera_missing"]
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "selector_not_found"
    assert envelope["run_ids"] == []
    # The selector resolves before adapter construction whether or not the
    # unrelated global --yes control is present.
    without_yes, without_yes_envelope = invoke_json(
        workspace, ["extract", "--log-id", "log_vera_missing"]
    )
    assert without_yes.exit_code == 2
    assert without_yes_envelope["diagnostic_class"] == "selector_not_found"
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


def test_facts_show_human_renders_content_and_leaves_the_envelope_unchanged(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.6: human mode carries the fact's meaning, JSON stays byte-equal."""

    evidence_id = seed_lineage(workspace, "human")
    install_fake_execution(
        monkeypatch,
        FakeContractRunner([fact_response([evidence_id])]),
    )
    extracted, extraction_envelope = invoke_json(workspace, ["--yes", "extract"])
    assert extracted.exit_code == 0
    fact_id = extraction_envelope["affected_ids"]["created"][0]["ids"][0]

    machine, envelope = invoke_json(
        workspace, ["facts", "show", "--fact-id", fact_id]
    )
    human = runner.invoke(
        app, ["--workspace", str(workspace), "facts", "show", "--fact-id", fact_id]
    )
    assert machine.exit_code == human.exit_code == 0
    fact = envelope["result"]["facts"][0]

    # Every field the machine projection carries, except the inert service
    # metadata map, is legible without a formatter (#156).
    assert f"Fact {fact['id']}" in human.stdout
    for label, value in (
        ("Claim", fact["claim"]),
        ("Claim kind", fact["claim_kind"]),
        ("Ownership level", fact["ownership_level"]),
        ("Context", fact["context"]),
        ("Confidence", fact["confidence"]),
        ("Source logs", ", ".join(fact["source_log_ids"])),
        ("Evidence items", ", ".join(fact["evidence_item_ids"])),
    ):
        assert f"{label}: {value}" in human.stdout
    # Human mode renders the instant with an explicit offset (`+00:00`) where
    # the envelope uses the equivalent `Z`; the placement itself is the same.
    assert fact["occurred"]["start"][:19] in human.stdout
    assert f"({fact['occurred']['precision']}, confidence " in human.stdout
    assert "metadata" not in human.stdout

    # The JSON side is untouched by the human rendering (#156's acceptance).
    reread, reread_envelope = invoke_json(
        workspace, ["facts", "show", "--fact-id", fact_id]
    )
    assert reread.exit_code == 0
    assert reread_envelope == envelope
    assert set(fact) == set(ExperienceFact.model_fields)


@pytest.mark.unit
def test_llm_failure_exit_classes_follow_rule_4() -> None:
    """§14.14 rule 4: local validation/integrity codes are class 7, not 6."""

    assert LLMInvocationError("response_validation_failed").exit_code == 7
    assert LLMInvocationError("business_commit_failed").exit_code == 7
    assert LLMInvocationError("deterministic_enrichment_failed").exit_code == 7
    assert LLMInvocationError("budget_exceeded").exit_code == 6
    assert LLMInvocationError("capability_mismatch").exit_code == 6
    assert LLMCancelledError().exit_code == 9


def test_extract_on_empty_workspace_completes_without_adapter_preflight(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.6: a zero-lineage extraction never probes Codex/bwrap/auth."""

    def refuse_preflight(*_args):
        raise AssertionError("adapter preflight ran for a zero-call extraction")

    registered = ADAPTER_REGISTRY["codex-cli"]
    monkeypatch.setitem(
        ADAPTER_REGISTRY,
        "codex-cli",
        AdapterRegistration(
            adapter_id=registered.adapter_id,
            declaration=registered.declaration,
            build_runner=refuse_preflight,
            classify_failure=registered.classify_failure,
        ),
    )
    # §29.2 selection stays required — the fixture config already carries it;
    # only the environment probe is deferred.
    result, envelope = invoke_json(workspace, ["--yes", "extract"])
    assert result.exit_code == 0
    assert envelope["status"] == "ok"
    assert envelope["affected_ids"]["created"] == []
    assert envelope["generation_ids"] == []
    assert len(envelope["run_ids"]) == 1
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT id, status FROM processing_runs"
        ).fetchone()
        calls = connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert tuple(run) == (envelope["run_ids"][0], "completed")
    assert calls == 0


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
    # §14.14 rule 5: the rerun's envelope carries produced AND invalidated
    # generation IDs, duplicate-free.
    assert set(first_envelope["generation_ids"]) < set(
        second_envelope["generation_ids"]
    )
    assert len(second_envelope["generation_ids"]) == 2

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


def _plant_current_branch(workspace: Path):
    """One current view with one branch on it, for the §13.13 rule 9 report."""

    from test_branch_substrate import plant_branch, plant_job_description
    from test_stage6_assessment import (
        assessment_response,
        prepare_graph,
        run_stage6,
    )

    ids, facts = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    plant_job_description(workspace)
    branch_id, bullet_id = plant_branch(
        workspace, snapshot_id=assessed.snapshot_id, fact_ids=facts
    )
    return assessed.snapshot_id, branch_id, bullet_id


BRANCH_COMMAND_SHAPE = (
    "exp2res bullets generate --jd 'jd_vera_0001' "
    "--snapshot <new-snapshot-id> --branch 'agent-engineer'"
)


def test_extract_reports_invalidated_branches_in_the_json_envelope(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5/§13.13 rule 9: the branch report reaches the envelope."""

    snapshot_id, branch_id, bullet_id = _plant_current_branch(workspace)
    install_fake_execution(
        monkeypatch, FakeContractRunner([fact_response(["evi_vera_signal_0"])])
    )

    result, envelope = invoke_json(
        workspace, ["extract", "--log-id", "log_vera_signal_0"]
    )

    assert result.exit_code == 0
    assert envelope["invalidated_branches"] == [
        {
            "name": "agent-engineer",
            "job_description_id": "jd_vera_0001",
            "former_view": {"scope": "global", "snapshot_id": snapshot_id},
            "regeneration_command_shape": BRANCH_COMMAND_SHAPE,
        }
    ]
    superseded = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["superseded"]
    }
    assert superseded["resume_branch"] == [branch_id]
    assert superseded["resume_bullet"] == [bullet_id]


def test_extract_prints_the_branch_report_in_human_mode(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: human mode reports the same §13.13 rule 9 context."""

    snapshot_id, _branch_id, _bullet_id = _plant_current_branch(workspace)
    install_fake_execution(
        monkeypatch, FakeContractRunner([fact_response(["evi_vera_signal_0"])])
    )

    result = runner.invoke(
        app,
        ["--workspace", str(workspace), "extract", "--log-id", "log_vera_signal_0"],
    )

    assert result.exit_code == 0
    assert (
        "Invalidated bullet-pack branch agent-engineer for job description "
        f"jd_vera_0001, generated against assessment view {snapshot_id} "
        f"(global); regenerate with: {BRANCH_COMMAND_SHAPE}"
    ) in result.stdout
