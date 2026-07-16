"""§16.7 anchored uncertainty intervals and precision-strength comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import OccurredAt

# §16.7 maximum-uncertainty widths for non-range precisions; ranges use
# their own `end - start`, and `unknown` is unbounded (None).
_MAX_UNCERTAINTY_WIDTH: dict[str, timedelta] = {
    "exact_datetime": timedelta(0),
    "exact_day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=31),
    "quarter": timedelta(days=92),
    "year": timedelta(days=366),
}

_CONFIDENCE_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class UncertaintyInterval:
    """Half-open UTC interval [start, end); equal bounds are the singleton.

    ``start is None`` and ``end is None`` together represent the unbounded
    `unknown` timeline; §11.1 admits no other unbounded shape.
    """

    start: datetime | None
    end: datetime | None

    @property
    def unbounded(self) -> bool:
        return self.start is None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("uncertainty intervals require offset-aware bounds")
    return value.astimezone(timezone.utc)


def uncertainty_width(occurred: OccurredAt) -> timedelta | None:
    """Return §16.7's comparison width; None means unbounded (`unknown`)."""

    if occurred.precision == "unknown":
        return None
    if occurred.precision in ("date_range", "approximate_range"):
        assert occurred.start is not None and occurred.end is not None
        return _utc(occurred.end) - _utc(occurred.start)
    return _MAX_UNCERTAINTY_WIDTH[occurred.precision]


def occurred_interval(occurred: OccurredAt) -> UncertaintyInterval:
    """Normalize an OccurredAt to its §16.7 anchored uncertainty interval."""

    if occurred.precision == "unknown":
        return UncertaintyInterval(None, None)
    assert occurred.start is not None
    start = _utc(occurred.start)
    if occurred.precision in ("date_range", "approximate_range"):
        assert occurred.end is not None
        return UncertaintyInterval(start, _utc(occurred.end))
    return UncertaintyInterval(start, start + _MAX_UNCERTAINTY_WIDTH[occurred.precision])


def interval_contains(outer: UncertaintyInterval, inner: UncertaintyInterval) -> bool:
    """Subset test under §16.7's half-open/singleton semantics."""

    if outer.unbounded:
        return True
    if inner.unbounded:
        return False
    assert outer.start is not None and outer.end is not None
    assert inner.start is not None and inner.end is not None
    if inner.start == inner.end:
        if outer.start == outer.end:
            return inner.start == outer.start
        return outer.start <= inner.start < outer.end
    if outer.start == outer.end:
        return False
    return outer.start <= inner.start and inner.end <= outer.end


def _is_exact_bounded(occurred: OccurredAt) -> bool:
    """§16.7: at equal width, `approximate_range` is the weaker form."""

    return occurred.precision != "approximate_range"


def strength_exceeds_support(
    candidate: OccurredAt, supports: tuple[OccurredAt, ...]
) -> bool:
    """True when the candidate is temporally stronger than every support.

    A candidate upgrades precision when its normalized width is narrower
    than the strongest supported width, or when it strengthens exactness at
    equal width (§16.7). An empty support set makes any bounded candidate
    an upgrade.
    """

    candidate_width = uncertainty_width(candidate)
    if candidate_width is None:
        return False
    strongest: timedelta | None = None
    exact_at_strongest = False
    for support in supports:
        width = uncertainty_width(support)
        if width is None:
            continue
        if strongest is None or width < strongest:
            strongest = width
            exact_at_strongest = _is_exact_bounded(support)
        elif width == strongest:
            exact_at_strongest = exact_at_strongest or _is_exact_bounded(support)
    if strongest is None:
        return True
    if candidate_width < strongest:
        return True
    return (
        candidate_width == strongest
        and _is_exact_bounded(candidate)
        and not exact_at_strongest
    )


def placement_supports(candidate: OccurredAt, support: OccurredAt) -> bool:
    """§13.3 rule 2: does this selected placement entail the candidate?

    A record asserting occurrence within `support` explicitly supports the
    candidate placement exactly when its anchored interval lies inside the
    candidate's and it is at least as strong under §16.7 — so a placement
    can only restate or weaken what some selected record asserts, never
    sharpen it (a July 5 exact day never licenses July 10).
    """

    return interval_contains(
        occurred_interval(candidate), occurred_interval(support)
    ) and not strength_exceeds_support(candidate, (support,))


def confidence_exceeds(candidate: str, ceiling: str) -> bool:
    """Compare two §10 weak-to-strong ordered confidence values."""

    return _CONFIDENCE_ORDER[candidate] > _CONFIDENCE_ORDER[ceiling]
