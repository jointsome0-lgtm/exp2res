"""Deterministic confidence propagation caps from §9.4."""

from __future__ import annotations

from collections.abc import Iterable

from .enums import Confidence


_CONFIDENCE_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}


def signal_confidence_cap(
    *,
    supporting_confidences: Iterable[Confidence],
    distinct_source_log_count: int,
    has_counter_facts: bool,
) -> str:
    """Return the total structural cap for one self-signal candidate."""

    confidences = tuple(supporting_confidences)
    cap = max(confidences, key=_CONFIDENCE_ORDER.__getitem__, default="unknown")
    if cap == "high" and not (
        len(confidences) >= 2 and distinct_source_log_count >= 2
    ):
        cap = "medium"
    if has_counter_facts and _CONFIDENCE_ORDER[cap] > _CONFIDENCE_ORDER["medium"]:
        cap = "medium"
    return cap
