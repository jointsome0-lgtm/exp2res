"""Deterministic confidence propagation caps from §9.4."""

from __future__ import annotations

from collections.abc import Iterable

from .enums import CONFIDENCE_RANK, Confidence


def pattern_generalization_cap(
    *,
    supporting_confidences: Iterable[Confidence],
    distinct_source_log_count: int,
    has_counter_facts: bool,
) -> str:
    """Return §9.4's pattern-generalization cap for one pattern-citing claim."""

    confidences = tuple(supporting_confidences)
    cap = max(confidences, key=CONFIDENCE_RANK.__getitem__, default="unknown")
    if cap == "high" and not (
        len(confidences) >= 2 and distinct_source_log_count >= 2
    ):
        cap = "medium"
    if has_counter_facts and CONFIDENCE_RANK[cap] > CONFIDENCE_RANK["medium"]:
        cap = "medium"
    return cap


def claim_confidence_cap(*, source_confidences: Iterable[Confidence]) -> str:
    """Return §9.4's source-maximum cap for one self-claim candidate."""

    return max(
        tuple(source_confidences),
        key=CONFIDENCE_RANK.__getitem__,
        default="unknown",
    )
