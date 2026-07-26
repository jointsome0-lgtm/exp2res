"""Focused §11.1/§16.7 open-ended temporal semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
import pytest

from exp2res.domain.models import OccurredAt
from exp2res.domain.temporal import (
    UncertaintyInterval,
    governing_contains,
    interval_contains,
    occurred_interval,
    placement_supports,
    uncertainty_width,
)


pytestmark = [pytest.mark.unit, pytest.mark.invariant]
UTC = timezone.utc


def occurred(
    *,
    start: datetime | None,
    end: datetime | None,
    precision: str = "date_range",
) -> OccurredAt:
    return OccurredAt(
        start=start,
        end=end,
        precision=precision,
        confidence="medium",
    )


def test_open_range_shape_and_width_stay_distinct_from_unknown_and_closed() -> None:
    """§11.1/§16.7: known-start openness is unbounded but not unknown."""

    start = datetime(2026, 4, 1, tzinfo=UTC)
    open_range = occurred(start=start, end=None)
    unknown = occurred(start=None, end=None, precision="unknown")
    closed = occurred(start=start, end=start + timedelta(days=30))

    assert occurred_interval(unknown) == UncertaintyInterval(None, None)
    assert occurred_interval(open_range) == UncertaintyInterval(start, None)
    assert occurred_interval(closed) == UncertaintyInterval(
        start, start + timedelta(days=30)
    )
    assert uncertainty_width(unknown) is None
    assert uncertainty_width(open_range) is None
    assert uncertainty_width(closed) == timedelta(days=30)
    with pytest.raises(ValidationError):
        occurred(start=start, end=start)


def test_interval_contains_handles_open_closed_unknown_singleton_and_empty() -> None:
    """§16.7: all interval states have explicit subset behavior."""

    start = datetime(2026, 4, 1, tzinfo=UTC)
    later = start + timedelta(days=10)
    open_outer = UncertaintyInterval(start, None)
    bounded_inner = UncertaintyInterval(later, later + timedelta(days=1))
    open_inner = UncertaintyInterval(later, None)
    closed_outer = UncertaintyInterval(start, start + timedelta(days=30))
    singleton = UncertaintyInterval(later, later)
    empty = UncertaintyInterval(start, start, empty=True)

    assert interval_contains(open_outer, bounded_inner)
    assert interval_contains(open_outer, open_inner)
    assert interval_contains(closed_outer, singleton)
    assert not interval_contains(closed_outer, open_inner)
    assert not interval_contains(empty, bounded_inner)
    assert not interval_contains(empty, UncertaintyInterval(start, start))
    assert interval_contains(UncertaintyInterval(None, None), open_inner)


def test_governing_contains_clips_only_bounded_candidates_to_attested_window() -> None:
    """§16.7: open governance clips evidence, while open copies stay legal."""

    start = datetime(2026, 4, 1, tzinfo=UTC)
    recorded_at = datetime(2026, 7, 15, 14, 30, tzinfo=timezone(timedelta(hours=2)))
    governing = occurred(start=start, end=None, precision="approximate_range")
    inside = occurred(
        start=datetime(2026, 7, 15, 11, 0, tzinfo=UTC),
        end=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )
    at_attestation = occurred(
        start=datetime(2026, 7, 15, 12, 30, tzinfo=UTC),
        end=datetime(2026, 7, 15, 12, 45, tzinfo=UTC),
    )
    shifted_open = occurred(
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=None,
    )

    assert governing_contains(governing, recorded_at, inside)
    assert not governing_contains(governing, recorded_at, at_attestation)
    assert governing_contains(governing, recorded_at, governing)
    assert governing_contains(governing, recorded_at, shifted_open)


def test_empty_attested_window_rejects_closed_narrowing_but_allows_open_copy() -> None:
    """§16.7: a future-start record attests no bounded occurrence."""

    start = datetime(2026, 8, 1, tzinfo=UTC)
    governing = occurred(start=start, end=None)
    bounded = occurred(
        start=start,
        end=start + timedelta(days=1),
    )

    assert not governing_contains(
        governing, start - timedelta(seconds=1), bounded
    )
    assert not governing_contains(governing, start, bounded)
    assert governing_contains(governing, start, governing)


def test_open_support_never_entails_a_bounded_candidate_and_closed_rejects_open() -> None:
    """§16.7: entailment is unclipped and closed governance cannot widen."""

    start = datetime(2026, 4, 1, tzinfo=UTC)
    open_support = occurred(start=start, end=None)
    bounded = occurred(start=start, end=start + timedelta(days=30))

    assert not placement_supports(bounded, open_support)
    assert not governing_contains(bounded, start + timedelta(days=10), open_support)


def test_open_exactness_is_still_compared_at_the_shared_unbounded_width() -> None:
    """§16.7: an open date_range upgrades an open approximate_range."""

    start = datetime(2026, 4, 1, tzinfo=UTC)
    approximate_open = occurred(
        start=start, end=None, precision="approximate_range"
    )
    exact_open = occurred(start=start, end=None, precision="date_range")

    assert not placement_supports(exact_open, approximate_open)
    assert placement_supports(approximate_open, exact_open)
    assert placement_supports(exact_open, exact_open)
    assert governing_contains(approximate_open, start + timedelta(days=1), exact_open)
