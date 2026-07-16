"""Narrow, secret-safe workspace configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from .domain.models import validate_structural
from .errors import (
    ConfigurationError,
    InvalidInputError,
    LLMInvocationError,
    UnknownLLMConfigKeyError,
)
from .llm.registry import LLMSelection, resolve_selection
from .llm.preflight import (
    CODEX_TOKEN_PATTERNS,
    looks_like_literal_credential,
    normalize_credential_field,
)
from .llm.runner import CallBudgets

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # exercised by the supported Python 3.10 test environment
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class LLMConfig:
    adapter: str
    model: str
    codex_home_env: str
    transport_attempt_cap: int
    backoff_lower_seconds: float
    backoff_upper_seconds: float
    invocation_deadline_seconds: float
    max_input_bytes: int
    input_token_budget: int
    output_token_budget: int
    per_run_call_ceiling: int
    per_invocation_cost_ceiling: Decimal | None
    per_run_cost_ceiling: Decimal | None

    @property
    def selection(self) -> LLMSelection:
        return LLMSelection(adapter=self.adapter, model=self.model)


@dataclass(frozen=True)
class WorkspaceConfig:
    timezone: str | None
    ignore_paths: tuple[str, ...]
    llm: LLMConfig


DEFAULT_LLM_CONFIG = LLMConfig(
    adapter="codex-cli",
    model="gpt-5.6-sol",
    codex_home_env="CODEX_HOME",
    transport_attempt_cap=2,
    backoff_lower_seconds=0.25,
    backoff_upper_seconds=2.0,
    invocation_deadline_seconds=120.0,
    max_input_bytes=1_048_576,
    input_token_budget=120_000,
    output_token_budget=8_192,
    per_run_call_ceiling=100,
    per_invocation_cost_ceiling=Decimal("5"),
    per_run_cost_ceiling=Decimal("25"),
)


def _parse_toml(data: bytes) -> dict[str, Any]:
    return tomllib.loads(data.decode("utf-8"))


def load_workspace_config(workspace: Path) -> WorkspaceConfig:
    path = workspace / ".exp2res" / "config.toml"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ConfigurationError()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(1_048_577)
            if len(data) > 1_048_576:
                raise ConfigurationError()
        finally:
            os.close(descriptor)
        if path.is_symlink():
            raise ConfigurationError()
        parsed = _parse_toml(data)
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise ConfigurationError() from error

    def reject_credential_value(value: Any) -> None:
        if isinstance(value, str):
            if looks_like_literal_credential(
                value, token_patterns=CODEX_TOKEN_PATTERNS
            ):
                raise ConfigurationError()
        elif isinstance(value, dict):
            reject_literal_credentials(value)
        elif isinstance(value, list):
            # TOML arrays may nest inline tables and further arrays; the
            # fail-closed boundary covers every reachable value.
            for item in value:
                reject_credential_value(item)

    def reject_literal_credentials(section: dict[str, Any]) -> None:
        for key, value in section.items():
            if normalize_credential_field(key) in {
                "api_key",
                "access_token",
                "refresh_token",
                "secret",
                "password",
                "authorization",
            } and value:
                raise ConfigurationError()
            reject_credential_value(value)

    for section in parsed.values():
        if not isinstance(section, dict):
            raise ConfigurationError()
        reject_literal_credentials(section)

    workspace_section = parsed.get("workspace", {})
    privacy_section = parsed.get("privacy", {})
    llm_section = parsed.get("llm", {})
    if (
        not isinstance(workspace_section, dict)
        or not isinstance(privacy_section, dict)
        or not isinstance(llm_section, dict)
    ):
        raise ConfigurationError()

    timezone = workspace_section.get("timezone")
    if timezone == "":
        timezone = None
    if timezone is not None:
        if not isinstance(timezone, str):
            raise ConfigurationError()
        try:
            validate_structural(timezone)
        except ValueError as error:
            raise ConfigurationError() from error

    ignore_paths = privacy_section.get("ignore_paths", [])
    if not isinstance(ignore_paths, list) or not all(
        isinstance(value, str) for value in ignore_paths
    ):
        raise ConfigurationError()
    validated: list[str] = []
    try:
        for pattern in ignore_paths:
            validate_structural(pattern)
            if "\\" in pattern or pattern.startswith("/"):
                raise ValueError("unsupported ignore pattern")
            validated.append(pattern)
    except ValueError as error:
        raise ConfigurationError() from error

    def integer(name: str, default: int) -> int:
        value = llm_section.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigurationError()
        return value

    def number(name: str, default: float, *, allow_zero: bool) -> float:
        value = llm_section.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigurationError()
        result = float(value)
        if not math.isfinite(result) or result < 0 or (not allow_zero and result == 0):
            raise ConfigurationError()
        return result

    def optional_decimal(name: str, default: Decimal | None) -> Decimal | None:
        value = llm_section.get(name, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(
            value, (Decimal, int, float, str)
        ):
            raise ConfigurationError()
        try:
            result = Decimal(str(value))
        except InvalidOperation as error:
            raise ConfigurationError() from error
        if not result.is_finite() or result <= 0:
            raise ConfigurationError()
        return result

    allowed_llm_keys = {
        "adapter",
        "model",
        "codex_home_env",
        "transport_attempt_cap",
        "backoff_lower_seconds",
        "backoff_upper_seconds",
        "invocation_deadline_seconds",
        "max_input_bytes",
        "input_token_budget",
        "output_token_budget",
        "per_run_call_ceiling",
        "per_invocation_cost_ceiling",
        "per_run_cost_ceiling",
    }
    if not set(llm_section).issubset(allowed_llm_keys):
        raise UnknownLLMConfigKeyError()

    adapter = llm_section.get("adapter", DEFAULT_LLM_CONFIG.adapter)
    model = llm_section.get("model", DEFAULT_LLM_CONFIG.model)
    codex_home_env = llm_section.get(
        "codex_home_env", DEFAULT_LLM_CONFIG.codex_home_env
    )
    if not isinstance(codex_home_env, str):
        raise ConfigurationError()
    selection = resolve_selection(adapter, model)
    try:
        validate_structural(selection.model)
    except ValueError as error:
        raise ConfigurationError() from error
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", codex_home_env) is None:
        raise ConfigurationError()

    lower = number(
        "backoff_lower_seconds",
        DEFAULT_LLM_CONFIG.backoff_lower_seconds,
        allow_zero=True,
    )
    upper = number(
        "backoff_upper_seconds",
        DEFAULT_LLM_CONFIG.backoff_upper_seconds,
        allow_zero=True,
    )
    if upper < lower:
        raise ConfigurationError()
    llm = LLMConfig(
        adapter=selection.adapter,
        model=selection.model,
        codex_home_env=codex_home_env,
        transport_attempt_cap=integer(
            "transport_attempt_cap", DEFAULT_LLM_CONFIG.transport_attempt_cap
        ),
        backoff_lower_seconds=lower,
        backoff_upper_seconds=upper,
        invocation_deadline_seconds=number(
            "invocation_deadline_seconds",
            DEFAULT_LLM_CONFIG.invocation_deadline_seconds,
            allow_zero=False,
        ),
        max_input_bytes=integer(
            "max_input_bytes", DEFAULT_LLM_CONFIG.max_input_bytes
        ),
        input_token_budget=integer(
            "input_token_budget", DEFAULT_LLM_CONFIG.input_token_budget
        ),
        output_token_budget=integer(
            "output_token_budget", DEFAULT_LLM_CONFIG.output_token_budget
        ),
        per_run_call_ceiling=integer(
            "per_run_call_ceiling", DEFAULT_LLM_CONFIG.per_run_call_ceiling
        ),
        per_invocation_cost_ceiling=optional_decimal(
            "per_invocation_cost_ceiling",
            DEFAULT_LLM_CONFIG.per_invocation_cost_ceiling,
        ),
        per_run_cost_ceiling=optional_decimal(
            "per_run_cost_ceiling", DEFAULT_LLM_CONFIG.per_run_cost_ceiling
        ),
    )

    return WorkspaceConfig(
        timezone=timezone,
        ignore_paths=tuple(validated),
        llm=llm,
    )


def resolve_codex_home(config: LLMConfig) -> Path:
    """Resolve the configured environment reference without a path fallback."""

    value = os.environ.get(config.codex_home_env)
    if not value:
        raise LLMInvocationError("transport_auth_failed")
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except OSError as error:
        raise LLMInvocationError("transport_auth_failed") from error
    if not path.is_dir():
        raise LLMInvocationError("transport_auth_failed")
    return path


def call_budgets(
    config: LLMConfig,
    *,
    planned_output_tokens: int,
    planned_call_count: int,
    model_context_tokens: int,
    model_max_output_tokens: int,
    input_cost_per_million: Decimal | None = None,
    output_cost_per_million: Decimal | None = None,
) -> CallBudgets:
    """Combine config-owned ceilings with selected-model declarations."""

    return CallBudgets(
        transport_attempt_cap=config.transport_attempt_cap,
        backoff_lower_seconds=config.backoff_lower_seconds,
        backoff_upper_seconds=config.backoff_upper_seconds,
        invocation_deadline_seconds=config.invocation_deadline_seconds,
        max_input_bytes=config.max_input_bytes,
        input_token_budget=config.input_token_budget,
        output_token_budget=config.output_token_budget,
        planned_output_tokens=planned_output_tokens,
        model_context_tokens=model_context_tokens,
        model_max_output_tokens=model_max_output_tokens,
        per_run_call_ceiling=config.per_run_call_ceiling,
        planned_call_count=planned_call_count,
        per_invocation_cost_ceiling=config.per_invocation_cost_ceiling,
        per_run_cost_ceiling=config.per_run_cost_ceiling,
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
    )


def require_timezone(config: WorkspaceConfig) -> str:
    if config.timezone is None:
        error = InvalidInputError()
        error.diagnostic_class = "workspace_timezone_required"
        error.public_message = "Set [workspace].timezone to an IANA name first."
        raise error
    return config.timezone
