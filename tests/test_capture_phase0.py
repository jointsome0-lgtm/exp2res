"""Stage 1 capture and strict-boundary acceptance tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID

import pytest
from pydantic import ValidationError

import exp2res.services.capture as capture_service

from exp2res.domain.models import RAW_TEXT_LIMIT, OccurredAt
from exp2res.errors import ForbiddenPathError, IdCollisionError, InvalidInputError
from exp2res.services.capture import (
    capture_daily,
    capture_daily_file,
    capture_retro,
    new_id,
)
from exp2res.services.logs import list_logs, show_log
from exp2res.services.time_input import parse_occurred, today_occurred

from conftest import FIXED_NOW, VERA_CORPUS


pytestmark = [pytest.mark.unit, pytest.mark.lifecycle]


def _retro_occurred(payload: dict[str, object]) -> OccurredAt:
    period = payload["answers"]["period"]  # type: ignore[index]
    return OccurredAt.model_validate_json(json.dumps(period))


def test_vera_daily_and_retro_round_trip_with_atomic_manual_evidence(
    workspace: Path,
) -> None:
    """§21.15 / §21.39; §24.1 / §24.18 / §24.42: Stage 1 round-trip."""
    daily_path = VERA_CORPUS / "logs" / "daily-2026-06-02.md"
    daily = capture_daily_file(
        workspace,
        source_path=str(daily_path),
        project="K8s Playbook",
        clock=lambda: FIXED_NOW,
    )
    retro_payload = json.loads(
        (VERA_CORPUS / "logs" / "retro-2026-06-k8s.json").read_text(
            encoding="utf-8"
        )
    )
    retro = capture_retro(
        workspace,
        occurred=_retro_occurred(retro_payload),
        raw_text=retro_payload["answers"]["text"],
        project=retro_payload["answers"]["project"],
        clock=lambda: FIXED_NOW.replace(hour=13),
    )

    daily_bundle = show_log(workspace, log_id=daily.raw_log.id)
    retro_bundle = show_log(workspace, log_id=retro.raw_log.id)
    assert daily_bundle.raw_log.raw_text == daily_path.read_text(encoding="utf-8")
    assert daily_bundle.raw_log.entry_type == "manual_daily"
    assert daily_bundle.raw_log.source_type == "manual_entry"
    assert daily_bundle.raw_log.occurred.precision == "exact_day"
    assert retro_bundle.raw_log.raw_text == retro_payload["answers"]["text"]
    assert retro_bundle.raw_log.entry_type == "manual_retro"
    assert retro_bundle.raw_log.source_type == "user_memory"
    assert retro_bundle.raw_log.occurred.precision == "approximate_range"
    for bundle in (daily_bundle, retro_bundle):
        assert len(bundle.evidence_items) == 1
        assert bundle.evidence_items[0].strength == "manual_claim"
        assert bundle.evidence_items[0].raw_log_id == bundle.raw_log.id

    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        stored_retro = connection.execute(
            "SELECT occurred_start, occurred_end FROM raw_logs WHERE id = ?",
            (retro.raw_log.id,),
        ).fetchone()
    assert tables == {
        "schema_meta",
        "raw_logs",
        "evidence_items",
        "processing_runs",
        "llm_calls",
        "experience_facts",
        "fact_sources",
        "gap_questions",
        "contradictions",
        "assessment_snapshots",
        "self_claims",
        "verification_findings",
        "job_descriptions",
        "resume_branches",
        "resume_bullets",
    }
    assert stored_retro == (
        "2026-06-01T00:00:00+02:00",
        "2026-07-01T00:00:00+02:00",
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "start": datetime(2026, 6, 1),
            "end": None,
            "precision": "exact_day",
            "confidence": "high",
        },
        {
            "start": datetime(2026, 6, 2, tzinfo=timezone.utc),
            "end": datetime(2026, 6, 1, tzinfo=timezone.utc),
            "precision": "approximate_range",
            "confidence": "low",
        },
        {
            "start": datetime(2026, 6, 1, tzinfo=timezone.utc),
            "end": None,
            "precision": "unknown",
            "confidence": "unknown",
        },
    ],
    ids=["naive", "reversed-range", "unknown-with-bound"],
)
def test_invalid_temporal_shapes_fail_before_persistence(
    workspace: Path, arguments: dict[str, object]
) -> None:
    """§21.7 / §21.39; §24.42 / §24.45: invalid time shapes fail closed."""
    with pytest.raises(ValidationError):
        OccurredAt(**arguments)
    assert list_logs(workspace) == ()


def test_oversize_and_forbidden_source_files_leave_no_record(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.39 / §21.42; §24.42 / §24.45: acquisition limits and deny paths."""
    oversize = tmp_path / "Vera Example oversize.md"
    oversize.write_bytes(b"Vera Example\n" + b"x" * RAW_TEXT_LIMIT)
    with pytest.raises(InvalidInputError) as too_large:
        capture_daily_file(
            workspace,
            source_path=str(oversize),
            clock=lambda: FIXED_NOW,
        )
    assert too_large.value.diagnostic_class == "input_too_large"

    forbidden = tmp_path / ".env"
    forbidden.write_text("Vera Example synthetic secret stand-in\n", encoding="utf-8")
    with pytest.raises(ForbiddenPathError):
        capture_daily_file(
            workspace,
            source_path=str(forbidden),
            clock=lambda: FIXED_NOW,
        )
    with pytest.raises(ForbiddenPathError):
        capture_daily_file(
            workspace,
            source_path=r"C:\Vera Example\daily.md",
            clock=lambda: FIXED_NOW,
        )
    assert list_logs(workspace) == ()


