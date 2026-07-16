"""Closed §15.13 adapter identifiers and this build's runtime registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Pattern

from exp2res.errors import (
    LLMAdapterNotRegisteredError,
    LLMModelInvalidError,
    UnknownLLMAdapterError,
)

from .preflight import CODEX_TOKEN_PATTERNS
from .runner import CodexCLIRunner


ADAPTER_IDENTIFIERS = frozenset(
    {"codex-cli", "claude-agent-sdk", "openai-compat"}
)
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


@dataclass(frozen=True)
class LLMSelection:
    adapter: str
    model: str


@dataclass(frozen=True)
class CLITestRange:
    minimum: tuple[int, int, int]
    maximum_exclusive: tuple[int, int, int]
    supported_flags: frozenset[str]


@dataclass(frozen=True)
class CodexCapabilityDeclaration:
    """The local, provider-free §15.10 rule 4 declaration for Codex CLI."""

    required_flags: frozenset[str]
    tested_ranges: tuple[CLITestRange, ...]
    token_patterns: tuple[Pattern[bytes], ...]
    credential_form: str = "externally-managed-session"
    structured_outputs_supported: bool = True
    timeout_supported: bool = True
    cancellation_supported: bool = True
    runner_protocol_version: int = 1


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
)


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_id: str
    runner_type: type[CodexCLIRunner]
    declaration: CodexCapabilityDeclaration


# This build intentionally ships one runtime. The other two identifiers remain
# stable product configuration values, but selection fails closed until their
# implementations and declarations land.
ADAPTER_REGISTRY = {
    "codex-cli": AdapterRegistration(
        adapter_id="codex-cli",
        runner_type=CodexCLIRunner,
        declaration=DEFAULT_DECLARATION,
    )
}


def resolve_selection(adapter: object, model: object) -> LLMSelection:
    if not isinstance(adapter, str) or adapter not in ADAPTER_IDENTIFIERS:
        raise UnknownLLMAdapterError()
    if adapter not in ADAPTER_REGISTRY:
        raise LLMAdapterNotRegisteredError()
    if (
        not isinstance(model, str)
        or not model
        or model.strip() != model
        or any(character.isspace() for character in model)
    ):
        raise LLMModelInvalidError()
    return LLMSelection(adapter=adapter, model=model)


def registration_for(selection: LLMSelection) -> AdapterRegistration:
    """Revalidate injected identity at the invocation capability seam."""

    resolved = resolve_selection(selection.adapter, selection.model)
    return ADAPTER_REGISTRY[resolved.adapter]
