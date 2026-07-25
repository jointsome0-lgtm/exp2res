"""Issue #159 owner-controlled artifact locator capture coverage."""

from __future__ import annotations

import builtins
import http.client
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote
import urllib.request

import pytest
from typer.main import get_command
from typer.testing import CliRunner

import exp2res.cli as cli_module
from exp2res.cli import app
from exp2res.config import load_workspace_config
from exp2res.domain.models import EvidenceItem, RawLog, STRING_LIMIT
from exp2res.errors import (
    InvalidInputError,
    LLMInvocationError,
    LocatorReauthorizationFailedError,
)
from exp2res.services.capture import capture_daily
from exp2res.services.correction import capture_correction
from exp2res.services.logs import delete_log, show_log
from exp2res.services.source_files import reauthorize_prompt_locators
from exp2res.storage.repository import (
    insert_evidence_item,
    insert_raw_log,
    list_experience_facts,
)
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW, configure_timezone
from fakes import FakeContractRunner
from test_stage3_extraction import (
    TestIds,
    exact_day,
    fact_response,
    run_stage3,
)


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle, pytest.mark.invariant]
runner = CliRunner()


def _capture_ids(*values: str):
    iterator = iter(values)

    def allocate(_kind: str) -> str:
        return next(iterator)

    return allocate


def _counts(workspace: Path) -> tuple[int, int]:
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0],
        )


def _invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
    )
    return result, json.loads(result.stdout)


def _set_ignore_paths(workspace: Path, *patterns: str) -> None:
    config = workspace / ".exp2res" / "config.toml"
    encoded_patterns = ", ".join(json.dumps(pattern) for pattern in patterns)
    config.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        '[llm]\nadapter = "codex-cli"\nmodel = "gpt-5.6-sol"\n\n'
        f"[privacy]\nignore_paths = [{encoded_patterns}]\n",
        encoding="utf-8",
    )
    config.chmod(0o600)


