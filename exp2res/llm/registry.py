"""Closed §15.13 adapter identifiers and this build's runtime registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from exp2res.errors import (
    LLMAdapterNotRegisteredError,
    LLMModelInvalidError,
    UnknownLLMAdapterError,
)

from .capabilities import CLICapabilityDeclaration, CapabilityTestRange
from .codex import (
    DEFAULT_DECLARATION,
    build_runner as build_codex_runner,
    classify_codex_failure,
)
from .runner import ContractRunner, RawResult

if TYPE_CHECKING:
    from exp2res.config import LLMConfig


ADAPTER_IDENTIFIERS = frozenset(
    {"codex-cli", "claude-agent-sdk", "openai-compat"}
)


@dataclass(frozen=True)
class LLMSelection:
    adapter: str
    model: str


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_id: str
    declaration: CLICapabilityDeclaration
    build_runner: Callable[["LLMConfig", Path], ContractRunner]
    classify_failure: Callable[[RawResult], tuple[str | None, bool]]


# This build intentionally ships one runtime. The other two identifiers remain
# stable product configuration values, but selection fails closed until their
# implementations and declarations land.
ADAPTER_REGISTRY: dict[str, AdapterRegistration] = {
    "codex-cli": AdapterRegistration(
        adapter_id="codex-cli",
        declaration=DEFAULT_DECLARATION,
        build_runner=build_codex_runner,
        classify_failure=classify_codex_failure,
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
