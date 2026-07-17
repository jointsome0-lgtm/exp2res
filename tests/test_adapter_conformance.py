"""Provider-parametrized §15.10/§15.12 agent-adapter conformance."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Callable

import pytest

import exp2res.llm.codex as codex_adapter
from exp2res.errors import LLMCancelledError, LLMInvocationError
from exp2res.llm.adapter import invoke_contract
from exp2res.llm.codex import CodexCLIRunner
from exp2res.llm.contracts import runner_instruction, schema_bytes
from exp2res.llm.registry import ADAPTER_REGISTRY, AdapterRegistration, LLMSelection
from exp2res.llm.runner import ContractRunner, PreparedCall, ProcessOutcome, RawResult
from exp2res.llm.sandbox import (
    SandboxLayout,
    build_bwrap_command,
    discover_bwrap,
    probe_isolation,
)

from conftest import FIXED_NOW, REPOSITORY_ROOT
from fakes import FakeContractRunner, assert_timeout_kills_process_group
from test_llm_runner import (
    CONTRACT,
    INPUT,
    INPUT_PAYLOAD,
    VALID,
    budgets,
    enrich,
    telemetry,
)


pytestmark = pytest.mark.contract


Executor = Callable[[list[str]], ProcessOutcome]


@dataclass(frozen=True)
class FailureMarker:
    error_channel: bytes
    exit_code: int
    stable_code: str
    retryable: bool


@dataclass(frozen=True)
class ConformanceTarget:
    registration: AdapterRegistration
    model_id: str
    cli_version: str
    fake_runtime_factory: Callable[[Path, Path | None], ContractRunner]
    install_executor: Callable[[pytest.MonkeyPatch, Executor], None]
    failure_markers: tuple[FailureMarker, ...]
    expected_command_control_flags: frozenset[str]


def _codex_fake_runtime(tmp_path: Path, bwrap_binary: Path | None) -> ContractRunner:
    codex = tmp_path / "codex-vera-example"
    codex.write_text(
        "#!/bin/sh\n"
        "# Vera Example fake Codex runtime\n"
        "output=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then\n"
        "    shift\n"
        "    output=$1\n"
        "  fi\n"
        "  shift\n"
        "done\n"
        "/usr/bin/env > \"$output\"\n"
        "printf '%s\\n' 'Vera Example stdout prose must be ignored'\n",
        encoding="utf-8",
    )
    codex.chmod(0o700)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        '{"fixture":"Vera Example synthetic auth"}', encoding="utf-8"
    )
    bwrap = bwrap_binary or (tmp_path / "bwrap-vera-example")
    if bwrap_binary is None:
        bwrap.write_text("Vera Example inert bwrap executable\n", encoding="utf-8")
        bwrap.chmod(0o700)
    return CodexCLIRunner(
        codex_binary=codex,
        bwrap_binary=bwrap,
        codex_home=codex_home,
    )


def _install_codex_executor(
    monkeypatch: pytest.MonkeyPatch, executor: Executor
) -> None:
    def run(command: list[str], *, timeout_seconds: float) -> ProcessOutcome:
        _ = timeout_seconds
        return executor(command)

    monkeypatch.setattr(codex_adapter, "run_subprocess", run)


CODEX_FAILURE_MARKERS = (
    FailureMarker(b"HTTP 408 request timeout", 1, "transport_timeout", True),
    FailureMarker(b"HTTP 429 rate limit", 1, "transport_rate_limited", True),
    FailureMarker(b"HTTP 401 authentication failed", 1, "transport_auth_failed", False),
    FailureMarker(
        b"ambiguous delivery lost response", 1, "transport_lost_response", True
    ),
    FailureMarker(b"provider connection failed", 1, "transport_provider_error", True),
    FailureMarker(b"TLS handshake failed", 1, "transport_provider_error", True),
    FailureMarker(b"provider overload", 1, "transport_provider_error", True),
    FailureMarker(b"HTTP 500 provider error", 1, "transport_provider_error", True),
    FailureMarker(b"provider rejected request", 1, "transport_provider_error", False),
    FailureMarker(b"", 0, "transport_provider_error", False),
)


CONFORMANCE_TARGETS = (
    ConformanceTarget(
        registration=ADAPTER_REGISTRY["codex-cli"],
        model_id="gpt-test-vera-example",
        cli_version="0.144.4-test",
        fake_runtime_factory=_codex_fake_runtime,
        install_executor=_install_codex_executor,
        failure_markers=CODEX_FAILURE_MARKERS,
        expected_command_control_flags=frozenset({"-s", "--model"}),
    ),
)


def _prepared(**changes: object) -> PreparedCall:
    call = PreparedCall(
        contract_id=CONTRACT.contract_id,
        serialized_input=INPUT,
        json_schema=schema_bytes(CONTRACT),
        model_id="gpt-test-vera-example",
        fixed_instruction=runner_instruction(CONTRACT),
        budgets=budgets(),
    )
    return replace(call, **changes)


def _workspace_from_command(command: list[str]) -> Path:
    bind_index = command.index("--bind")
    return Path(command[bind_index + 1])


def _invoke_target(
    workspace: Path,
    target: ConformanceTarget,
    runner: ContractRunner,
    *,
    run_id: str,
    call_budgets=None,
):
    return invoke_contract(
        workspace=workspace,
        runner=runner,
        contract=CONTRACT,
        input_payload=INPUT_PAYLOAD,
        selection=LLMSelection(target.registration.adapter_id, target.model_id),
        budgets=call_budgets or budgets(),
        run_id=run_id,
        stage="13.test",
        cli_version=target.cli_version,
        enrich=enrich,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def test_declarative_tmpfs_precedes_nested_file_ro_bind(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    credential = tmp_path / "Vera Example credential.json"
    credential.write_text('{"fixture":"Vera Example"}', encoding="utf-8")
    command = build_bwrap_command(
        SandboxLayout(
            workspace=workspace,
            bwrap_binary=tmp_path / "bwrap",
            top_dirs=("/runtime-config",),
            tmpfs_mounts=("/runtime-config",),
            ro_binds=((credential, "/runtime-config/credentials.json"),),
        ),
        ["/runner/fake"],
    )
    tmpfs_index = next(
        index
        for index in range(len(command) - 1)
        if command[index : index + 2] == ["--tmpfs", "/runtime-config"]
    )
    bind_index = next(
        index
        for index in range(len(command) - 2)
        if command[index : index + 3]
        == ["--ro-bind", str(credential), "/runtime-config/credentials.json"]
    )
    assert tmpfs_index < bind_index


@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
def test_conformance_declared_controls_and_isolation_skeleton_are_assembled(
    target: ConformanceTarget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def execute(command: list[str]) -> ProcessOutcome:
        commands.append(command)
        (_workspace_from_command(command) / "output.json").write_bytes(VALID)
        return ProcessOutcome(0, 0.01, b"", False, False)

    target.install_executor(monkeypatch, execute)
    runner = target.fake_runtime_factory(tmp_path, None)
    assert runner.run_contract(_prepared()).final_message_bytes == VALID
    command = commands[0]
    for flag in target.registration.declaration.required_flags:
        assert flag in command
    for flag in target.expected_command_control_flags:
        assert flag in command
    for flag in (
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
    ):
        assert flag in command

@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
def test_conformance_contract_workspace_has_only_protocol_files(
    target: ConformanceTarget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[set[str], Path]] = []

    def execute(command: list[str]) -> ProcessOutcome:
        workspace = _workspace_from_command(command)
        seen.append(({path.name for path in workspace.iterdir()}, workspace))
        (workspace / "output.json").write_bytes(VALID)
        return ProcessOutcome(0, 0.01, b"", False, False)

    target.install_executor(monkeypatch, execute)
    runner = target.fake_runtime_factory(tmp_path, None)
    runner.run_contract(_prepared())
    runner.run_contract(
        _prepared(
            validation_errors=b'{"errors":[{"type":"Vera Example invalid"}]}',
            validation_round=1,
        )
    )
    assert seen[0][0] == {"input.json", "schema.json"}
    assert seen[1][0] == {"input.json", "schema.json", "validation_errors.json"}
    assert all(not workspace.exists() for _names, workspace in seen)


@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
def test_conformance_parent_environment_is_absent_from_real_sandbox_child(
    target: ConformanceTarget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bwrap = discover_bwrap()
    result = probe_isolation(repository_root=REPOSITORY_ROOT, bwrap_binary=bwrap)
    if not result.available:
        pytest.skip(
            f"bwrap/userns unavailable: {result.reason}; runtime preflight fails "
            "closed with capability_mismatch"
        )
    assert result.effective is True, result.reason
    sentinel_name = "VERA_EXAMPLE_PARENT_CREDENTIAL_SENTINEL"
    sentinel_value = "VeraExampleParentCredentialMustBeAbsent"
    monkeypatch.setenv(sentinel_name, sentinel_value)
    runner = target.fake_runtime_factory(tmp_path, bwrap)
    output = runner.run_contract(_prepared()).final_message_bytes
    assert output is not None
    assert sentinel_name.encode("ascii") not in output
    assert sentinel_value.encode("ascii") not in output
    assert b"HOME=/work" in output


@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
def test_conformance_only_final_message_file_is_consumed(
    target: ConformanceTarget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout_prose = b'{"value":"Vera Example wrong stdout result"}'
    emitted_stdout: list[bytes] = []

    def execute(command: list[str]) -> ProcessOutcome:
        emitted_stdout.append(stdout_prose)
        (_workspace_from_command(command) / "output.json").write_bytes(VALID)
        return ProcessOutcome(0, 0.01, b"", False, False)

    target.install_executor(monkeypatch, execute)
    result = target.fake_runtime_factory(tmp_path, None).run_contract(_prepared())
    assert emitted_stdout == [stdout_prose]
    assert result.final_message_bytes == VALID
    assert result.final_message_bytes != stdout_prose


FAILURE_PARAMS = tuple(
    pytest.param(
        target,
        marker,
        id=(
            f"{target.registration.adapter_id}-{marker.stable_code}-{index}"
        ),
    )
    for target in CONFORMANCE_TARGETS
    for index, marker in enumerate(target.failure_markers)
)


@pytest.mark.invariant
@pytest.mark.parametrize(("target", "marker"), FAILURE_PARAMS)
def test_conformance_failure_marker_table(
    target: ConformanceTarget, marker: FailureMarker
) -> None:
    raw = RawResult(
        None,
        marker.exit_code,
        0.01,
        (),
        error_channel=marker.error_channel,
    )
    assert target.registration.classify_failure(raw) == (
        marker.stable_code,
        marker.retryable,
    )


@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
def test_conformance_registration_supplies_default_token_patterns(
    target: ConformanceTarget, workspace: Path
) -> None:
    fake = FakeContractRunner([VALID])
    secret_shaped = INPUT_PAYLOAD.__class__(
        subject="sk-VeraExampleTokenValue0123456789"
    )
    with pytest.raises(LLMInvocationError) as caught:
        invoke_contract(
            workspace=workspace,
            runner=fake,
            contract=CONTRACT,
            input_payload=secret_shaped,
            selection=LLMSelection(target.registration.adapter_id, target.model_id),
            budgets=budgets(),
            run_id=f"run_vera_{target.registration.adapter_id}_token_pattern",
            stage="13.test",
            cli_version=target.cli_version,
            enrich=enrich,
            clock=lambda: FIXED_NOW,
        )
    assert caught.value.failure_code == "credential_detected"
    assert fake.calls == []


@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
def test_conformance_timeout_group_kill_and_interrupt_telemetry(
    target: ConformanceTarget, workspace: Path, tmp_path: Path
) -> None:
    assert_timeout_kills_process_group(tmp_path)

    class InterruptingRunner:
        def run_contract(self, _call: PreparedCall) -> RawResult:
            raise KeyboardInterrupt()

    run_id = f"run_vera_{target.registration.adapter_id}_interrupt"
    with pytest.raises(LLMCancelledError):
        _invoke_target(workspace, target, InterruptingRunner(), run_id=run_id)
    run, call = telemetry(workspace, run_id)
    assert run["status"] == call["status"] == "failed"
    assert run["failure_code"] == call["failure_code"] == "cancelled"


@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
def test_conformance_terminal_metadata_and_raw_error_privacy(
    target: ConformanceTarget, workspace: Path
) -> None:
    raw_sentinel = b"VERA_RAW_ERROR_CHANNEL_SENTINEL_401_authentication"
    result = RawResult(None, 1, 0.01, (), error_channel=raw_sentinel)
    run_id = f"run_vera_{target.registration.adapter_id}_raw_error"
    with pytest.raises(LLMInvocationError) as caught:
        _invoke_target(
            workspace,
            target,
            FakeContractRunner([result]),
            run_id=run_id,
            call_budgets=budgets(transport_attempt_cap=1),
        )
    assert caught.value.failure_code == "transport_auth_failed"
    run, call = telemetry(workspace, run_id)
    metadata = json.loads(run["metadata_json"])
    assert metadata["adapter_id"] == target.registration.adapter_id
    assert metadata["runner_protocol_version"] == str(
        target.registration.declaration.runner_protocol_version
    )
    assert metadata["sandbox_mechanism"] == "bwrap"
    assert metadata["cli_version"] == target.cli_version
    assert run["failure_code"] == call["failure_code"] == "transport_auth_failed"
    database = workspace / ".exp2res" / "exp2res.sqlite"
    assert raw_sentinel not in database.read_bytes()


@pytest.mark.parametrize(
    "target", CONFORMANCE_TARGETS, ids=lambda target: target.registration.adapter_id
)
@pytest.mark.parametrize(
    "terminal_path", ("success", "validation", "transport", "cancellation")
)
def test_conformance_contract_workspace_is_deleted_on_every_terminal_path(
    target: ConformanceTarget,
    terminal_path: str,
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Path] = []
    invalid = b'{"value":"Vera Example","extra":"forbidden"}'

    def execute(command: list[str]) -> ProcessOutcome:
        contract_workspace = _workspace_from_command(command)
        seen.append(contract_workspace)
        if terminal_path == "success":
            (contract_workspace / "output.json").write_bytes(VALID)
            return ProcessOutcome(0, 0.01, b"", False, False)
        if terminal_path == "validation":
            (contract_workspace / "output.json").write_bytes(invalid)
            return ProcessOutcome(0, 0.01, b"", False, False)
        if terminal_path == "transport":
            return ProcessOutcome(1, 0.01, b"HTTP 401 authentication failed", False, False)
        return ProcessOutcome(None, 0.01, b"", False, True)

    target.install_executor(monkeypatch, execute)
    runner = target.fake_runtime_factory(tmp_path, None)
    run_id = f"run_vera_{target.registration.adapter_id}_{terminal_path}"
    if terminal_path == "success":
        _invoke_target(workspace, target, runner, run_id=run_id)
    elif terminal_path == "cancellation":
        with pytest.raises(LLMCancelledError):
            _invoke_target(workspace, target, runner, run_id=run_id)
    else:
        with pytest.raises(LLMInvocationError):
            _invoke_target(
                workspace,
                target,
                runner,
                run_id=run_id,
                call_budgets=budgets(transport_attempt_cap=1),
            )
    assert seen
    assert all(not contract_workspace.exists() for contract_workspace in seen)
