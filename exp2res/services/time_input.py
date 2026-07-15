"""Workspace-timezone-only owner input resolution."""

from __future__ import annotations

from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from exp2res.domain.enums import TemporalConfidence, TemporalPrecision
from exp2res.domain.models import OccurredAt
from exp2res.errors import InvalidInputError


def _time_error(code: str, message: str) -> InvalidInputError:
    error = InvalidInputError()
    error.diagnostic_class = code
    error.public_message = message
    return error


def workspace_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise _time_error(
            "workspace_timezone_invalid", "The configured IANA timezone is invalid."
        ) from error


def resolve_local(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if (
            round_trip.replace(tzinfo=None) == value
            and candidate.utcoffset() == round_trip.utcoffset()
        ):
            candidates.append(candidate)
    offsets = {candidate.utcoffset() for candidate in candidates}
    if not candidates or len(offsets) != 1:
        raise _time_error(
            "local_time_unresolved",
            "The local time is ambiguous or nonexistent; supply an explicit offset.",
        )
    return candidates[0]


def _parse_datetime(value: str, zone: ZoneInfo) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _time_error("invalid_time", "The time value is invalid.") from error
    return resolve_local(parsed, zone)


def day_start(day: date, zone: ZoneInfo) -> datetime:
    return resolve_local(datetime.combine(day, datetime.min.time()), zone)


def today_occurred(*, now: datetime, timezone_name: str) -> OccurredAt:
    if now.tzinfo is None or now.utcoffset() is None:
        raise _time_error("invalid_time", "The service clock must carry an offset.")
    zone = workspace_zone(timezone_name)
    return OccurredAt(
        start=day_start(now.astimezone(zone).date(), zone),
        end=None,
        precision="exact_day",
        confidence="high",
    )


def _named_anchor(value: str, precision: TemporalPrecision, zone: ZoneInfo) -> datetime:
    if precision == "year" and re.fullmatch(r"\d{4}", value):
        return day_start(date(int(value), 1, 1), zone)
    if precision == "month" and re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = (int(part) for part in value.split("-"))
        return day_start(date(year, month, 1), zone)
    if precision == "quarter":
        match = re.fullmatch(r"(?:Q([1-4])\s+(\d{4})|(\d{4})-Q([1-4]))", value)
        if match:
            quarter = int(match.group(1) or match.group(4))
            year = int(match.group(2) or match.group(3))
            return day_start(date(year, (quarter - 1) * 3 + 1, 1), zone)
    if precision == "week":
        match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
        if match:
            return day_start(
                date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1), zone
            )
    return _parse_datetime(value, zone)


def _build_occurred(**kwargs: object) -> OccurredAt:
    try:
        return OccurredAt(**kwargs)  # type: ignore[arg-type]
    except ValidationError as error:
        # Owner-typed shapes (a reversed range, an unknown precision or
        # confidence literal) are §14.14 exit-class-2 input, not exit 1.
        raise _time_error(
            "invalid_time_shape", "The temporal shape is invalid."
        ) from error


def parse_occurred(
    *,
    period: str,
    precision: TemporalPrecision,
    confidence: TemporalConfidence,
    timezone_name: str,
) -> OccurredAt:
    zone = workspace_zone(timezone_name)
    if precision == "unknown":
        return _build_occurred(
            start=None, end=None, precision=precision, confidence=confidence
        )
    if precision in {"date_range", "approximate_range"}:
        parts = period.split("/", 1)
        if len(parts) != 2:
            raise _time_error("invalid_time_shape", "A range requires start/end values.")
        return _build_occurred(
            start=_parse_datetime(parts[0], zone),
            end=_parse_datetime(parts[1], zone),
            precision=precision,
            confidence=confidence,
        )
    return _build_occurred(
        start=_named_anchor(period, precision, zone),
        end=None,
        precision=precision,
        confidence=confidence,
    )