def test_artifact_locators_round_trip_in_canonical_order_without_dereference(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.51: classification is exact, order is canonical, values stay inert."""

    first = tmp_path / "Vera Example artifact one.md"
    first.write_text("Vera Example inert artifact one.\n", encoding="utf-8")
    alias = tmp_path / "Vera Example artifact alias.md"
    alias.symlink_to(first)
    second = tmp_path / "Vera Example artifact two.md"
    second.write_text("Vera Example inert artifact two.\n", encoding="utf-8")
    remote = "vera+demo://Owner/Artifact%2FOne?Keep=Bytes#Fragment"

    def unexpected(*_args, **_kwargs):
        raise AssertionError("an artifact locator was dereferenced")

    monkeypatch.setattr(builtins, "open", unexpected)
    monkeypatch.setattr(Path, "open", unexpected)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    monkeypatch.setattr(http.client.HTTPConnection, "request", unexpected)
    monkeypatch.setattr(
        "exp2res.services.capture.read_capture_file",
        unexpected,
    )

    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured inert artifact provenance.",
        # Supplied deliberately out of canonical order: §13.1 orders the
        # created items by stored value, not by the order they were typed.
        artifacts=(
            remote,
            f"file:{quote(str(second), safe='/')}",
            str(alias),
        ),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_artifact_order",
            "evi_vera_artifact_manual",
            "evi_vera_artifact_first",
            "evi_vera_artifact_second",
            "evi_vera_artifact_remote",
        ),
    )

    assert [item.strength for item in captured.evidence_items] == [
        "manual_claim",
        "artifact_reference",
        "artifact_reference",
        "artifact_reference",
    ]
    assert [
        (item.path, item.uri) for item in captured.evidence_items[1:]
    ] == [
        (str(first.resolve()), None),
        (str(second.resolve()), None),
        (None, remote),
    ]
    for item in captured.evidence_items[1:]:
        assert item.title is None
        assert item.summary == "Owner-supplied artifact reference."
        assert item.metadata == {}

    inspected = show_log(workspace, log_id=captured.raw_log.id)
    assert inspected.evidence_items == captured.evidence_items


@pytest.mark.parametrize(
    ("case", "diagnostic"),
    [
        ("unresolvable", "artifact_locator_unresolved"),
        ("denied", "artifact_locator_denied"),
        ("ignored", "artifact_locator_ignored"),
        ("drive", "artifact_locator_path_unsupported"),
        ("slash_drive_file_uri", "artifact_locator_path_unsupported"),
        ("unc", "artifact_locator_path_unsupported"),
        ("backslash", "artifact_locator_path_unsupported"),
    ],
)
def test_rejected_local_artifact_locator_is_atomic(
    workspace: Path,
    tmp_path: Path,
    case: str,
    diagnostic: str,
) -> None:
    """§21.51: local authorization failures precede every candidate row."""

    if case == "unresolvable":
        locator = str(tmp_path / "Vera Example missing artifact.md")
    elif case == "denied":
        denied = tmp_path / ".env"
        denied.write_text("Vera Example synthetic denied value.\n", encoding="utf-8")
        locator = str(denied)
    elif case == "ignored":
        ignored = tmp_path / "Vera Example ignored artifact.md"
        ignored.write_text("Vera Example ignored artifact.\n", encoding="utf-8")
        configure_timezone(workspace)
        config = workspace / ".exp2res" / "config.toml"
        config.write_text(
            '[workspace]\ntimezone = "Etc/UTC"\n\n'
            '[llm]\nadapter = "codex-cli"\nmodel = "gpt-5.6-sol"\n\n'
            '[privacy]\nignore_paths = ["*ignored artifact.md"]\n',
            encoding="utf-8",
        )
        config.chmod(0o600)
        locator = str(ignored)
    elif case == "drive":
        locator = r"C:\Vera Example\artifact.md"
    elif case == "slash_drive_file_uri":
        locator = "file:///C:/Vera-Example/artifact.md"
    elif case == "unc":
        locator = r"\\server\Vera Example\artifact.md"
    else:
        locator = r"Vera Example\artifact.md"

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example rejected locator candidate.",
            artifacts=(locator,),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.exit_code == 2
    assert failure.value.diagnostic_class == diagnostic
    assert _counts(workspace) == (0, 0)


@pytest.mark.parametrize(
    "locator",
    (
        "https://example.invalid/a b",
        "https://example.invalid/%ZZ",
    ),
    ids=("whitespace", "malformed-percent"),
)
def test_malformed_remote_artifact_uri_is_rejected(
    workspace: Path,
    locator: str,
) -> None:
    """§21.51: complete absolute-URI syntax is checked without normalization."""

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example rejected malformed remote provenance.",
            artifacts=(locator,),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.diagnostic_class == "artifact_locator_invalid"
    assert _counts(workspace) == (0, 0)


@pytest.mark.parametrize(
    ("locators", "diagnostic"),
    [
        (
            ("https://example.invalid/Vera-Example\x00artifact",),
            "artifact_locator_invalid",
        ),
        (
            ("https://example.invalid/" + "x" * STRING_LIMIT,),
            "artifact_locator_invalid",
        ),
        (
            tuple(f"vera-example:{index}" for index in range(17)),
            "artifact_locator_limit",
        ),
    ],
)
def test_remote_hygiene_and_count_fail_before_persistence(
    workspace: Path,
    locators: tuple[str, ...],
    diagnostic: str,
) -> None:
    """§21.51: remote provenance stays bounded and the option is capped."""

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example rejected remote locator candidate.",
            artifacts=locators,
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.diagnostic_class == diagnostic
    assert _counts(workspace) == (0, 0)


def test_captured_local_locator_is_reauthorized_before_prompt_serialization(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§21.51 / §24.55: a later ignore rule fails Stage 3 without row mutation."""

    artifact = tmp_path / "Vera Example later ignored artifact.md"
    artifact.write_text(
        "Vera Example inert artifact retained at its owner path.\n",
        encoding="utf-8",
    )
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured local artifact provenance.",
        artifacts=(str(artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_reauthorization_capture",
            "evi_vera_reauthorization_manual",
            "evi_vera_reauthorization_artifact",
        ),
    )
    persisted_path = captured.evidence_items[1].path
    assert persisted_path == str(artifact.resolve())
    capture_correction(
        workspace,
        log_id=captured.raw_log.id,
        raw_text="Vera Example corrected the captured artifact statement.",
        occurred=captured.raw_log.occurred,
        project=captured.raw_log.project,
        clock=lambda: FIXED_NOW.replace(hour=13),
        id_factory=_capture_ids(
            "log_vera_reauthorization_correction",
            "evi_vera_reauthorization_correction",
        ),
    )
    _set_ignore_paths(workspace, "*later ignored artifact.md")

    fake = FakeContractRunner([])
    with pytest.raises(LocatorReauthorizationFailedError) as failure:
        run_stage3(
            workspace,
            fake,
            TestIds(),
            log_id=captured.raw_log.id,
        )
    assert failure.value.exit_code == 7
    assert failure.value.diagnostic_class == "locator_reauthorization_failed"
    assert fake.calls == []
    inspected = show_log(workspace, log_id=captured.raw_log.id)
    assert inspected.raw_log == captured.raw_log
    assert inspected.evidence_items == captured.evidence_items
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM processing_runs"
        ).fetchone()[0] == 0


