"""Public Phase 0 CLI envelope and non-interactive behavior tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import typer
from typer.testing import CliRunner

from exp2res.cli import app, main
from exp2res.storage.workspace import initialize_workspace

from conftest import FIXED_NOW, VERA_CORPUS


runner = CliRunner()


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
    )
    return result, json.loads(result.stdout)


def test_cli_daily_demo_lists_only_raw_text_free_projection(workspace: Path) -> None:
    """§21.41; §24.1 / §24.44: Vera Example capture/list JSON demo."""
    source = VERA_CORPUS / "logs" / "daily-2026-06-09.md"
    captured, capture_envelope = invoke_json(
        workspace,
        ["log", "today", "--file", str(source), "--project", "K8s Playbook"],
    )
    assert captured.exit_code == 0
    assert capture_envelope["command"] == "log today"
    assert capture_envelope["status"] == "ok"
    assert capture_envelope["result"] is None
    assert [group["entity_type"] for group in capture_envelope["affected_ids"]["created"]] == [
        "evidence_item",
        "raw_log",
    ]

    listed, list_envelope = invoke_json(workspace, ["logs", "list"])
    assert listed.exit_code == 0
    assert list_envelope["command"] == "logs list"
    assert list_envelope["result"]["logs"][0]["entry_type"] == "manual_daily"
    serialized = listed.stdout + listed.stderr
    assert source.read_text(encoding="utf-8") not in serialized
    assert "raw_text" not in listed.stdout
    assert "metadata" not in listed.stdout
    assert "external_ref" not in listed.stdout


def test_noninteractive_capture_and_delete_never_prompt_or_block(workspace: Path) -> None:
    """§21.41; §24.44: missing prompts/consent fail with exit class 2."""
    daily, daily_envelope = invoke_json(workspace, ["--no-input", "log", "today"])
    retro, retro_envelope = invoke_json(workspace, ["--no-input", "log", "retro"])
    assert daily.exit_code == retro.exit_code == 2
    assert daily_envelope["diagnostic_class"] == "input_required"
    assert retro_envelope["diagnostic_class"] == "input_required"
    assert "Describe what happened" not in daily.stdout + daily.stderr
    assert "What period" not in retro.stdout + retro.stderr

    source = VERA_CORPUS / "logs" / "daily-2026-06-20.md"
    captured, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    log_id = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    denied, denied_envelope = invoke_json(
        workspace, ["--no-input", "logs", "delete", "--log-id", log_id]
    )
    assert captured.exit_code == 0
    assert denied.exit_code == 2
    assert denied_envelope["diagnostic_class"] == "input_required"


def test_correction_is_stably_deferred_without_model_or_mutation(workspace: Path) -> None:
    """§22 Phase 0 / §24.30: correction is a stable Phase-2 refusal."""
    result, envelope = invoke_json(
        workspace, ["correction", "add", "--log-id", "log_vera_example"]
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "operation_deferred_phase_2"
    listed, listed_envelope = invoke_json(workspace, ["logs", "list"])
    assert listed.exit_code == 0
    assert listed_envelope["result"]["logs"] == []


def test_json_workspace_failure_is_one_closed_envelope(tmp_path: Path) -> None:
    """§21.41; §24.44: missing workspace returns stable class 3 JSON."""
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(tmp_path / "missing"), "logs", "list"],
    )
    envelope = json.loads(result.stdout)
    assert result.exit_code == 3
    assert envelope["envelope_version"] == 1
    assert envelope["command"] == "logs list"
    assert envelope["workspace"] is None
    assert envelope["diagnostic_class"] == "workspace_not_found"
    assert envelope["affected_ids"] == {
        "created": [],
        "deleted": [],
        "superseded": [],
    }


def test_confirmed_owner_delete_reports_closed_raw_free_result(workspace: Path) -> None:
    """§21.11 / §21.41; §24.3 / §24.44: deletion envelope is complete and private."""
    source = VERA_CORPUS / "logs" / "daily-2026-06-25.md"
    _, capture_envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    log_id = next(
        group["ids"][0]
        for group in capture_envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    deleted, envelope = invoke_json(
        workspace, ["--yes", "logs", "delete", "--log-id", log_id]
    )
    assert deleted.exit_code == 0
    assert envelope["command"] == "logs delete"
    assert envelope["result"]["selected_log"]["id"] == log_id
    assert envelope["result"]["selected_log"]["external_ref"] == str(source)
    assert envelope["affected_ids"]["deleted"][1] == {
        "entity_type": "raw_log",
        "ids": [log_id],
    }
    assert source.read_text(encoding="utf-8") not in deleted.stdout + deleted.stderr
    assert "raw_text" not in deleted.stdout


def test_local_time_requires_workspace_config_and_ignores_ambient_timezone(
    tmp_path: Path, monkeypatch
) -> None:
    """§21.41 / §21.42; §24.44 / §24.45: no ambient timezone default exists."""
    root = tmp_path / "unconfigured-workspace"
    root.mkdir()
    initialize_workspace(root, clock=lambda: FIXED_NOW)
    monkeypatch.setenv("TZ", "Europe/Moscow")
    source = VERA_CORPUS / "logs" / "daily-2026-06-02.md"
    result, envelope = invoke_json(
        root, ["log", "today", "--file", str(source)]
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "workspace_timezone_required"
    listed, listed_envelope = invoke_json(root, ["logs", "list"])
    assert listed.exit_code == 0
    assert listed_envelope["result"]["logs"] == []


def test_literal_credential_config_fails_without_echo(workspace: Path) -> None:
    """§21.41; §24.30 / §24.44: config secrets fail closed and stay out of logs."""
    credential = "Vera Example synthetic credential sentinel"
    config = workspace / ".exp2res" / "config.toml"
    config.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        "[privacy]\nignore_paths = []\n\n"
        f'[llm]\napi_key = "{credential}"\n',
        encoding="utf-8",
    )
    source = VERA_CORPUS / "logs" / "daily-2026-06-02.md"
    result, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    assert result.exit_code == 7
    assert envelope["diagnostic_class"] == "configuration_invalid"
    assert credential not in result.stdout + result.stderr


def test_console_entry_point_exits_with_contract_code(tmp_path: Path, monkeypatch) -> None:
    """PR #95 review r2: the installed entry point returns §14.14 codes, not a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["exp2res", "--json", "db", "status"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 3


def test_interactive_capture_validates_timezone_before_owner_text(
    tmp_path: Path, monkeypatch
) -> None:
    """PR #95 review r2: a doomed local-time capture never collects owner text."""
    root = tmp_path / "unconfigured-workspace"
    root.mkdir()
    initialize_workspace(root, clock=lambda: FIXED_NOW)
    monkeypatch.setattr("exp2res.cli._noninteractive", lambda _controls: False)

    def refuse_prompt(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("owner text was requested before timezone validation")

    monkeypatch.setattr(typer, "prompt", refuse_prompt)
    for command in (["log", "today"], ["log", "retro"]):
        result, envelope = invoke_json(root, command)
        assert result.exit_code == 2
        assert envelope["diagnostic_class"] == "workspace_timezone_required"
