"""Local-only Codex capability, budget, secret, and sandbox preflights."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import re

import pytest

import exp2res.llm.adapter as codex_adapter
from exp2res.config import load_workspace_config, resolve_codex_home
from exp2res.errors import (
    ConfigurationError,
    LLMAdapterNotRegisteredError,
    LLMInvocationError,
    LLMModelInvalidError,
    UnknownLLMAdapterError,
    UnknownLLMConfigKeyError,
)
from exp2res.llm.adapter import (
    CodexCapabilityDeclaration,
    REQUIRED_FLAGS,
    CLITestRange,
    preflight_adapter,
    validate_cli_declaration,
)
from exp2res.llm.contracts import schema_bytes, runner_instruction
from exp2res.llm.preflight import preflight_call
from exp2res.llm.registry import ADAPTER_REGISTRY
from exp2res.llm.runner import PreparedCall
from exp2res.llm.sandbox import probe_isolation
from exp2res.storage.workspace import initialize_workspace

from test_llm_runner import CONTRACT, INPUT, budgets


pytestmark = pytest.mark.contract


def prepared(input_bytes: bytes = INPUT, **budget_overrides: object) -> PreparedCall:
    return PreparedCall(
        contract_id=CONTRACT.contract_id,
        serialized_input=input_bytes,
        json_schema=schema_bytes(CONTRACT),
        model_id="gpt-test-vera-example",
        fixed_instruction=runner_instruction(CONTRACT),
        budgets=budgets(**budget_overrides),
    )


def test_this_build_registers_only_the_codex_adapter() -> None:
    assert tuple(ADAPTER_REGISTRY) == ("codex-cli",)


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


def test_fresh_config_exposes_adapter_model_home_reference_and_all_budgets(
    tmp_path: Path,
) -> None:
    """Issue #110: init writes and loading exposes the §15.13 selection."""

    workspace = tmp_path / "fresh-workspace"
    workspace.mkdir()
    initialize_workspace(workspace)
    text = (workspace / ".exp2res" / "config.toml").read_text(encoding="utf-8")
    assert 'adapter = "codex-cli"' in text
    assert 'model = "gpt-5.6-sol"' in text
    assert "runner =" not in text

    config = load_workspace_config(workspace).llm
    assert config.adapter == "codex-cli"
    assert config.model == "gpt-5.6-sol"
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


def write_llm_config(workspace: Path, llm_lines: str) -> None:
    config = workspace / ".exp2res" / "config.toml"
    config.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        f"[llm]\n{llm_lines}\n\n"
        "[privacy]\nignore_paths = []\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("adapter", ["claude-agent-sdk", "openai-compat"])
def test_known_but_unregistered_adapter_fails_with_distinct_diagnostic(
    workspace: Path, adapter: str
) -> None:
    write_llm_config(
        workspace,
        f'adapter = "{adapter}"\nmodel = "Vera-Example-model"',
    )
    with pytest.raises(LLMAdapterNotRegisteredError) as caught:
        load_workspace_config(workspace)
    assert caught.value.diagnostic_class == "llm_adapter_not_registered"
    assert "not registered in this build" in caught.value.public_message


def test_unknown_adapter_fails_distinct_from_unregistered(workspace: Path) -> None:
    write_llm_config(
        workspace,
        'adapter = "vera-example-fabricated"\nmodel = "Vera-Example-model"',
    )
    with pytest.raises(UnknownLLMAdapterError) as caught:
        load_workspace_config(workspace)
    assert caught.value.diagnostic_class == "llm_adapter_unknown"


@pytest.mark.parametrize(
    "llm_lines, error_type",
    [
        ('runner = "codex-cli"\nmodel = "gpt-5.6-sol"', UnknownLLMConfigKeyError),
        (
            'adapter = "codex-cli"\nmodel = "gpt-5.6-sol"\nVera_Unknown = 1',
            UnknownLLMConfigKeyError,
        ),
        ('adapter = "codex-cli"\nmodel = ""', LLMModelInvalidError),
        ('adapter = "codex-cli"\nmodel = "bad model"', LLMModelInvalidError),
    ],
    ids=["legacy-runner", "unknown-key", "empty-model", "spaced-model"],
)
def test_llm_config_is_closed_and_model_is_strict(
    workspace: Path, llm_lines: str, error_type: type[ConfigurationError]
) -> None:
    write_llm_config(workspace, llm_lines)
    with pytest.raises(error_type):
        load_workspace_config(workspace)


def test_missing_external_codex_session_uses_auth_failure_class(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_workspace_config(workspace).llm
    monkeypatch.delenv(config.codex_home_env, raising=False)
    with pytest.raises(LLMInvocationError) as unresolved:
        resolve_codex_home(config)
    assert unresolved.value.failure_code == "transport_auth_failed"

    empty_home = tmp_path / "empty-codex-home"
    empty_home.mkdir()
    with pytest.raises(LLMInvocationError) as unauthenticated:
        codex_adapter._preflight_auth(empty_home)
    assert unauthenticated.value.failure_code == "transport_auth_failed"


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


@pytest.mark.invariant
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


def test_cost_ceilings_without_declared_pricing_are_inert() -> None:
    """§15.10 rule 5: cost maxima exist only when the provider declares pricing."""

    undeclared = prepared(
        input_cost_per_million=None,
        output_cost_per_million=None,
        per_invocation_cost_ceiling=Decimal("5"),
        per_run_cost_ceiling=Decimal("25"),
    )
    metrics = preflight_call(undeclared)
    assert metrics.conservative_invocation_cost is None
    assert metrics.conservative_run_cost is None

    half_declared = prepared(
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=None,
    )
    with pytest.raises(LLMInvocationError) as caught:
        preflight_call(half_declared)
    assert caught.value.failure_code == "capability_mismatch"


def test_exact_adapter_resolved_credential_is_detected_without_echo() -> None:
    """§29.4: transport-only resolved values are checked against exact input bytes."""

    credential = b"VeraExampleResolvedCredential0123456789"
    call = prepared(b'{"note":"VeraExampleResolvedCredential0123456789"}')
    with pytest.raises(LLMInvocationError) as caught:
        preflight_call(call, resolved_credentials=(credential,))
    assert caught.value.failure_code == "credential_detected"
    assert credential.decode("ascii") not in str(caught.value)


@pytest.mark.invariant
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


@pytest.mark.invariant
@pytest.mark.parametrize(
    "llm_lines",
    [
        'items = [{api_key = "Vera Example literal credential"}]',
        'notes = ["sk-VeraExampleTokenValue0123456789"]',
        'nested = [[{password = "Vera Example literal credential"}]]',
    ],
    ids=["inline-table-array", "token-string-array", "nested-array-table"],
)
def test_credential_values_inside_config_arrays_fail_closed(
    workspace: Path, llm_lines: str
) -> None:
    """§29.2/§29.4: the config boundary reaches values nested in TOML arrays."""

    config = workspace / ".exp2res" / "config.toml"
    config.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        "[privacy]\nignore_paths = []\n\n"
        f"[llm]\n{llm_lines}\n",
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
