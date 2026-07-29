"""§21.52 non-prompt `correction add --file` capture (§14.4)."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.services.lifecycle as lifecycle_service
from exp2res.cli import app
from exp2res.services.logs import list_logs, show_log
from exp2res.storage.workspace import CONFIG_TEMPLATE

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_cli_correction import _lifecycle_response
from test_stage3_extraction import SELECTION, add_log, budgets, exact_day


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]

TARGET_ID = "log_vera_correction_source"


def install_lifecycle_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lifecycle_service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner([_lifecycle_response] * 12),
        ),
    )


def seed_target(workspace: Path):
    target, _items = add_log(
        workspace,
        log_id=TARGET_ID,
        recorded_at=FIXED_NOW - timedelta(hours=2),
        raw_text="Vera Example originally described a provenance workflow.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_correction_source", "manual_claim"),),
        project="Vera Example Project",
    )
    return target


def invoke_json(
    workspace: Path, arguments: list[str], *, input: str | bytes | None = None
):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
        input=input,
    )
    return result, json.loads(result.stdout.splitlines()[-1])


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


def test_file_and_stdin_corrections_round_trip_and_copy_placement(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§21.52 / §24.56: lossless bytes, canonical external_ref, copied context."""

    target = seed_target(workspace)
    install_lifecycle_runner(monkeypatch)

    source_bytes = (
        b"Vera Example restates the whole record.\n\nSecond restated line.\n"
    )
    source = tmp_path / "Vera Example correction.md"
    source.write_bytes(source_bytes)
    file_result, file_envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            "--owner-authored",
            "--artifact",
            "urn:vera-example:correction",
        ],
    )
    assert file_result.exit_code == 0, file_result.output

    corrected = show_log(workspace, log_id=created_log_id(file_envelope))
    assert corrected.raw_log.raw_text.encode("utf-8") == source_bytes
    assert corrected.raw_log.external_ref == str(source)
    assert (
        corrected.raw_log.entry_type,
        corrected.raw_log.source_type,
    ) == ("correction", "manual_entry")
    assert corrected.raw_log.corrects_log_id == target.id
    # Copy-unless-replaced: no temporal or project flag leaves both exactly as
    # the target stored them, with no precision increase and no silent strip.
    assert corrected.raw_log.occurred == target.occurred
    assert corrected.raw_log.project == target.project
    assert [item.strength for item in corrected.evidence_items] == [
        "manual_claim",
        "artifact_reference",
    ]

    stdin_bytes = "Vera Example corrects the correction.\n".encode("utf-8")
    stdin_result, stdin_envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            corrected.raw_log.id,
            "--file",
            "-",
            "--owner-authored",
        ],
        input=stdin_bytes,
    )
    assert stdin_result.exit_code == 0, stdin_result.output
    from_stdin = show_log(workspace, log_id=created_log_id(stdin_envelope))
    assert from_stdin.raw_log.raw_text.encode("utf-8") == stdin_bytes
    # Standard input selects no filesystem object, so it records no locator.
    assert from_stdin.raw_log.external_ref is None
    assert from_stdin.raw_log.corrects_log_id == corrected.raw_log.id


