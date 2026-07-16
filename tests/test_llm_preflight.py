"""Local-only Codex capability, budget, secret, and sandbox preflights."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import re

import pytest

import exp2res.llm.adapter as codex_adapter
from exp2res.config import load_workspace_config
from exp2res.errors import ConfigurationError, LLMInvocationError
from exp2res.llm.adapter import (
    CodexCapabilityDeclaration,
    REQUIRED_FLAGS,
    CLITestRange,
    preflight_adapter,
    validate_cli_declaration,
)
from exp2res.llm.contracts import schema_bytes, runner_instruction
from exp2res.llm.preflight import preflight_call
from exp2res.llm.runner import PreparedCall
from exp2res.llm.sandbox import probe_isolation

from test_llm_runner import CONTRACT, INPUT, budgets


def prepared(input_bytes: bytes = INPUT, **budget_overrides: object) -> PreparedCall:
    return PreparedCall(
        contract_id=CONTRACT.contract_id,
        serialized_input=input_bytes,
        json_schema=schema_bytes(CONTRACT),
        model_id="gpt-test-vera-example",
        fixed_instruction=runner_instruction(CONTRACT),
        budgets=budgets(**budget_overrides),
    )


def test_cli_version_table_fails_closed_when_a_required_flag_is_undeclared() -> None:
    """Issue #69: local version declaration, never provider discovery, gates flags."""

    declaration = CodexCapabilityDeclaration(
        required_flags=REQUIRED_FLAGS,
        tested_ranges=(
            CLITestRange(
                (0, 144, 0),
                (0, 145, 0),
                REQUIRED_FLAGS - {"--output-schema"},
            ),
        ),
        token_patterns=(re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),),
    )
    with pytest.raises(LLMInvocationError) as caught:
        validate_cli_declaration("codex-cli 0.144.4", declaration)
    assert caught.value.failure_code == "capability_mismatch"


def test_fresh_config_exposes_runner_model_home_reference_and_all_budgets(
    workspace: Path,
) -> None:
    """Issue #69: runner settings and numeric §15.10 budgets are local config."""

    config = load_workspace_config(workspace).llm
    assert config.runner == "codex-cli"
    assert config.model is None
    assert config.codex_home_env == "CODEX_HOME"
    assert config.transport_attempt_cap > 0
    assert 0 <= config.backoff_lower_seconds <= config.backoff_upper_seconds
    assert config.invocation_deadline_seconds > 0
    assert config.max_input_bytes > 0
    assert config.input_token_budget > 0
    assert config.output_token_budget > 0
    assert config.per_run_call_ceiling > 0
    assert config.per_invocation_cost_ceiling is not None
    assert config.per_invocation_cost_ceiling > 0
    assert config.per_run_cost_ceiling is not None
    assert config.per_run_cost_ceiling > 0


def test_missing_bwrap_fails_capability_before_any_canary_or_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #69: wrapper availability is the second fail-closed half."""

    codex = tmp_path / "codex-vera-example"
    codex.write_text("#!/bin/sh\necho codex-cli 0.144.4\n", encoding="utf-8")
    codex.chmod(0o700)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        '{"fixture":"Vera Example synthetic auth metadata"}', encoding="utf-8"
    )
    monkeypatch.setattr("exp2res.llm.adapter._resolve_codex_binary", lambda _value: codex)
    with pytest.raises(LLMInvocationError) as caught:
        preflight_adapter(
            repository_root=Path(__file__).resolve().parent.parent,
            codex_home=codex_home,
            codex_binary=codex,
            bwrap_binary=tmp_path / "missing-bwrap",
        )
    assert caught.value.failure_code == "capability_mismatch"


def test_npm_launcher_resolves_to_the_native_binary_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #69: the sandbox binds the native CLI, not an ambient Node tree."""

    package = tmp_path / "codex"
    launcher = package / "bin" / "codex.js"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/usr/bin/env node\n// Vera Example launcher\n", encoding="utf-8")
    launcher.chmod(0o700)
    native = (
        package
        / "node_modules"
        / "@openai"
        / "codex-linux-x64"
        / "vendor"
        / "x86_64-unknown-linux-musl"
        / "bin"
        / "codex"
    )
    native.parent.mkdir(parents=True)
    native.write_bytes(b"\x7fELF Vera Example inert native fixture")
    native.chmod(0o700)
    monkeypatch.setattr(codex_adapter.platform, "system", lambda: "Linux")
    monkeypatch.setattr(codex_adapter.platform, "machine", lambda: "x86_64")
    assert codex_adapter._resolve_codex_binary(launcher) == native


