"""§21.52 non-prompt owner-affirmed capture and retro uncertainty tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.services.detection as detection_service
from exp2res.cli import app
from exp2res.services.logs import list_logs, show_log
from exp2res.storage.workspace import CONFIG_TEMPLATE

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage4_detection import DetectionIds, detector_response, prepare_fact


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(
    workspace: Path, arguments: list[str], *, input: str | bytes | None = None
):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
        input=input,
    )
    return result, json.loads(result.stdout)


def seed_gap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    ids = DetectionIds()
    fact_id, log_id, _item_id = prepare_fact(workspace, ids)
    payload = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    fake = FakeContractRunner([payload])
    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    result, envelope = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert result.exit_code == 0
    return envelope["result"]["gaps"][0]["id"]


def created_log_id(envelope: dict[str, object]) -> str:
    affected = envelope["affected_ids"]
    assert isinstance(affected, dict)
    created = affected["created"]
    assert isinstance(created, list)
    return next(
        group["ids"][0]
        for group in created
        if group["entity_type"] == "raw_log"
    )


def test_multiline_file_and_stdin_capture_round_trip_with_unchanged_classes(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.52 / §24.56: all non-prompt sources preserve bytes and classes."""

    gap_id = seed_gap(workspace, monkeypatch)
    daily_bytes = b"Vera Example daily first line.\n\nDaily second line.\n"
    daily_file = tmp_path / "Vera Example daily multiline.md"
    daily_file.write_bytes(daily_bytes)
    daily_result, daily_envelope = invoke_json(
        workspace,
        [
            "log",
            "today",
            "--file",
            str(daily_file),
            "--owner-authored",
            "--artifact",
            "urn:vera-example:daily",
        ],
    )
    assert daily_result.exit_code == 0

    daily_stdin_bytes = b"Vera Example daily stdin first line.\n\nDaily stdin second line.\n"
    daily_stdin_result, daily_stdin_envelope = invoke_json(
        workspace,
        ["log", "today", "--file", "-", "--owner-authored"],
        input=daily_stdin_bytes,
    )
    assert daily_stdin_result.exit_code == 0

    retro_file_bytes = b"Vera Example retro file first line.\n\nRetro file second line.\n"
    retro_file = tmp_path / "Vera Example retro multiline.md"
    retro_file.write_bytes(retro_file_bytes)
    retro_file_result, retro_file_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(retro_file),
            "--precision",
            "month",
            "--period",
            "2026-07",
            "--confidence",
            "medium",
            "--owner-authored",
        ],
    )
    assert retro_file_result.exit_code == 0

    retro_bytes = b"Vera Example retro first line.\n\nRetro second line.\n"
    retro_result, retro_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            "-",
            "--precision",
            "unknown",
            "--confidence",
            "low",
            "--project",
            "Vera Example Migration",
            "--owner-authored",
            "--artifact",
            "urn:vera-example:retro",
        ],
        input=retro_bytes,
    )
    assert retro_result.exit_code == 0

    answer_bytes = b"Vera Example answer first line.\n\nAnswer second line.\n"
    answer_result, answer_envelope = invoke_json(
        workspace,
        [
            "gaps",
            "answer",
            "--gap-id",
            gap_id,
            "--file",
            "-",
            "--owner-authored",
            "--artifact",
            "urn:vera-example:answer",
        ],
        input=answer_bytes,
    )
    assert answer_result.exit_code == 0

    daily = show_log(workspace, log_id=created_log_id(daily_envelope))
    daily_stdin = show_log(
        workspace, log_id=created_log_id(daily_stdin_envelope)
    )
    retro_file_log = show_log(
        workspace, log_id=created_log_id(retro_file_envelope)
    )
    retro = show_log(workspace, log_id=created_log_id(retro_envelope))
    answer = show_log(workspace, log_id=created_log_id(answer_envelope))

    assert daily.raw_log.raw_text.encode("utf-8") == daily_bytes
    assert daily.raw_log.external_ref == str(daily_file)
    assert (daily.raw_log.entry_type, daily.raw_log.source_type) == (
        "manual_daily",
        "manual_entry",
    )
    assert daily_stdin.raw_log.raw_text.encode("utf-8") == daily_stdin_bytes
    assert daily_stdin.raw_log.external_ref is None
    assert (daily_stdin.raw_log.entry_type, daily_stdin.raw_log.source_type) == (
        "manual_daily",
        "manual_entry",
    )
    assert retro_file_log.raw_log.raw_text.encode("utf-8") == retro_file_bytes
    assert retro_file_log.raw_log.external_ref == str(retro_file)
    assert (
        retro_file_log.raw_log.entry_type,
        retro_file_log.raw_log.source_type,
    ) == ("manual_retro", "user_memory")
    assert retro.raw_log.raw_text.encode("utf-8") == retro_bytes
    assert retro.raw_log.external_ref is None
    assert (retro.raw_log.entry_type, retro.raw_log.source_type) == (
        "manual_retro",
        "user_memory",
    )
    assert retro.raw_log.occurred.precision == "unknown"
    assert retro.raw_log.occurred.start is None
    assert retro.raw_log.occurred.end is None
    assert answer.raw_log.raw_text.encode("utf-8") == answer_bytes
    assert answer.raw_log.external_ref is None
    assert (answer.raw_log.entry_type, answer.raw_log.source_type) == (
        "gap_answer",
        "manual_entry",
    )
    for bundle in (daily, retro, answer):
        assert [item.strength for item in bundle.evidence_items] == [
            "manual_claim",
            "artifact_reference",
        ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--yes", "log", "today", "--file", "Vera Example missing.md"],
        [
            "log",
            "retro",
            "--file",
            "Vera Example missing.md",
            "--precision",
            "month",
            "--period",
            "2026-07",
            "--confidence",
            "medium",
        ],
        [
            "gaps",
            "answer",
            "--gap-id",
            "gap_vera_example_missing",
            "--file",
            "Vera Example missing.md",
        ],
    ],
    ids=["daily-yes-is-not-affirmation", "retro", "gap-answer"],
)
def test_missing_owner_authorship_fails_before_every_nonprompt_source_read(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    """§21.52 / §24.56: affirmation is explicit, uniform, and pre-acquisition."""

    assert "owner_authored" not in CONFIG_TEMPLATE
    monkeypatch.setenv("EXP2RES_OWNER_AUTHORED", "1")
    result, envelope = invoke_json(workspace, arguments)
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "owner_authorship_required"
    assert list_logs(workspace) == ()


def test_interactive_unknown_precision_skips_period_and_stores_null_bounds(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.52 / §24.56: precision is asked first and unknown needs no fiction."""

    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    answers = iter(
        [
            "unknown",
            "low",
            "",
            "Vera Example remembers the event but not when it happened.",
        ]
    )
    prompts: list[str] = []

    def prompt(label: str, **_kwargs: object) -> str:
        prompts.append(label)
        return next(answers)

    monkeypatch.setattr(typer, "prompt", prompt)
    result, envelope = invoke_json(workspace, ["log", "retro"])
    assert result.exit_code == 0
    assert prompts == [
        "How precise is this?",
        "How confident are you?",
        "Project/activity?",
        "Describe what you remember.",
    ]
    stored = show_log(workspace, log_id=created_log_id(envelope)).raw_log
    assert stored.occurred.precision == "unknown"
    assert stored.occurred.start is None
    assert stored.occurred.end is None


def test_noninteractive_retro_rejects_unknown_period_and_missing_typed_values(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§21.52 / §24.56: bad typed forms fail instead of prompting or discarding."""

    source = tmp_path / "Vera Example retro.md"
    source.write_text("Vera Example reconstructed record.\n", encoding="utf-8")
    unknown_period, unknown_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "unknown",
            "--period",
            "2026-07",
            "--confidence",
            "low",
            "--owner-authored",
        ],
    )
    assert unknown_period.exit_code == 2
    assert unknown_envelope["diagnostic_class"] == "period_not_allowed"

    missing_period, missing_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "date_range",
            "--confidence",
            "medium",
            "--owner-authored",
        ],
    )
    assert missing_period.exit_code == 2
    assert missing_envelope["diagnostic_class"] == "input_required"
    assert "What period" not in missing_period.stdout + missing_period.stderr

    malformed_range, range_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "approximate_range",
            "--period",
            "2026-07",
            "--confidence",
            "medium",
            "--owner-authored",
        ],
    )
    assert malformed_range.exit_code == 2
    assert range_envelope["diagnostic_class"] == "invalid_time_shape"
    assert list_logs(workspace) == ()


@pytest.mark.parametrize(
    ("payload", "diagnostic"),
    [
        (b"Vera Example\n" + b"x" * 1_048_564, "input_too_large"),
        (b"Vera Example invalid UTF-8: \xff\n", "input_not_utf8"),
    ],
    ids=["oversize", "invalid-utf8"],
)
def test_stdin_capture_is_bounded_utf8_and_atomic(
    workspace: Path, payload: bytes, diagnostic: str
) -> None:
    """§11 / §14.2 / §21.52: stdin shares the source-file byte gate."""

    result, envelope = invoke_json(
        workspace,
        ["log", "today", "--file", "-", "--owner-authored"],
        input=payload,
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == diagnostic
    assert list_logs(workspace) == ()
