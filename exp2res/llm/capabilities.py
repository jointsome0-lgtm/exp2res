"""Shared provider capability declaration value types."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Pattern

from exp2res.errors import LLMInvocationError


@dataclass(frozen=True)
class CapabilityTestRange:
    minimum: tuple[int, int, int]
    maximum_exclusive: tuple[int, int, int]
    supported_flags: frozenset[str]


@dataclass(frozen=True)
class CLICapabilityDeclaration:
    """A local, provider-free §15.10 rule 4 runtime declaration."""

    required_flags: frozenset[str]
    tested_ranges: tuple[CapabilityTestRange, ...]
    token_patterns: tuple[Pattern[bytes], ...]
    reasoning_efforts: frozenset[str] = frozenset()
    credential_form: str = "externally-managed-session"
    structured_outputs_supported: bool = True
    timeout_supported: bool = True
    cancellation_supported: bool = True
    runner_protocol_version: int = 1


def parse_cli_version(value: str) -> tuple[int, int, int]:
    """Parse the first standalone X.Y.Z runtime version."""

    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", value)
    if match is None:
        raise LLMInvocationError("capability_mismatch")
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def validate_reasoning_effort(
    effort: object, declaration: CLICapabilityDeclaration
) -> str:
    """Fail closed when selected config is outside an adapter declaration."""

    if not isinstance(effort, str) or effort not in declaration.reasoning_efforts:
        raise LLMInvocationError("capability_mismatch")
    return effort
