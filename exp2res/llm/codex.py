"""Codex CLI declaration, preflight, sandboxed runner, and failure mapping."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
from typing import Sequence, TYPE_CHECKING

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
from .sandbox import SandboxLayout, build_bwrap_command, discover_bwrap, probe_isolation

if TYPE_CHECKING:
    from exp2res.config import LLMConfig


RUNNER_PROTOCOL_VERSION = 1
REQUIRED_FLAGS = frozenset(
    {
        "--output-schema",
        "--output-last-message",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-C",
        "-c",
        "--skip-git-repo-check",
    }
)

CLITestRange = CapabilityTestRange
CodexCapabilityDeclaration = CLICapabilityDeclaration

DEFAULT_DECLARATION = CodexCapabilityDeclaration(
    required_flags=REQUIRED_FLAGS,
    tested_ranges=(
        CLITestRange(
            minimum=(0, 144, 0),
            maximum_exclusive=(0, 145, 0),
            supported_flags=REQUIRED_FLAGS,
        ),
    ),
    token_patterns=CODEX_TOKEN_PATTERNS,
    reasoning_efforts=frozenset({"minimal", "low", "medium", "high", "xhigh"}),
)


class CodexCLIRunner:
    """Run Codex in a fresh bubblewrap-confined workspace per attempt."""

    def __init__(
        self,
        *,
        codex_binary: Path,
        bwrap_binary: Path,
        codex_home: Path,
        reasoning_effort: str = "high",
    ) -> None:
        self.codex_binary = codex_binary
        self.bwrap_binary = bwrap_binary
        self.codex_home = codex_home
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
            codex_command = [
                "/runner/codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "-C",
                "/work",
                "-s",
                "read-only",
                "-c",
                'approval_policy="never"',
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "--output-schema",
                "/work/schema.json",
                "--output-last-message",
                "/work/output.json",
                "--model",
                call.model_id,
                call.fixed_instruction,
            ]
            command = build_bwrap_command(
                SandboxLayout(
                    workspace=workspace,
                    bwrap_binary=self.bwrap_binary,
                    ro_binds=(
                        (self.codex_binary, "/runner/codex"),
                        (self.codex_home / "auth.json", "/codex-home/auth.json"),
                    ),
                    top_dirs=("/codex-home", "/runner"),
                    extra_env=(("CODEX_HOME", "/codex-home"),),
                ),
                codex_command,
            )
            timeout = (
                call.budgets.invocation_deadline_seconds
                if call.timeout_seconds is None
                else call.timeout_seconds
            )
            outcome = run_subprocess(command, timeout_seconds=timeout)
            output: bytes | None = None
            if outcome.exit_code == 0:
                output = _read_output(workspace / "output.json")
            attempt = AttemptTelemetry(
                attempt_index=1,
                exit_code=outcome.exit_code,
                duration_seconds=outcome.duration_seconds,
                timed_out=outcome.timed_out,
                cancelled=outcome.cancelled,
            )
            return RawResult(
                final_message_bytes=output,
                exit_code=outcome.exit_code,
                duration_seconds=outcome.duration_seconds,
                attempts=(attempt,),
                error_channel=outcome.error_channel,
                timed_out=outcome.timed_out,
                cancelled=outcome.cancelled,
            )
        finally:
            try:
                shutil.rmtree(workspace)
            except FileNotFoundError:
                pass


def parse_codex_version(value: str) -> tuple[int, int, int]:
    return parse_cli_version(value)


def validate_cli_declaration(
    version_output: str,
    declaration: CodexCapabilityDeclaration = DEFAULT_DECLARATION,
) -> str:
    """Validate local CLI capability from version plus the tested-range table."""

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
    version = parse_codex_version(version_output)
    for tested in declaration.tested_ranges:
        if tested.minimum <= version < tested.maximum_exclusive:
            if not declaration.required_flags.issubset(tested.supported_flags):
                raise LLMInvocationError("capability_mismatch")
            return ".".join(str(item) for item in version)
    raise LLMInvocationError("capability_mismatch")


def _resolve_executable(value: str | Path | None, fallback: str) -> Path:
    candidate = str(value) if value is not None else shutil.which(fallback)
    if candidate is None:
        raise LLMInvocationError("capability_mismatch")
    try:
        resolved = Path(candidate).resolve(strict=True)
    except OSError:
        raise LLMInvocationError("capability_mismatch") from None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise LLMInvocationError("capability_mismatch")
    return resolved


def _resolve_codex_binary(value: str | Path | None) -> Path:
    """Resolve an npm launcher to its platform-native executable fail-closed."""

    launcher = _resolve_executable(value, "codex")
    try:
        with launcher.open("rb") as stream:
            header = stream.read(4)
    except OSError:
        raise LLMInvocationError("capability_mismatch") from None
    if header == b"\x7fELF" or header in {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    }:
        return launcher
    if launcher.name != "codex.js":
        raise LLMInvocationError("capability_mismatch")
    target = {
        ("Linux", "x86_64"): ("codex-linux-x64", "x86_64-unknown-linux-musl"),
        ("Linux", "aarch64"): ("codex-linux-arm64", "aarch64-unknown-linux-musl"),
        ("Darwin", "x86_64"): ("codex-darwin-x64", "x86_64-apple-darwin"),
        ("Darwin", "arm64"): ("codex-darwin-arm64", "aarch64-apple-darwin"),
    }.get((platform.system(), platform.machine()))
    if target is None:
        raise LLMInvocationError("capability_mismatch")
    package_name, target_triple = target
    package_root = launcher.parent.parent
    candidates = (
        package_root
        / "node_modules"
        / "@openai"
        / package_name
        / "vendor"
        / target_triple
        / "bin"
        / "codex",
        package_root / "vendor" / target_triple / "bin" / "codex",
    )
    for candidate in candidates:
        try:
            native = candidate.resolve(strict=True)
        except OSError:
            continue
        if native.is_file() and os.access(native, os.X_OK):
            return native
    raise LLMInvocationError("capability_mismatch")


def _preflight_auth(codex_home: Path) -> None:
    auth = codex_home / "auth.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(auth, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
                raise OSError("invalid auth file")
        finally:
            os.close(descriptor)
        if auth.is_symlink():
            raise OSError("auth file must not be a symlink")
    except OSError:
        raise LLMInvocationError("transport_auth_failed") from None


def _ambient_configuration_paths(codex_home: Path) -> tuple[Path, ...]:
    """Return existing Codex rules/config files that the canary must deny."""

    candidates = [codex_home / "config.toml", codex_home / "AGENTS.md"]
    rules = codex_home / "rules"
    if rules.is_dir():
        candidates.extend(path for path in rules.rglob("*") if path.is_file())
    return tuple(path for path in candidates if path.is_file())


@dataclass(frozen=True)
class AdapterRuntime:
    codex_binary: Path
    bwrap_binary: Path
    codex_home: Path
    cli_version: str


def preflight_adapter(
    *,
    repository_root: Path,
    codex_home: Path,
    codex_binary: str | Path | None = None,
    bwrap_binary: str | Path | None = None,
    declaration: CodexCapabilityDeclaration = DEFAULT_DECLARATION,
    ambient_paths: Sequence[Path] = (),
) -> AdapterRuntime:
    """Fail closed on either the local CLI half or wrapper/canary half."""

    codex = _resolve_codex_binary(codex_binary)
    try:
        version_process = subprocess.run(
            [str(codex), "--version"],
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
        ambient_paths=(*_ambient_configuration_paths(codex_home), *ambient_paths),
    )
    if not canary.available or not canary.effective:
        raise LLMInvocationError("capability_mismatch")
    _preflight_auth(codex_home)
    return AdapterRuntime(codex, bwrap, codex_home, version)


def build_runner(config: LLMConfig, repository_root: Path) -> ContractRunner:
    """Perform the complete Codex preflight and return its ready runner."""

    from exp2res.config import resolve_codex_home

    reasoning_effort = validate_reasoning_effort(
        config.reasoning_effort, DEFAULT_DECLARATION
    )
    codex_home = resolve_codex_home(config)
    runtime = preflight_adapter(
        repository_root=repository_root,
        codex_home=codex_home,
        declaration=DEFAULT_DECLARATION,
    )
    return CodexCLIRunner(
        codex_binary=runtime.codex_binary,
        bwrap_binary=runtime.bwrap_binary,
        codex_home=runtime.codex_home,
        reasoning_effort=reasoning_effort,
    )


def classify_codex_failure(result: RawResult) -> tuple[str | None, bool]:
    """Map Codex's stderr markers to §15.10 stable failure classes."""

    if result.cancelled:
        return "cancelled", False
    if result.timed_out:
        return "transport_timeout", True
    if result.exit_code == 0 and result.final_message_bytes is not None:
        return None, False
    channel = result.error_channel.lower()
    if any(
        marker in channel
        for marker in (b"408", b"request timeout", b"response timeout")
    ):
        return "transport_timeout", True
    if any(
        marker in channel
        for marker in (
            b"429",
            b"rate limit",
            b"rate_limit",
            b"too many requests",
            b"usage limit",
            b"quota exceeded",
        )
    ):
        return "transport_rate_limited", True
    if any(
        marker in channel
        for marker in (
            b"401",
            b"403",
            b"unauthorized",
            b"authentication",
            b"not logged in",
            b"login required",
            b"invalid api key",
        )
    ):
        return "transport_auth_failed", False
    if any(marker in channel for marker in (b"lost response", b"ambiguous delivery")):
        return "transport_lost_response", True
    retryable = any(
        marker in channel
        for marker in (b"connection", b"tls", b"overload", b"http 5", b" 500")
    )
    return "transport_provider_error", retryable
