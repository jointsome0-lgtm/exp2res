"""Claude adapter-specific offline coverage and double-opt-in live smoke."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from typing import Literal

import pytest

import exp2res.llm.claude as claude_adapter
import exp2res.llm.codex as codex_adapter
from exp2res.config import (
    DEFAULT_LLM_CONFIG,
    load_workspace_config,
    resolve_claude_config_dir,
)
from exp2res.domain.models import StrictModel
from exp2res.errors import ConfigurationError, LLMInvocationError
from exp2res.llm.adapter import invoke_contract
from exp2res.llm.claude import (
    CLAUDE_TOKEN_PATTERNS,
    ClaudeAgentRunner,
    _ambient_configuration_paths,
    _preflight_auth,
    _resolve_claude_binary,
    classify_claude_failure,
    validate_cli_declaration,
)
from exp2res.llm.contracts import ContractDefinition, runner_instruction, schema_bytes
from exp2res.llm.preflight import preflight_call
from exp2res.llm.registry import ADAPTER_REGISTRY
from exp2res.llm.runner import PreparedCall, ProcessOutcome
from exp2res.llm.sandbox import discover_bwrap, probe_isolation

from conftest import FIXED_NOW, REPOSITORY_ROOT
from test_llm_runner import (
    CONTRACT,
    INPUT,
    VALID,
    budgets,
    telemetry,
)


pytestmark = pytest.mark.contract


def _prepared(**changes: object) -> PreparedCall:
    call = PreparedCall(
        contract_id=CONTRACT.contract_id,
        serialized_input=INPUT,
        json_schema=schema_bytes(CONTRACT),
        model_id="claude-test-vera-example",
        fixed_instruction=runner_instruction(CONTRACT),
        budgets=budgets(),
    )
    return replace(call, **changes)


def _runner(tmp_path: Path) -> ClaudeAgentRunner:
    binary = tmp_path / "claude-vera-example"
    binary.write_text("Vera Example inert runtime\n", encoding="utf-8")
    binary.chmod(0o700)
    bwrap = tmp_path / "bwrap-vera-example"
    bwrap.write_text("Vera Example inert sandbox\n", encoding="utf-8")
    bwrap.chmod(0o700)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(
        '{"fixture":"Vera Example synthetic session"}', encoding="utf-8"
    )
    return ClaudeAgentRunner(
        claude_binary=binary,
        bwrap_binary=bwrap,
        claude_config_dir=config_dir,
    )


def _emit_envelope(
    monkeypatch: pytest.MonkeyPatch,
    envelope: bytes,
    *,
    exit_code: int = 0,
    stderr: bytes = b"",
) -> None:
    def run(
        _command: list[str],
        *,
        timeout_seconds: float,
        stdout_descriptor=None,
    ) -> ProcessOutcome:
        _ = timeout_seconds
        assert isinstance(stdout_descriptor, int)
        os.write(stdout_descriptor, envelope)
        return ProcessOutcome(exit_code, 0.01, stderr, False, False)

    monkeypatch.setattr(claude_adapter, "run_subprocess", run)


def _json_envelope(**changes: object) -> bytes:
    value: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": VALID.decode("utf-8"),
        "structured_output": {"ignored": "Vera Example"},
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def test_success_extracts_only_the_envelope_result_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _emit_envelope(monkeypatch, _json_envelope())
    result = _runner(tmp_path).run_contract(_prepared())
    assert result.final_message_bytes == VALID
    assert result.error_channel == b""
    assert classify_claude_failure(result) == (None, False)


@pytest.mark.parametrize(
    ("envelope", "expected_code"),
    [
        (
            _json_envelope(
                subtype="success",
                is_error=True,
                result="Not logged in · Please run /login",
                terminal_reason="api_error",
                api_error_status=None,
            ),
            "transport_auth_failed",
        ),
        (
            _json_envelope(result={"value": "Vera Example"}),
            "transport_provider_error",
        ),
        (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": {"value": "Vera Example"},
                }
            ).encode("utf-8"),
            "transport_provider_error",
        ),
        (
            b'{"type":"result","type":"result","is_error":false}',
            "transport_provider_error",
        ),
    ],
    ids=[
        "is-error-beats-success-subtype",
        "non-string-result",
        "structured-output-only",
        "duplicate-key-envelope",
    ],
)
def test_envelope_extraction_fails_closed(
    envelope: bytes,
    expected_code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _emit_envelope(monkeypatch, envelope)
    result = _runner(tmp_path).run_contract(_prepared())
    assert result.final_message_bytes is None
    assert classify_claude_failure(result) == (expected_code, False)


def test_typed_api_status_is_preserved_for_failure_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _emit_envelope(
        monkeypatch,
        _json_envelope(
            is_error=True,
            result="Vera Example provider diagnostic",
            api_error_status=429,
        ),
        exit_code=1,
    )
    result = _runner(tmp_path).run_contract(_prepared())
    assert result.api_error_status == 429
    assert classify_claude_failure(result) == ("transport_rate_limited", True)


def test_auth_preflight_rejects_missing_empty_and_symlink_credentials(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    missing.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / ".credentials.json").touch()
    target = tmp_path / "Vera Example credential target"
    target.write_text("Vera Example nonempty synthetic credential", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".credentials.json").symlink_to(target)

    for config_dir in (missing, empty, linked):
        with pytest.raises(LLMInvocationError) as caught:
            _preflight_auth(config_dir)
        assert caught.value.failure_code == "transport_auth_failed"


def test_unset_config_env_uses_home_default_and_fails_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(DEFAULT_LLM_CONFIG.claude_config_dir_env, raising=False)
    with pytest.raises(LLMInvocationError) as caught:
        resolve_claude_config_dir(DEFAULT_LLM_CONFIG)
    assert caught.value.failure_code == "transport_auth_failed"


def test_explicit_config_env_resolves_without_reading_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    monkeypatch.setenv(
        DEFAULT_LLM_CONFIG.claude_config_dir_env, str(config_dir)
    )
    assert resolve_claude_config_dir(DEFAULT_LLM_CONFIG) == config_dir


def test_native_binary_resolution_follows_symlink_and_rejects_launcher(
    tmp_path: Path,
) -> None:
    native = tmp_path / "claude-native"
    native.write_bytes(b"\x7fELF Vera Example inert native fixture")
    native.chmod(0o700)
    link = tmp_path / "claude"
    link.symlink_to(native)
    assert _resolve_claude_binary(link) == native

    launcher = tmp_path / "claude-launcher"
    launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    launcher.chmod(0o700)
    with pytest.raises(LLMInvocationError) as caught:
        _resolve_claude_binary(launcher)
    assert caught.value.failure_code == "capability_mismatch"


def test_version_declaration_accepts_probed_version_and_rejects_drift() -> None:
    assert validate_cli_declaration("2.1.212 (Claude Code)") == "2.1.212"
    with pytest.raises(LLMInvocationError) as caught:
        validate_cli_declaration("2.2.0 (Claude Code)")
    assert caught.value.failure_code == "capability_mismatch"


def test_sandbox_argv_has_exact_claude_controls_and_inline_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], bytes, bytes, set[str], Path]] = []

    def run(
        command: list[str],
        *,
        timeout_seconds: float,
        stdout_descriptor=None,
    ) -> ProcessOutcome:
        _ = timeout_seconds
        assert isinstance(stdout_descriptor, int)
        bind_index = command.index("--bind")
        workspace = Path(command[bind_index + 1])
        schema = (workspace / "schema.json").read_bytes()
        inline = command[command.index("--json-schema") + 1].encode("utf-8")
        seen.append(
            (
                command,
                schema,
                inline,
                {path.name for path in workspace.iterdir()},
                workspace,
            )
        )
        os.write(stdout_descriptor, _json_envelope())
        return ProcessOutcome(0, 0.01, b"", False, False)

    monkeypatch.setattr(claude_adapter, "run_subprocess", run)
    runner = _runner(tmp_path)
    result = runner.run_contract(_prepared())
    assert result.final_message_bytes == VALID
    command, schema, inline, names, workspace = seen[0]
    assert schema == inline == _prepared().json_schema
    assert names == {"input.json", "schema.json", "output.json"}
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--tools") + 1] == "Read"
    assert command[command.index("--setting-sources") + 1] == ""
    assert "--bare" not in command
    assert "VERA_EXAMPLE_PARENT_CREDENTIAL_SENTINEL" not in command
    tmpfs_index = command.index("--tmpfs", command.index("/claude-home"))
    credential_bind = next(
        index
        for index in range(len(command) - 2)
        if command[index : index + 3]
        == [
            "--ro-bind",
            str(runner.claude_config_dir / ".credentials.json"),
            "/claude-home/.credentials.json",
        ]
    )
    assert tmpfs_index < credential_bind
    assert command[command.index("--chdir") + 1] == "/work"
    assert [
        command[index : index + 3]
        for index, value in enumerate(command[:-2])
        if value == "--setenv"
    ] == [
        ["--setenv", "PATH", "/runner:/usr/local/bin:/usr/bin:/bin"],
        ["--setenv", "HOME", "/work"],
        ["--setenv", "CLAUDE_CONFIG_DIR", "/claude-home"],
        ["--setenv", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"],
    ]
    assert not workspace.exists()


def test_claude_patterns_include_generic_and_sk_ant_classifiers() -> None:
    patterns = {pattern.pattern for pattern in CLAUDE_TOKEN_PATTERNS}
    assert rb"\bsk-[A-Za-z0-9_-]{20,}\b" in patterns
    assert rb"\bsk-ant-[A-Za-z0-9_-]{16,}\b" in patterns
    with pytest.raises(LLMInvocationError) as caught:
        preflight_call(
            _prepared(
                serialized_input=(
                    b'{"subject":"sk-ant-oat01-VeraExampleTokenValue0123456789"}'
                )
            ),
            token_patterns=CLAUDE_TOKEN_PATTERNS,
        )
    assert caught.value.failure_code == "credential_detected"


def test_ambient_canary_paths_cover_existing_claude_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config_dir = home / ".claude"
    config_dir.mkdir(parents=True)
    settings = config_dir / "settings.json"
    instructions = config_dir / "CLAUDE.md"
    global_config = home / ".claude.json"
    for path in (settings, instructions, global_config):
        path.write_text("Vera Example ambient canary\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert _ambient_configuration_paths(config_dir) == (
        settings,
        instructions,
        global_config,
    )


@pytest.mark.parametrize(
    ("adapter", "effort", "builder"),
    [
        ("codex-cli", "max", codex_adapter.build_runner),
        ("claude-agent-sdk", "minimal", claude_adapter.build_runner),
    ],
)
def test_adapter_specific_effort_mismatch_fails_at_build_time(
    adapter: str,
    effort: str,
    builder,
    workspace: Path,
) -> None:
    config_path = workspace / ".exp2res" / "config.toml"
    config_path.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        f'[llm]\nadapter = "{adapter}"\nmodel = "Vera-Example-model"\n'
        f'reasoning_effort = "{effort}"\n\n'
        "[privacy]\nignore_paths = []\n",
        encoding="utf-8",
    )
    config = load_workspace_config(workspace).llm
    with pytest.raises(LLMInvocationError) as caught:
        builder(config, REPOSITORY_ROOT)
    assert caught.value.failure_code == "capability_mismatch"


def test_unknown_effort_outside_registered_union_fails_config_load(
    workspace: Path,
) -> None:
    config_path = workspace / ".exp2res" / "config.toml"
    config_path.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        '[llm]\nadapter = "claude-agent-sdk"\nmodel = "Vera-Example-model"\n'
        'reasoning_effort = "Vera-Example-unknown"\n\n'
        "[privacy]\nignore_paths = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_workspace_config(workspace)


def test_fake_runtime_session_artifacts_die_with_sandbox(
    tmp_path: Path,
) -> None:
    bwrap = discover_bwrap()
    canary = probe_isolation(repository_root=REPOSITORY_ROOT, bwrap_binary=bwrap)
    if not canary.available:
        pytest.skip(
            f"bwrap/userns unavailable: {canary.reason}; runtime preflight fails "
            "closed with capability_mismatch"
        )
    assert canary.effective is True, canary.reason
    runtime = tmp_path / "claude-vera-example"
    runtime.write_text(
        "#!/usr/bin/python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['CLAUDE_CONFIG_DIR'], 'session-artifact').write_text("
        "'Vera Example transient')\n"
        "Path(os.environ['HOME'], 'session-artifact').write_text("
        "'Vera Example transient')\n"
        "print(json.dumps({'type':'result','is_error':False,'result':"
        f"{VALID.decode('utf-8')!r}}}))\n",
        encoding="utf-8",
    )
    runtime.chmod(0o700)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    credential = config_dir / ".credentials.json"
    credential.write_text(
        '{"fixture":"Vera Example synthetic session"}', encoding="utf-8"
    )
    runner = ClaudeAgentRunner(
        claude_binary=runtime,
        bwrap_binary=bwrap,  # type: ignore[arg-type]
        claude_config_dir=config_dir,
    )
    assert runner.run_contract(_prepared()).final_message_bytes == VALID
    assert {path.name for path in config_dir.iterdir()} == {".credentials.json"}


class _LiveOutput(StrictModel):
    value: Literal["Vera Example live structured round trip"]


class _LiveInput(StrictModel):
    subject: str


LIVE_CONTRACT = ContractDefinition(
    contract_id="test-only-vera-example-claude-live",
    output_model=_LiveOutput,
    fixed_instructions=(
        "Read the input and return exactly the schema-constrained literal value."
    ),
    schema_revision="test-live-v1",
    service_owned_fields=frozenset(),
)


@pytest.mark.live
def test_live_claude_full_stack_structured_round_trip(workspace: Path) -> None:
    config_path = workspace / ".exp2res" / "config.toml"
    config_path.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        '[llm]\nadapter = "claude-agent-sdk"\nmodel = "claude-opus-4-8"\n'
        'claude_config_dir_env = "CLAUDE_CONFIG_DIR"\n'
        'reasoning_effort = "high"\n\n'
        "[privacy]\nignore_paths = []\n",
        encoding="utf-8",
    )
    config = load_workspace_config(workspace).llm
    selection = config.selection
    registration = ADAPTER_REGISTRY[selection.adapter]
    runner = registration.build_runner(config, REPOSITORY_ROOT)
    run_id = "run_vera_claude_live_smoke"
    result = invoke_contract(
        workspace=workspace,
        runner=runner,
        contract=LIVE_CONTRACT,
        input_payload=_LiveInput(subject="Vera Example synthetic live smoke"),
        selection=selection,
        budgets=budgets(
            transport_attempt_cap=1,
            invocation_deadline_seconds=180.0,
        ),
        run_id=run_id,
        stage="13.test.live",
        cli_version="2.1.212-live",
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )
    assert result.output == _LiveOutput(
        value="Vera Example live structured round trip"
    )
    run, call = telemetry(workspace, run_id)
    assert run["status"] == call["status"] == "completed"
    assert (run["provider"], run["model"]) == (
        "claude-agent-sdk",
        "claude-opus-4-8",
    )