def test_injected_failure_between_raw_and_evidence_rolls_back_both(
    workspace: Path,
) -> None:
    """§21.15 / §21.37; §24.1 / §24.40: the Stage 1 pair is one transaction."""
    def crash() -> None:
        raise RuntimeError("synthetic injected crash")

    with pytest.raises(RuntimeError):
        capture_daily(
            workspace,
            raw_text="Vera Example atomic failure sentinel",
            clock=lambda: FIXED_NOW,
            after_raw_insert=crash,
        )
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 0


def test_retained_id_collision_retries_then_fails_without_duplicate(
    workspace: Path,
) -> None:
    """§21.35; §24.38: retained primary-key collision never duplicates a pair."""
    values = iter(["log_same", "evi_same"])
    first = capture_daily(
        workspace,
        raw_text="Vera Example first ID owner",
        clock=lambda: FIXED_NOW,
        id_factory=lambda _kind: next(values),
    )
    assert first.raw_log.id == "log_same"

    def colliding(kind: str) -> str:
        return "log_same" if kind == "raw_log" else "evi_same"

    with pytest.raises(IdCollisionError):
        capture_daily(
            workspace,
            raw_text="Vera Example colliding candidate",
            clock=lambda: FIXED_NOW,
            id_factory=colliding,
        )
    assert [item.id for item in list_logs(workspace)] == ["log_same"]


def test_workspace_timezone_rejects_dst_gap_and_fold_but_accepts_offset() -> None:
    """§21.42; §24.45: local gaps/folds fail while explicit offsets survive."""
    for local_value in ("2026-03-29T02:30:00", "2026-10-25T02:30:00"):
        with pytest.raises(InvalidInputError) as failure:
            parse_occurred(
                period=local_value,
                precision="exact_datetime",
                confidence="high",
                timezone_name="Europe/Berlin",
            )
        assert failure.value.diagnostic_class == "local_time_unresolved"

    explicit = parse_occurred(
        period="2026-10-25T02:30:00+02:00",
        precision="exact_datetime",
        confidence="high",
        timezone_name="Europe/Berlin",
    )
    assert explicit.start.isoformat() == "2026-10-25T02:30:00+02:00"


def test_reversed_retro_range_is_invalid_input_not_internal_error() -> None:
    """PR #95 review: owner-typed reversed ranges stay in §14.14 exit class 2."""
    from exp2res.errors import InvalidInputError
    from exp2res.services.time_input import parse_occurred

    with pytest.raises(InvalidInputError) as caught:
        parse_occurred(
            period="2026-06-10/2026-06-01",
            precision="date_range",
            confidence="medium",
            timezone_name="Europe/Belgrade",
        )
    assert caught.value.diagnostic_class == "invalid_time_shape"
    assert caught.value.exit_code == 2


def test_out_of_range_calendar_anchor_is_invalid_input_not_internal_error() -> None:
    """PR #95 review r2: month 13 / week 99 stay in §14.14 exit class 2."""
    for period, precision in (("2026-13", "month"), ("2026-W99", "week")):
        with pytest.raises(InvalidInputError) as caught:
            parse_occurred(
                period=period,
                precision=precision,
                confidence="medium",
                timezone_name="Etc/UTC",
            )
        assert caught.value.diagnostic_class == "invalid_time"
        assert caught.value.exit_code == 2


def _placement(**overrides: object) -> str:
    body: dict[str, object] = {
        "start": "2026-06-01T00:00:00+00:00",
        "end": None,
        "precision": "exact_day",
        "confidence": "high",
    }
    body.update(overrides)
    return json.dumps(body)


