"""Shared §16.11 verification-status aggregation."""

from __future__ import annotations

from typing import Iterable, cast

from exp2res.domain.enums import VerificationStatus
from exp2res.errors import IntegrityFailureError


AGGREGATE_PRECEDENCE = (
    "rejected",
    "unsupported",
    "contradicted",
    "needs_clarification",
    "partially_supported",
    "inferred_but_acceptable",
    "supported",
)


def aggregate_verification_status(
    statuses: Iterable[VerificationStatus],
) -> VerificationStatus:
    values = set(statuses)
    if not values:
        raise IntegrityFailureError("snapshot_claim_set_empty")
    if "unverified" in values:
        return "unverified"
    for status in AGGREGATE_PRECEDENCE:
        if status in values:
            return cast(VerificationStatus, status)
    raise IntegrityFailureError("snapshot_status_invalid")
