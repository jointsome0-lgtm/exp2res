"""Shared provider capability declaration value types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Pattern


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
    credential_form: str = "externally-managed-session"
    structured_outputs_supported: bool = True
    timeout_supported: bool = True
    cancellation_supported: bool = True
    runner_protocol_version: int = 1
