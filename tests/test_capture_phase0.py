"""Stage 1 capture and strict-boundary acceptance tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from exp2res.domain.models import RAW_TEXT_LIMIT, OccurredAt
from exp2res.errors import ForbiddenPathError, IdCollisionError, InvalidInputError
from exp2res.services.capture import (
    capture_daily,
    capture_daily_file,
    capture_retro,
)
from exp2res.services.logs import list_logs, show_log
from exp2res.services.time_input import parse_occurred

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
            "self_signals",
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
