"""Deterministic §15.10 budget/context and §29.4 secret preflight."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import re
from typing import Iterable, Pattern

from exp2res.errors import LLMInvocationError

from .runner import PreparedCall


CREDENTIAL_FIELDS = frozenset(
    {
        "api_key",
        "access_token",
        "refresh_token",
        "secret",
        "password",
        "authorization",
    }
)
PEM_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
CODEX_TOKEN_PATTERNS: tuple[Pattern[bytes], ...] = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


@dataclass(frozen=True)
class PreflightMetrics:
    input_bytes: int
    estimated_input_tokens: int
    planned_output_tokens: int
    conservative_invocation_cost: Decimal | None
    conservative_run_cost: Decimal | None


def estimate_tokens(value: bytes) -> int:
    """Conservatively estimate one token per three UTF-8 bytes, rounded up."""

    return 0 if not value else (len(value) + 2) // 3


def normalize_credential_field(name: str) -> str:
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9]+", "_", with_boundaries.casefold()).strip("_")


def _credential_field_present(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                normalize_credential_field(str(key)) in CREDENTIAL_FIELDS
                and child not in (None, "", [], {})
            ):
                return True
            if _credential_field_present(child):
                return True
    elif isinstance(value, list):
        return any(_credential_field_present(item) for item in value)
    return False


def contains_credential_material(
    value: bytes,
    *,
    resolved_credentials: Iterable[bytes] = (),
    token_patterns: Iterable[Pattern[bytes]] = (),
) -> bool:
    """Detect required secret classes without returning or echoing a match."""

    upper = value.upper()
    if any(marker in upper for marker in PEM_MARKERS):
        return True
    for credential in resolved_credentials:
        if credential and credential in value:
            return True
    if any(pattern.search(value) is not None for pattern in token_patterns):
        return True
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, UnicodeError):
        return False
    return _credential_field_present(decoded)


def looks_like_literal_credential(
    value: str, *, token_patterns: Iterable[Pattern[bytes]] = ()
) -> bool:
    encoded = value.encode("utf-8")
    return any(marker in encoded.upper() for marker in PEM_MARKERS) or any(
        pattern.search(encoded) is not None for pattern in token_patterns
    )


def _validate_budget_shape(call: PreparedCall) -> None:
    budgets = call.budgets
    positive_integers = (
        budgets.transport_attempt_cap,
        budgets.max_input_bytes,
        budgets.input_token_budget,
        budgets.output_token_budget,
        budgets.planned_output_tokens,
        budgets.model_context_tokens,
        budgets.model_max_output_tokens,
        budgets.per_run_call_ceiling,
        budgets.planned_call_count,
    )
    if any(isinstance(value, bool) or value <= 0 for value in positive_integers):
        raise LLMInvocationError("capability_mismatch")
    if (
        budgets.invocation_deadline_seconds <= 0
        or budgets.backoff_lower_seconds < 0
        or budgets.backoff_upper_seconds < budgets.backoff_lower_seconds
    ):
        raise LLMInvocationError("capability_mismatch")


def preflight_call(
    call: PreparedCall,
    *,
    resolved_credentials: Iterable[bytes] = (),
    token_patterns: Iterable[Pattern[bytes]] = (),
) -> PreflightMetrics:
    """Fail locally before transport on secrets, budgets, or model overflow."""

    _validate_budget_shape(call)
    budgets = call.budgets
    byte_count = len(call.serialized_input)
    if byte_count > budgets.max_input_bytes:
        raise LLMInvocationError("budget_exceeded")
    tokens = estimate_tokens(call.serialized_input)
    if tokens > budgets.input_token_budget:
        raise LLMInvocationError("budget_exceeded")
    if budgets.planned_output_tokens > budgets.output_token_budget:
        raise LLMInvocationError("budget_exceeded")
    if budgets.planned_output_tokens > budgets.model_max_output_tokens:
        raise LLMInvocationError("context_overflow")
    if tokens + budgets.planned_output_tokens > budgets.model_context_tokens:
        raise LLMInvocationError("context_overflow")
    if budgets.planned_call_count > budgets.per_run_call_ceiling:
        raise LLMInvocationError("budget_exceeded")
    if contains_credential_material(
        call.serialized_input,
        resolved_credentials=resolved_credentials,
        token_patterns=token_patterns,
    ):
        raise LLMInvocationError("credential_detected")

    invocation_cost: Decimal | None = None
    run_cost: Decimal | None = None
    pricing = (budgets.input_cost_per_million, budgets.output_cost_per_million)
    if any(value is not None for value in pricing):
        if any(value is None or value < 0 for value in pricing):
            raise LLMInvocationError("capability_mismatch")
        attempt_multiplier = Decimal(budgets.transport_attempt_cap * 2)
        invocation_cost = attempt_multiplier * (
            Decimal(tokens) * pricing[0]  # type: ignore[operator]
            + Decimal(budgets.planned_output_tokens) * pricing[1]  # type: ignore[operator]
        ) / Decimal(1_000_000)
        run_cost = invocation_cost * Decimal(budgets.planned_call_count)
    if budgets.per_invocation_cost_ceiling is not None:
        if invocation_cost is None:
            raise LLMInvocationError("capability_mismatch")
        if invocation_cost > budgets.per_invocation_cost_ceiling:
            raise LLMInvocationError("budget_exceeded")
    if budgets.per_run_cost_ceiling is not None:
        if run_cost is None:
            raise LLMInvocationError("capability_mismatch")
        if run_cost > budgets.per_run_cost_ceiling:
            raise LLMInvocationError("budget_exceeded")
    return PreflightMetrics(
        input_bytes=byte_count,
        estimated_input_tokens=tokens,
        planned_output_tokens=budgets.planned_output_tokens,
        conservative_invocation_cost=invocation_cost,
        conservative_run_cost=run_cost,
    )
