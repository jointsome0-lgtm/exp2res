"""Deterministic §17 plain-text OccurredAt rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from exp2res.domain.models import OccurredAt
from exp2res.exports.markdown import render_occurred


pytestmark = pytest.mark.unit


def test_open_occurred_rendering_is_attested_explicit_and_clock_free() -> None:
    """§17/§21.53: open rendering states start, flavor, and attestation only."""

    occurred = OccurredAt(
        start=datetime(
            2026, 4, 1, 0, 0, tzinfo=timezone(timedelta(hours=2))
        ),
        end=None,
        precision="approximate_range",
        confidence="medium",
    )
    attested = datetime(
        2026, 7, 15, 14, 30, tzinfo=timezone(timedelta(hours=2))
    )

    first = render_occurred(occurred, attested_as_of=attested)
    second = render_occurred(occurred, attested_as_of=attested)
    assert first == (
        "Approximate open period: 2026-04-01T00:00:00+02:00; "
        "no recorded end as of 2026-07-15T14:30:00+02:00"
    )
    assert second.encode("utf-8") == first.encode("utf-8")
    for forbidden in (
        "to the present",
        "currently",
        "still",
        "ongoing today",
        "2026-07-26",
    ):
        assert forbidden not in first.lower()


def test_open_occurred_rendering_requires_attestation() -> None:
    occurred = OccurredAt(
        start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        end=None,
        precision="date_range",
        confidence="high",
    )
    with pytest.raises(ValueError, match="as-of attestation"):
        render_occurred(occurred)


@pytest.mark.parametrize(
    ("occurred", "expected"),
    [
        (
            OccurredAt(
                start=None,
                end=None,
                precision="unknown",
                confidence="unknown",
            ),
            "Unknown occurrence time",
        ),
        (
            OccurredAt(
                start=datetime(2026, 4, 1, tzinfo=timezone.utc),
                end=None,
                precision="month",
                confidence="medium",
            ),
            "Month (representational anchor): 2026-04-01T00:00:00+00:00",
        ),
        (
            OccurredAt(
                start=datetime(2026, 4, 1, tzinfo=timezone.utc),
                end=datetime(2026, 5, 1, tzinfo=timezone.utc),
                precision="date_range",
                confidence="medium",
            ),
            "Date range: 2026-04-01T00:00:00+00:00 / "
            "2026-05-01T00:00:00+00:00",
        ),
    ],
)
def test_other_occurred_shapes_render_precision_safely(
    occurred: OccurredAt, expected: str
) -> None:
    assert render_occurred(occurred) == expected
