"""Claude CLI declaration, preflight, sandboxed runner, and failure mapping."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Sequence, TYPE_CHECKING

from exp2res.errors import LLMInvocationError

from .capabilities import (
    CLICapabilityDeclaration,
    CapabilityTestRange,
    parse_cli_version,
    validate_reasoning_effort,
)
from .preflight import CODEX_TOKEN_PATTERNS
from .runner import (
    AttemptTelemetry,
    ContractRunner,
    PreparedCall,
    RawResult,
    _read_output,
    _write_private,
    run_subprocess,
)
from .sandbox import (
    SandboxLayout,
    build_bwrap_command,
    discover_bwrap,
    probe_isolation,
    proxy_environment,
)

if TYPE_CHECKING:
    from exp2res.config import LLMConfig


RUNNER_PROTOCOL_VERSION = 1
REQUIRED_FLAGS = frozenset(
    {
        "-p",
        "--model",
        "--effort",
        "--permission-mode",
        "--output-format",
        "--json-schema",
        "--tools",
        "--settings",
        "--strict-mcp-config",
        "--setting-sources",
        "--safe-mode",
        "--no-session-persistence",
        "--disable-slash-commands",
    }
)
# §15.12 rule 6: the granted Read authority is scoped away from the one
# secret inside the visible set. The `//` absolute deny form is
# probe-verified to block on the tested range while /work reads stay
# allowed; print mode ignores invalid settings silently, so the tested
# version range is what bounds a drifted-syntax residual, layered over
# rule 6's already-accepted auth-material exposure.
CLAUDE_PERMISSION_SETTINGS = (
    '{"permissions":{"deny":["Read(//claude-home/**)"]}}'
)
CLAUDE_TOKEN_PATTERNS = (
    *CODEX_TOKEN_PATTERNS,
    re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
)

CLITestRange = CapabilityTestRange
ClaudeCapabilityDeclaration = CLICapabilityDeclaration

DEFAULT_DECLARATION = ClaudeCapabilityDeclaration(
    required_flags=REQUIRED_FLAGS,
    tested_ranges=(
        CLITestRange(
            minimum=(2, 1, 200),
            maximum_exclusive=(2, 2, 0),
            supported_flags=REQUIRED_FLAGS,
        ),
    ),
    token_patterns=CLAUDE_TOKEN_PATTERNS,
    reasoning_efforts=frozenset({"low", "medium", "high", "xhigh", "max"}),
    credential_form="externally-managed-session",
    runner_protocol_version=RUNNER_PROTOCOL_VERSION,
)


@dataclass(frozen=True)
class _ClaudeEnvelope:
    is_error: bool
    result: object
    terminal_reason: str | None
    api_error_status: int | None
    structured_output: object
    has_structured_output: bool


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _read_envelope(path: Path) -> _ClaudeEnvelope | None:
    raw = _read_output(path)
    if raw is None:
        return None
    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return None
    if not isinstance(decoded, dict) or decoded.get("type") != "result":
        return None
    is_error = decoded.get("is_error")
    if not isinstance(is_error, bool):
        return None
    terminal_reason = decoded.get("terminal_reason")
    if terminal_reason is not None and not isinstance(terminal_reason, str):
        return None
    api_error_status = decoded.get("api_error_status")
    if api_error_status is not None and (
        isinstance(api_error_status, bool) or not isinstance(api_error_status, int)
    ):
        return None
    return _ClaudeEnvelope(
        is_error=is_error,
        result=decoded.get("result"),
        terminal_reason=terminal_reason,
        api_error_status=api_error_status,
        structured_output=decoded.get("structured_output"),
        has_structured_output="structured_output" in decoded,
    )


def _result_matches_structured_output(envelope: _ClaudeEnvelope) -> bool:
    """Require the declared dual success shape: `result` bytes stay the one
    §15.12 rule 7 channel, and the runtime's own `structured_output` echo must
    agree with them — declared-semantics drift fails closed rather than being
    silently adapted to (rule 8)."""

    if not envelope.has_structured_output:
        return False
    try:
        parsed = json.loads(envelope.result)  # type: ignore[arg-type]
    except (json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return False
    return parsed == envelope.structured_output


def _diagnostic_bytes(
    envelope: _ClaudeEnvelope | None, stderr: bytes
) -> bytes:
    fields: dict[str, object] = {}
    if envelope is not None:
        fields = {
            "is_error": envelope.is_error,
            "terminal_reason": envelope.terminal_reason,
            "api_error_status": envelope.api_error_status,
        }
        if envelope.is_error and isinstance(envelope.result, str):
            fields["result"] = envelope.result[:4_096]
    typed = (
        json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if fields
        else b""
    )
    parts = [part for part in (typed[:8_192], stderr[-57_344:]) if part]
    return b"\n".join(parts)[-65_536:]


class ClaudeAgentRunner:
    """Run Claude in a fresh bubblewrap-confined workspace per attempt."""

    def __init__(
        self,
        *,
        claude_binary: Path,
        bwrap_binary: Path,
        claude_config_dir: Path,
        reasoning_effort: str = "high",
    ) -> None:
        self.claude_binary = claude_binary
        self.bwrap_binary = bwrap_binary
        self.claude_config_dir = claude_config_dir
        self.reasoning_effort = reasoning_effort

    def run_contract(self, call: PreparedCall) -> RawResult:
        workspace = Path(tempfile.mkdtemp(prefix="exp2res-llm-"))
        os.chmod(workspace, 0o700)
        try:
            _write_private(workspace / "input.json", call.serialized_input)
            _write_private(workspace / "schema.json", call.json_schema)
            if call.validation_errors is not None:
                _write_private(
                    workspace / "validation_errors.json", call.validation_errors
                )
            try:
                inline_schema = call.json_schema.decode("utf-8")
            except UnicodeError:
                raise LLMInvocationError("capability_mismatch") from None
            claude_command = [
                "/runner/claude",
                "-p",
                call.fixed_instruction,
                "--model",
                call.model_id,
                "--effort",
                self.reasoning_effort,
                "--permission-mode",
                "dontAsk",
                "--output-format",
                "json",
                "--json-schema",
                inline_schema,
                "--tools",
                "Read",
                "--settings",
                CLAUDE_PERMISSION_SETTINGS,
                "--strict-mcp-config",
                "--setting-sources",
                "",
                "--safe-mode",
                "--no-session-persistence",
                "--disable-slash-commands",
            ]
            command = build_bwrap_command(
                SandboxLayout(
                    workspace=workspace,
                    bwrap_binary=self.bwrap_binary,
                    ro_binds=(
                        (self.claude_binary, "/runner/claude"),
                        (
                            self.claude_config_dir / ".credentials.json",
                            "/claude-home/.credentials.json",
                        ),
                    ),
                    tmpfs_mounts=("/claude-home",),
                    top_dirs=("/claude-home", "/runner"),
                    extra_env=(
                        ("CLAUDE_CONFIG_DIR", "/claude-home"),
                        ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"),
                        *proxy_environment(),
                    ),
                    chdir="/work",
                ),
                claude_command,
            )
            timeout = (
                call.budgets.invocation_deadline_seconds
                if call.timeout_seconds is None
                else call.timeout_seconds
            )
            output_path = workspace / "output.json"
            # Claude's native machine-format stdout is its one result envelope.
            # Binding that stream to this dedicated file makes the envelope the
            # rule-7 final-message artifact; only its typed `result` string is
            # returned to contract validation below.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(
                os, "O_NOFOLLOW", 0
            )
            descriptor = os.open(output_path, flags, 0o600)
            os.chmod(output_path, 0o600)
            try:
                outcome = run_subprocess(
                    command,
                    timeout_seconds=timeout,
                    stdout_descriptor=descriptor,
                )
            finally:
                os.close(descriptor)
            envelope = _read_envelope(output_path)
            final_message: bytes | None = None
            if (
                outcome.exit_code == 0
                and envelope is not None
                and envelope.is_error is False
                and isinstance(envelope.result, str)
                and _result_matches_structured_output(envelope)
            ):
                final_message = envelope.result.encode("utf-8")
            error_channel = (
                b""
                if final_message is not None
                else _diagnostic_bytes(envelope, outcome.error_channel)
            )
            attempt = AttemptTelemetry(
                attempt_index=1,
                exit_code=outcome.exit_code,
                duration_seconds=outcome.duration_seconds,
                timed_out=outcome.timed_out,
                cancelled=outcome.cancelled,
            )
            return RawResult(
                final_message_bytes=final_message,
                exit_code=outcome.exit_code,
                duration_seconds=outcome.duration_seconds,
                attempts=(attempt,),
                error_channel=error_channel,
                timed_out=outcome.timed_out,
                cancelled=outcome.cancelled,
                api_error_status=(
                    None if envelope is None else envelope.api_error_status
                ),
            )
        finally:
            try:
                shutil.rmtree(workspace)
            except FileNotFoundError:
                pass


def validate_cli_declaration(
    version_output: str,
    declaration: ClaudeCapabilityDeclaration = DEFAULT_DECLARATION,
) -> str:
    """Validate Claude capability from version plus the tested-range table."""

    if (
        declaration.credential_form != "externally-managed-session"
        or not declaration.structured_outputs_supported
        or not declaration.timeout_supported
        or not declaration.cancellation_supported
        or not declaration.token_patterns
        or not declaration.reasoning_efforts
        or declaration.runner_protocol_version != RUNNER_PROTOCOL_VERSION
    ):
        raise LLMInvocationError("capability_mismatch")
    version = parse_cli_version(version_output)
    for tested in declaration.tested_ranges:
        if tested.minimum <= version < tested.maximum_exclusive:
            if not declaration.required_flags.issubset(tested.supported_flags):
                raise LLMInvocationError("capability_mismatch")
            return ".".join(str(item) for item in version)
    raise LLMInvocationError("capability_mismatch")


def _resolve_claude_binary(value: str | Path | None) -> Path:
    candidate = str(value) if value is not None else shutil.which("claude")
    if candidate is None:
        raise LLMInvocationError("capability_mismatch")
    try:
        resolved = Path(candidate).resolve(strict=True)
        with resolved.open("rb") as stream:
            header = stream.read(4)
    except OSError:
        raise LLMInvocationError("capability_mismatch") from None
    if (
        not resolved.is_file()
        or not os.access(resolved, os.X_OK)
        or header != b"\x7fELF"
    ):
        raise LLMInvocationError("capability_mismatch")
    return resolved


def _preflight_auth(claude_config_dir: Path) -> None:
    credential = claude_config_dir / ".credentials.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(credential, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
                raise OSError("invalid credential file")
        finally:
            os.close(descriptor)
        if credential.is_symlink():
            raise OSError("credential file must not be a symlink")
    except OSError:
        raise LLMInvocationError("transport_auth_failed") from None


def _ambient_configuration_paths(claude_config_dir: Path) -> tuple[Path, ...]:
    candidates = (
        claude_config_dir / "settings.json",
        claude_config_dir / "CLAUDE.md",
        Path("~/.claude.json").expanduser(),
    )
    return tuple(path for path in candidates if path.is_file())


@dataclass(frozen=True)
class AdapterRuntime:
    claude_binary: Path
    bwrap_binary: Path
    claude_config_dir: Path
    cli_version: str


def preflight_adapter(
    *,
    repository_root: Path,
    claude_config_dir: Path,
    claude_binary: str | Path | None = None,
    bwrap_binary: str | Path | None = None,
    declaration: ClaudeCapabilityDeclaration = DEFAULT_DECLARATION,
    ambient_paths: Sequence[Path] = (),
) -> AdapterRuntime:
    """Fail closed on either the Claude runtime or confinement half."""

    claude = _resolve_claude_binary(claude_binary)
    try:
        version_process = subprocess.run(
            [str(claude), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LLMInvocationError("capability_mismatch") from None
    if version_process.returncode != 0:
        raise LLMInvocationError("capability_mismatch")
    version = validate_cli_declaration(version_process.stdout, declaration)
    bwrap = discover_bwrap(None if bwrap_binary is None else Path(bwrap_binary))
    if bwrap is None:
        raise LLMInvocationError("capability_mismatch")
    canary = probe_isolation(
        repository_root=repository_root,
        bwrap_binary=bwrap,
        ambient_paths=(
            *_ambient_configuration_paths(claude_config_dir),
            *ambient_paths,
        ),
    )
    if not canary.available or not canary.effective:
        raise LLMInvocationError("capability_mismatch")
    _preflight_auth(claude_config_dir)
    return AdapterRuntime(claude, bwrap, claude_config_dir, version)


def build_runner(config: LLMConfig, repository_root: Path) -> ContractRunner:
    """Perform complete Claude preflight and return its ready runner."""

    from exp2res.config import resolve_claude_config_dir

    reasoning_effort = validate_reasoning_effort(
        config.reasoning_effort, DEFAULT_DECLARATION
    )
    claude_config_dir = resolve_claude_config_dir(config)
    runtime = preflight_adapter(
        repository_root=repository_root,
        claude_config_dir=claude_config_dir,
        declaration=DEFAULT_DECLARATION,
    )
    return ClaudeAgentRunner(
        claude_binary=runtime.claude_binary,
        bwrap_binary=runtime.bwrap_binary,
        claude_config_dir=runtime.claude_config_dir,
        reasoning_effort=reasoning_effort,
    )


def classify_claude_failure(result: RawResult) -> tuple[str | None, bool]:
    """Map Claude's typed envelope and diagnostics to stable failure classes."""

    if result.timed_out:
        return "transport_timeout", True
    if result.cancelled:
        return "cancelled", False
    status = result.api_error_status
    if status is not None:
        if status == 408:
            return "transport_timeout", True
        if status == 429:
            return "transport_rate_limited", True
        if status in {401, 403}:
            return "transport_auth_failed", False
        if 500 <= status <= 599:
            return "transport_provider_error", True
        if 400 <= status <= 499:
            return "transport_provider_error", False
    if result.exit_code == 0 and result.final_message_bytes is not None:
        return None, False
    channel = result.error_channel.lower()
    if any(
        marker in channel
        for marker in (
            b"not logged in",
            b"please run /login",
            b"invalid api key",
            b"authentication",
            b"unauthorized",
            b"oauth token",
        )
    ):
        return "transport_auth_failed", False
    if any(
        marker in channel
        for marker in (
            b"rate limit",
            b"usage limit",
            b"quota",
            b"too many requests",
        )
    ):
        return "transport_rate_limited", True
    if any(marker in channel for marker in (b"timeout", b"timed out")):
        return "transport_timeout", True
    if any(marker in channel for marker in (b"lost response", b"ambiguous delivery")):
        return "transport_lost_response", True
    if any(
        marker in channel
        for marker in (b"overloaded", b"overload", b"connection", b"tls", b"5xx")
    ):
        return "transport_provider_error", True
    return "transport_provider_error", False