@pytest.mark.parametrize(
    ("flags", "expected_precision", "expected_project"),
    [
        (
            ["--precision", "month", "--period", "2026-07", "--confidence", "high"],
            "month",
            "Vera Example Project",
        ),
        (["--project", "Vera Example Replacement"], "exact_day", "Vera Example Replacement"),
        (["--clear-project"], "exact_day", None),
    ],
    ids=["temporal-set", "project-replacement", "project-cleared"],
)
def test_explicit_replacements_take_effect_exactly(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    expected_precision: str,
    expected_project: str | None,
) -> None:
    """§21.52 / §24.56: each explicit flag replaces exactly what it names."""

    target = seed_target(workspace)
    install_lifecycle_runner(monkeypatch)
    source = tmp_path / "Vera Example replacement.md"
    source.write_text("Vera Example restates the record in full.\n")

    result, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            "--owner-authored",
            *flags,
        ],
    )
    assert result.exit_code == 0, result.output
    corrected = show_log(workspace, log_id=created_log_id(envelope))
    assert corrected.raw_log.occurred.precision == expected_precision
    assert corrected.raw_log.project == expected_project


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ([], "owner_authorship_required"),
        (["--precision", "month"], "owner_authorship_required"),
    ],
    ids=["bare", "with-replacement-flags"],
)
def test_missing_affirmation_fails_before_acquisition(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    diagnostic: str,
) -> None:
    """§21.52 / §24.56: affirmation is explicit and pre-acquisition."""

    target = seed_target(workspace)
    assert "owner_authored" not in CONFIG_TEMPLATE
    monkeypatch.setenv("EXP2RES_OWNER_AUTHORED", "1")
    source = tmp_path / "Vera Example unread.md"
    source.write_text("Vera Example restates the record in full.\n")

    result, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            *arguments,
        ],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == diagnostic
    # Nothing beyond the seeded target was written.
    assert [log.id for log in list_logs(workspace)] == [target.id]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--precision", "month", "--period", "2026-07", "--confidence", "high"],
        ["--project", "Vera Example Replacement"],
        ["--clear-project"],
    ],
    ids=["temporal", "project", "clear-project"],
)
def test_replacement_flags_without_file_are_invalid_usage(
    workspace: Path, arguments: list[str]
) -> None:
    """§14.4: the replacement flags belong to the non-prompt form alone."""

    target = seed_target(workspace)
    result, envelope = invoke_json(
        workspace,
        ["--yes", "correction", "add", "--log-id", target.id, *arguments],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "invalid_usage"
    assert [log.id for log in list_logs(workspace)] == [target.id]


def test_project_replacement_and_clear_are_mutually_exclusive(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.4: replacing and clearing the label are contradictory intents."""

    target = seed_target(workspace)
    source = tmp_path / "Vera Example conflict.md"
    source.write_text("Vera Example restates the record in full.\n")
    result, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            "--owner-authored",
            "--project",
            "Vera Example Replacement",
            "--clear-project",
        ],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "invalid_usage"
    assert [log.id for log in list_logs(workspace)] == [target.id]


@pytest.mark.parametrize(
    ("flags", "diagnostic"),
    [
        (["--precision", "month"], "input_required"),
        (["--precision", "unknown", "--period", "2026-07", "--confidence", "high"],
         "period_not_allowed"),
        (["--precision", "month", "--confidence", "high"], "input_required"),
    ],
    ids=["partial-set", "unknown-with-period", "missing-period"],
)
def test_temporal_replacement_requires_the_whole_typed_set(
    workspace: Path,
    tmp_path: Path,
    flags: list[str],
    diagnostic: str,
) -> None:
    """§21.52 / §14.3: a partial replacement is never completed by a prompt."""

    target = seed_target(workspace)
    source = tmp_path / "Vera Example partial.md"
    source.write_text("Vera Example restates the record in full.\n")
    result, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            "--owner-authored",
            *flags,
        ],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == diagnostic
    assert [log.id for log in list_logs(workspace)] == [target.id]


def test_affirmation_does_not_supply_the_rebuild_consent(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§21.52 / §14.14 rule 3: --owner-authored never implies --yes."""

    target = seed_target(workspace)

    def refuse_build(_workspace: Path):
        raise AssertionError("adapter construction ran before cost consent")

    monkeypatch.setattr(lifecycle_service, "build_llm_execution", refuse_build)
    source = tmp_path / "Vera Example unconsented.md"
    source.write_text("Vera Example restates the record in full.\n")
    result, envelope = invoke_json(
        workspace,
        [
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            "--owner-authored",
        ],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"
    assert [log.id for log in list_logs(workspace)] == [target.id]


def test_missing_consent_fails_before_the_source_is_read(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 3: `--file -` never blocks on a pipe it will not use."""

    target = seed_target(workspace)

    def refuse_read(*_args, **_kwargs):
        raise AssertionError("the source was acquired before cost consent")

    monkeypatch.setattr(cli_module, "read_correction_source", refuse_read)
    result, envelope = invoke_json(
        workspace,
        [
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            "-",
            "--owner-authored",
        ],
        input=b"Vera Example text that must never be consumed.\n",
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "input_required"
    assert [log.id for log in list_logs(workspace)] == [target.id]


def clear_workspace_timezone(workspace: Path) -> None:
    config = workspace / ".exp2res" / "config.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace('timezone = "Etc/UTC"', 'timezone = ""'), encoding="utf-8"
    )


def test_copied_placement_needs_no_workspace_timezone(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 8: copying an already-resolved placement uses no local time."""

    target = seed_target(workspace)
    install_lifecycle_runner(monkeypatch)
    clear_workspace_timezone(workspace)
    source = tmp_path / "Vera Example untimed.md"
    source.write_text("Vera Example restates the record in full.\n")

    result, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            "--owner-authored",
        ],
    )
    assert result.exit_code == 0, result.output
    corrected = show_log(workspace, log_id=created_log_id(envelope))
    assert corrected.raw_log.occurred == target.occurred


def test_explicit_temporal_replacement_still_requires_the_timezone(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 8: replacing a placement is the local-time feature boundary."""

    target = seed_target(workspace)
    clear_workspace_timezone(workspace)
    source = tmp_path / "Vera Example replacement.md"
    source.write_text("Vera Example restates the record in full.\n")

    result, envelope = invoke_json(
        workspace,
        [
            "--yes",
            "correction",
            "add",
            "--log-id",
            target.id,
            "--file",
            str(source),
            "--owner-authored",
            "--precision",
            "month",
            "--period",
            "2026-07",
            "--confidence",
            "high",
        ],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == "workspace_timezone_required"
    assert [log.id for log in list_logs(workspace)] == [target.id]