def test_import_file_locators_share_prompt_reauthorization_boundary(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§21.51 / §24.55: imported path fields fail closed under current policy."""

    source = tmp_path / "Vera Example imported design.md"
    source.write_text(
        "Vera Example synthetic imported design evidence.\n",
        encoding="utf-8",
    )
    stored_path = str(source.resolve())
    raw_log = RawLog(
        id="log_vera_import_reauthorization",
        recorded_at=FIXED_NOW,
        entry_type="design_doc",
        source_type="imported_artifact",
        occurred=exact_day(15),
        raw_text="Vera Example synthetic imported design evidence.",
        project="Vera Example Project",
        external_ref=stored_path,
        corrects_log_id=None,
        metadata={},
    )
    evidence = EvidenceItem(
        id="evi_vera_import_reauthorization",
        created_at=FIXED_NOW,
        raw_log_id=raw_log.id,
        title="Vera Example imported design",
        summary="Vera Example imported design support.",
        uri=None,
        path=stored_path,
        strength="design_doc",
        metadata={},
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        insert_raw_log(connection, raw_log)
        insert_evidence_item(connection, evidence)
        connection.commit()
    _set_ignore_paths(workspace, "*imported design.md")

    fake = FakeContractRunner([])
    with pytest.raises(LocatorReauthorizationFailedError) as failure:
        run_stage3(workspace, fake, TestIds(), log_id=raw_log.id)
    assert failure.value.diagnostic_class == "locator_reauthorization_failed"
    assert fake.calls == []
    with read_database(workspace) as connection:
        stored_log = connection.execute(
            "SELECT external_ref FROM raw_logs WHERE id = ?",
            (raw_log.id,),
        ).fetchone()
        stored_evidence = connection.execute(
            "SELECT path, uri FROM evidence_items WHERE id = ?",
            (evidence.id,),
        ).fetchone()
        assert tuple(stored_log) == (stored_path,)
        assert tuple(stored_evidence) == (stored_path, None)


def test_remote_persisted_locators_are_not_resolved_again(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.51 / §24.55: non-local schemes remain inert provenance."""

    remote = "https://example.invalid/Vera-Example-remote-artifact"
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured remote artifact provenance.",
        external_ref=remote,
        artifacts=(remote,),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_remote_reauthorization",
            "evi_vera_remote_manual",
            "evi_vera_remote_artifact",
        ),
    )
    original_resolve = Path.resolve

    def reject_remote_path(
        path: Path, *args: object, **kwargs: object
    ) -> Path:
        if str(path).startswith("https:"):
            raise AssertionError("a remote locator reached path resolution")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", reject_remote_path)
    fake = FakeContractRunner(
        [fact_response([captured.evidence_items[1].id])]
    )
    result = run_stage3(
        workspace,
        fake,
        TestIds(),
        log_id=captured.raw_log.id,
    )
    assert len(result.created) == 1
    serialized = json.loads(fake.calls[0].serialized_input)
    assert serialized["raw_logs"][0]["external_ref"] == remote
    serialized_artifact = next(
        item
        for item in serialized["evidence_items"]
        if item["id"] == captured.evidence_items[1].id
    )
    assert serialized_artifact["uri"] == remote