@pytest.mark.parametrize(
    ("call", "failure_code"),
    [
        (prepared(max_input_bytes=len(INPUT) - 1), "budget_exceeded"),
        (prepared(input_token_budget=1), "budget_exceeded"),
        (prepared(planned_output_tokens=5000), "budget_exceeded"),
        (prepared(planned_call_count=11), "budget_exceeded"),
        (
            prepared(per_invocation_cost_ceiling=Decimal("0.000001")),
            "budget_exceeded",
        ),
        (
            prepared(model_context_tokens=100, planned_output_tokens=90),
            "context_overflow",
        ),
        (
            prepared(b'{"authorization":"Vera Example forbidden bearer"}'),
            "credential_detected",
        ),
        (
            prepared(b'{"note":"-----BEGIN PRIVATE KEY-----\\nVera Example"}'),
            "credential_detected",
        ),
        (
            prepared(b'{"note":"sk-VeraExampleTokenValue0123456789"}'),
            "credential_detected",
        ),
    ],
    ids=[
        "oversize-input",
        "input-token-budget",
        "output-token-budget",
        "run-call-ceiling",
        "invocation-cost-ceiling",
        "context-window",
        "credential-field",
        "pem-marker",
        "adapter-token-format",
    ],
)
def test_size_budget_context_and_secret_preflight_codes(
    call: PreparedCall, failure_code: str
) -> None:
    """§15.10/§29.4: exact bytes fail locally with stable non-content codes."""

    with pytest.raises(LLMInvocationError) as caught:
        preflight_call(
            call,
            token_patterns=(re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),),
        )
    assert caught.value.failure_code == failure_code
    assert "Vera Example" not in str(caught.value)


def test_exact_adapter_resolved_credential_is_detected_without_echo() -> None:
    """§29.4: transport-only resolved values are checked against exact input bytes."""

    credential = b"VeraExampleResolvedCredential0123456789"
    call = prepared(b'{"note":"VeraExampleResolvedCredential0123456789"}')
    with pytest.raises(LLMInvocationError) as caught:
        preflight_call(call, resolved_credentials=(credential,))
    assert caught.value.failure_code == "credential_detected"
    assert credential.decode("ascii") not in str(caught.value)


@pytest.mark.parametrize(
    "key",
    ["apiKey", "access-token", "REFRESH_TOKEN", "password", "authorization"],
)
def test_normalized_literal_credential_fields_in_config_fail_closed(
    workspace: Path, key: str
) -> None:
    """§29.4: normalized credential-shaped config keys never hold values."""

    config = workspace / ".exp2res" / "config.toml"
    config.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        "[privacy]\nignore_paths = []\n\n"
        f'[llm]\n{json.dumps(key)} = "Vera Example literal credential"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_workspace_config(workspace)


def test_canary_proves_closed_read_namespace_or_skips_explicitly(
    tmp_path: Path,
) -> None:
    """§21.50: provider-free bwrap canary is CI-safe and never weakens runtime."""

    repository = tmp_path / "repository"
    (repository / ".exp2res").mkdir(parents=True)
    (repository / "exp2res").mkdir()
    for relative in (
        "SDD.md",
        ".env",
        ".exp2res/exp2res.sqlite",
        "exp2res/__init__.py",
        "pyproject.toml",
    ):
        (repository / relative).write_text(
            "Vera Example planted isolation canary\n", encoding="utf-8"
        )
    rules = tmp_path / "AGENTS.md"
    rules.write_text("Vera Example user Codex rules\n", encoding="utf-8")
    result = probe_isolation(repository_root=repository, user_rules_path=rules)
    if not result.available:
        pytest.skip(
            f"bwrap/userns unavailable: {result.reason}; runtime preflight fails "
            "closed with capability_mismatch"
        )
    assert result.effective is True, result.reason