def test_a_json_datetime_admits_only_an_iso_8601_spelling() -> None:
    """§11 rules 3 and 6: the ISO string is the one string-to-datetime bridge.

    Pydantic's JSON parser reads a numeric string as a Unix timestamp, which
    is a second bridge; an LLM or importer emitting one would otherwise land a
    silently different instant that no later stage can tell apart. The
    spellings it reads that way are not only whole seconds, so the gate is the
    ISO prefix and every non-ISO form fails it.
    """
    timestamps = (
        "1780272000",
        "1780272000.5",
        "-1780272000",
        ".5",
        "+.5",
        "1.",
        "20260601",
    )
    for spelling in timestamps:
        with pytest.raises(ValidationError) as caught:
            OccurredAt.model_validate_json(_placement(start=spelling))
        assert "ISO 8601" in str(caught.value)

    # Pydantic also accepts `_` as a separator, which no standard defines.
    with pytest.raises(ValidationError) as underscore:
        OccurredAt.model_validate_json(_placement(start="2026-06-01_00:00:00+00:00"))
    assert "ISO 8601" in str(underscore.value)

    # Every separator a standard does define survives the gate, so the
    # allowlist narrows the bridge without narrowing rule 3's grant.
    for spelling in (
        "2026-06-01T00:00:00+00:00",
        "2026-06-01t00:00:00+00:00",
        "2026-06-01 00:00:00+00:00",
        "2026-06-01T00:00:00Z",
    ):
        accepted = OccurredAt.model_validate_json(_placement(start=spelling))
        assert accepted.start == datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_a_calendar_edge_placement_is_refused_at_the_boundary() -> None:
    """§11 rule 54: no accepted value defers its overflow to a consumer.

    Each rejected spelling below reaches §16.7 rule 6 normalization somewhere
    downstream, where `OverflowError` — not a `ValueError` — escapes Pydantic.
    The west-shifted start is the one that only rule 3's UTC basis catches: it
    carries its own width in local time and overflows only after the shift.
    """
    unrepresentable = (
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59+00:00",
        "9999-12-30T20:00:00-05:00",
    )
    for spelling in unrepresentable:
        with pytest.raises(ValidationError):
            OccurredAt.model_validate_json(_placement(start=spelling))

    # Neither the same instant under a range precision, which adds no width,
    # nor a start whose offset shifts it away from the edge is over-rejected.
    assert OccurredAt.model_validate_json(
        _placement(start="9999-12-31T00:00:00+00:00", precision="date_range")
    ).end is None
    assert OccurredAt.model_validate_json(
        _placement(start="9999-12-29T00:00:00+14:00")
    ).precision == "exact_day"


def test_a_calendar_edge_local_time_is_owner_input_not_an_internal_error() -> None:
    """§11 rule 54 / §14.14: the same edge typed by the owner stays exit 2."""
    for period, zone in (
        ("9999-12-31T23:00:00", "America/New_York"),
        ("0001-01-01T00:30:00", "Asia/Tokyo"),
    ):
        with pytest.raises(InvalidInputError) as caught:
            parse_occurred(
                period=period,
                precision="exact_datetime",
                confidence="high",
                timezone_name=zone,
            )
        assert caught.value.diagnostic_class == "invalid_time"
        assert caught.value.exit_code == 2


def test_a_derived_daily_placement_at_the_edge_stays_in_exit_class_two() -> None:
    """§11 rule 54: a refused supplied value is never an integrity fault.

    `log today` builds its placement from the service clock rather than from
    anything the owner typed, so it is the one construction that could report
    the new refusal as exit class 1 instead.
    """
    edge = datetime(9999, 12, 31, 12, 0, tzinfo=timezone.utc)
    for now, zone in (
        # The derived day carries no width at or west of UTC …
        (edge, "Etc/UTC"),
        (edge, "America/New_York"),
        # … and east of it the clock itself has no local date at all.
        (datetime(9999, 12, 31, 23, 0, tzinfo=timezone.utc), "Asia/Tokyo"),
    ):
        with pytest.raises(InvalidInputError) as caught:
            today_occurred(now=now, timezone_name=zone)
        assert caught.value.exit_code == 2

    # An east-shifted day at the same clock moves away from the edge and is
    # not over-rejected, exactly as the placement boundary treats it.
    assert today_occurred(now=edge, timezone_name="Asia/Tokyo").start.isoformat() == (
        "9999-12-31T00:00:00+09:00"
    )
    assert today_occurred(now=FIXED_NOW, timezone_name="Etc/UTC").precision == (
        "exact_day"
    )


def test_every_allocated_id_delegates_to_a_version_4_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§12 rule 11: random allocation is the whole anti-reuse mechanism."""

    kinds = (
        "raw_log",
        "evidence_item",
        "fact",
        "gap",
        "contradiction",
        "snapshot",
        "claim",
        "finding",
        "job_description",
        "jd_requirement",
        "run",
        "gen",
    )
    drawn: list[UUID] = []
    source = capture_service.uuid4

    def recording_uuid4() -> UUID:
        value = source()
        drawn.append(value)
        return value

    monkeypatch.setattr(capture_service, "uuid4", recording_uuid4)
    for kind in kinds:
        allocated = new_id(kind)
        prefix, _, suffix = allocated.partition("_")
        assert prefix
        assert len(drawn) == kinds.index(kind) + 1
        assert drawn[-1].version == 4
        assert suffix == drawn[-1].hex

    monkeypatch.undo()
    for kind in kinds:
        allocated = [new_id(kind) for _ in range(256)]
        assert len(set(allocated)) == 256
        assert not set(allocated) & {new_id(kind) for _ in range(256)}