def test_equivalent_local_locators_are_rejected_not_deduplicated(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§21.51: duplicate identity is the exact resulting stored field/value."""

    artifact = tmp_path / "Vera Example duplicate artifact.md"
    artifact.write_text("Vera Example duplicate artifact.\n", encoding="utf-8")
    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example duplicate locator candidate.",
            artifacts=(str(artifact), f"file:{quote(str(artifact), safe='/')}"),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.diagnostic_class == "artifact_locator_duplicate"
    assert _counts(workspace) == (0, 0)


def test_repeatable_cli_artifacts_reach_logs_show_projection(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§21.51 / §24.55: CLI capture and inspection agree on canonical order."""

    source = tmp_path / "Vera Example daily source.md"
    source.write_text("Vera Example captured a locator through CLI.\n", encoding="utf-8")
    local = tmp_path / "Vera Example CLI artifact.md"
    local.write_text("Vera Example CLI artifact.\n", encoding="utf-8")
    remote = "https://example.invalid/Vera-Example-CLI"
    created, envelope = _invoke_json(
        workspace,
        [
            "log",
            "today",
            "--file",
            str(source),
            "--artifact",
            str(local),
            "--artifact",
            remote,
        ],
    )
    assert created.exit_code == 0, created.stderr
    log_id = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )

    shown, projection = _invoke_json(
        workspace, ["logs", "show", "--log-id", log_id]
    )
    assert shown.exit_code == 0, shown.stderr
    items = projection["result"]["evidence_items"]
    assert [item["strength"] for item in items] == [
        "manual_claim",
        "artifact_reference",
        "artifact_reference",
    ]
    assert items[1]["path"] == str(local.resolve())
    assert items[1]["uri"] is None
    assert items[2]["uri"] == remote
    assert items[2]["path"] is None


def test_every_owner_capture_command_exposes_artifact_option() -> None:
    """§14.2–§14.4 / §14.7: all four decided command forms parse the option."""

    # Declared parameters, not rendered help: help output wraps and truncates
    # at the terminal width, which is not part of the §14 command contract.
    root = get_command(app)
    for path in (
        ("log", "today"),
        ("log", "retro"),
        ("correction", "add"),
        ("gaps", "answer"),
    ):
        command = root
        for name in path:
            command = command.commands[name]
        options = {
            option
            for parameter in command.params
            for option in parameter.opts
        }
        assert "--artifact" in options, path
        parameter = next(
            item for item in command.params if "--artifact" in item.opts
        )
        assert parameter.multiple, path


def test_retro_cli_accepts_artifact_locator(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§14.3 / §21.51: the interactive retro form forwards its locator."""

    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    remote = "urn:vera-example:retro-artifact"
    result = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "log",
            "retro",
            "--artifact",
            remote,
        ],
        input=(
            "2026-07\n"
            "month\n"
            "medium\n"
            "Vera Example Retro\n"
            "Vera Example reconstructed an artifact-backed event.\n"
        ),
    )
    envelope = json.loads(result.stdout.splitlines()[-1])
    assert result.exit_code == 0, result.stderr
    evidence_ids = next(
        group["ids"]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "evidence_item"
    )
    assert len(evidence_ids) == 2
    log_id = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    inspected = show_log(workspace, log_id=log_id)
    assert inspected.evidence_items[1].uri == remote


def test_owner_only_corrected_lineage_reaches_unchanged_high_ceiling(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§9.4 / §21.51: same-log medium; corrected two-log support permits high."""

    remote = "https://example.invalid/Vera-Example-calibration"
    root = capture_daily(
        workspace,
        raw_text="Vera Example captured one artifact-backed owner assertion.",
        artifacts=(remote,),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_owner_root",
            "evi_vera_owner_manual",
            "evi_vera_owner_artifact",
        ),
    )
    artifact_id = root.evidence_items[1].id
    stage_ids = TestIds()
    invalid_high = fact_response([artifact_id], confidence="high")
    with pytest.raises(LLMInvocationError):
        run_stage3(
            workspace,
            FakeContractRunner([invalid_high, invalid_high]),
            stage_ids,
            log_id=root.raw_log.id,
        )
    medium = run_stage3(
        workspace,
        FakeContractRunner([fact_response([artifact_id], confidence="medium")]),
        stage_ids,
        log_id=root.raw_log.id,
    )
    assert len(medium.created) == 1

    corrected = capture_correction(
        workspace,
        log_id=root.raw_log.id,
        raw_text="Vera Example corrected and restated the artifact-backed work.",
        occurred=root.raw_log.occurred,
        project=root.raw_log.project,
        clock=lambda: FIXED_NOW.replace(hour=13),
        id_factory=_capture_ids(
            "log_vera_owner_correction",
            "evi_vera_owner_correction_manual",
        ),
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("displaced artifact support was dereferenced")

    monkeypatch.setattr(builtins, "open", unexpected)
    monkeypatch.setattr(Path, "open", unexpected)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    monkeypatch.setattr(http.client.HTTPConnection, "request", unexpected)
    high = run_stage3(
        workspace,
        FakeContractRunner(
            [
                fact_response(
                    [artifact_id, corrected.evidence_items[0].id],
                    confidence="high",
                )
            ]
        ),
        stage_ids,
        log_id=root.raw_log.id,
    )
    with read_database(workspace) as connection:
        fact = next(
            item
            for item in list_experience_facts(connection)
            if item.id in high.created
        )
    assert fact.confidence == "high"
    assert set(fact.source_log_ids) == {
        root.raw_log.id,
        corrected.raw_log.id,
    }



def test_prompt_reauthorization_rejects_windows_forms_and_skips_external_ref(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: a drive letter is not a remote scheme; #171 owns external_ref.

    A Windows drive letter parses as a one-character URI scheme, so the
    unsupported-form check must precede the remote-scheme shortcut or the form
    reaches the provider. `RawLog.external_ref` stays outside this boundary
    while issue #171 settles whether the supplied or canonical spelling is
    persisted — re-checking a relative spelling here would resolve it against
    the working directory of whichever later command runs the stage.
    """

    config = load_workspace_config(workspace)
    for value in ("C:/Vera Example/artifact.md", r"C:\Vera Example\artifact.md"):
        with pytest.raises(LocatorReauthorizationFailedError) as failure:
            reauthorize_prompt_locators({"path": value}, config=config)
        assert failure.value.diagnostic_class == "locator_reauthorization_failed"

    reauthorize_prompt_locators(
        {"external_ref": "Vera Example notes/missing-today.md"}, config=config
    )


def test_deletion_report_orders_evidence_ids_by_identity(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§14.14 rule 5: reported ID groups use stable identity, not §13.1 order."""

    artifacts = []
    for name in ("zeta", "alpha"):
        source = tmp_path / f"Vera Example {name} artifact.md"
        source.write_text(f"Vera Example {name} artifact.\n", encoding="utf-8")
        artifacts.append(str(source))

    captured = capture_daily(
        workspace,
        raw_text="Vera Example deletion report ordering.",
        artifacts=tuple(artifacts),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_deletion_order",
            "evi_vera_deletion_manual",
            "evi_vera_deletion_alpha",
            "evi_vera_deletion_zeta",
        ),
    )
    presented = [item.id for item in captured.evidence_items]

    deleted = delete_log(workspace, log_id=captured.raw_log.id)
    assert sorted(deleted.evidence_item_ids) == sorted(presented)
    assert list(deleted.evidence_item_ids) == sorted(
        presented, key=lambda value: value.encode("utf-8")
    )
    assert list(deleted.evidence_item_ids) != presented
