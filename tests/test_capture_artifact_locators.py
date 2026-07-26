"""Issue #159 owner-controlled artifact locator capture coverage."""

from __future__ import annotations

import builtins
import http.client
import json
from pathlib import Path
import sqlite3
import time
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
from exp2res.services.source_files import (
    _ignore_matcher,
    reauthorize_prompt_locators,
)
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


def test_relative_capture_locators_persist_canonically_for_any_later_directory(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.51 / §24.56: the persisted form names one object, not a spelling.

    Capture runs from a directory the later stage knows nothing about and
    supplies both locator kinds relative to it. Persisting the supplied
    spelling would make the §29.4 pre-serialization re-check resolve against
    whatever directory the later command happens to run in.
    """

    capture_root = tmp_path / "Vera Example capture root"
    (capture_root / "notes").mkdir(parents=True)
    record = capture_root / "notes" / "vera-example-today.md"
    record.write_text("Vera Example relative capture record.\n", encoding="utf-8")
    artifact = capture_root / "notes" / "vera-example-artifact.md"
    artifact.write_text("Vera Example relative artifact.\n", encoding="utf-8")
    relative_record = "notes/vera-example-today.md"
    relative_artifact = "notes/vera-example-artifact.md"

    monkeypatch.chdir(capture_root)
    result, envelope = _invoke_json(
        workspace,
        [
            "log",
            "today",
            "--file",
            relative_record,
            "--owner-authored",
            "--artifact",
            relative_artifact,
        ],
    )
    assert result.exit_code == 0

    created = next(
        group["ids"][0]
        for group in envelope["affected_ids"]["created"]
        if group["entity_type"] == "raw_log"
    )
    bundle = show_log(workspace, log_id=created)
    assert bundle.raw_log.external_ref == str(record.resolve())
    assert [item.path for item in bundle.evidence_items] == [
        None,
        str(artifact.resolve()),
    ]

    # A directory that shares no ancestry with the capture directory: the
    # discarded spellings resolve to nothing here, the persisted ones still
    # name the captured files.
    elsewhere = tmp_path / "Vera Example unrelated directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config = load_workspace_config(workspace)
    reauthorize_prompt_locators(
        {
            "external_ref": bundle.raw_log.external_ref,
            "path": bundle.evidence_items[1].path,
        },
        config=config,
    )
    for field, spelling in (
        ("external_ref", relative_record),
        ("path", relative_artifact),
    ):
        with pytest.raises(LocatorReauthorizationFailedError):
            reauthorize_prompt_locators({field: spelling}, config=config)


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
            "--owner-authored",
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
            "month\n"
            "2026-07\n"
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



def test_prompt_reauthorization_rejects_windows_forms_in_every_locator_field(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: a drive letter is not a remote scheme, in any named field.

    A Windows drive letter parses as a one-character URI scheme, so the
    unsupported-form test must precede the remote-scheme shortcut or the form
    reaches the provider. §29.4 names `external_ref` alongside `path` and
    `uri`, so the gate covers it too.
    """

    config = load_workspace_config(workspace)
    values = ("C:/Vera Example/artifact.md", r"C:\Vera Example\artifact.md")
    for field in ("path", "uri", "external_ref"):
        for value in values:
            with pytest.raises(LocatorReauthorizationFailedError) as failure:
                reauthorize_prompt_locators({field: value}, config=config)
            assert failure.value.diagnostic_class == "locator_reauthorization_failed"


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


def test_root_relative_ignore_pattern_binds_capture_wherever_it_runs(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.51 / §29.4: user patterns anchor to the workspace root, not the cwd."""

    private = workspace / "private"
    private.mkdir()
    ignored = private / "Vera Example board.md"
    ignored.write_text("Vera Example private board.\n", encoding="utf-8")
    public = workspace / "public"
    public.mkdir()
    accepted = public / "Vera Example board.md"
    accepted.write_text("Vera Example shared board.\n", encoding="utf-8")
    _set_ignore_paths(workspace, "private/**")
    # The gate is evaluated from an unrelated directory: an anchored pattern
    # binds the workspace-root-relative path, so the verdict cannot depend on
    # where the owner happened to stand.
    monkeypatch.chdir(tmp_path)

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example captured a root-relative ignored locator.",
            artifacts=(str(ignored),),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.exit_code == 2
    assert failure.value.diagnostic_class == "artifact_locator_ignored"
    assert _counts(workspace) == (0, 0)

    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured a locator outside the ignored subtree.",
        artifacts=(str(accepted),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_root_relative_ignore",
            "evi_vera_root_relative_manual",
            "evi_vera_root_relative_artifact",
        ),
    )
    assert captured.evidence_items[1].path == str(accepted.resolve())


def test_root_relative_ignore_pattern_binds_prompt_reauthorization(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.51 / §29.4: the same anchored pattern holds at the §15 boundary."""

    private = workspace / "private"
    private.mkdir()
    artifact = private / "Vera Example later ignored board.md"
    artifact.write_text("Vera Example private board.\n", encoding="utf-8")
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured provenance before the ignore rule.",
        artifacts=(str(artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_anchored_reauthorization",
            "evi_vera_anchored_manual",
            "evi_vera_anchored_artifact",
        ),
    )
    _set_ignore_paths(workspace, "private/**")
    monkeypatch.chdir(tmp_path)

    fake = FakeContractRunner([])
    with pytest.raises(LocatorReauthorizationFailedError) as failure:
        run_stage3(workspace, fake, TestIds(), log_id=captured.raw_log.id)
    assert failure.value.exit_code == 7
    assert failure.value.diagnostic_class == "locator_reauthorization_failed"
    assert fake.calls == []
    inspected = show_log(workspace, log_id=captured.raw_log.id)
    assert inspected.evidence_items == captured.evidence_items


@pytest.mark.parametrize(
    "pattern",
    [
        "private/**",
        "private/",
        "private/**/Vera Example board.md",
        "**/Vera Example board.md",
        "private/**/**/**/**/**/**/**/Vera Example board.md",
    ],
    ids=[
        "recursive",
        "directory-marker",
        "zero-directory",
        "any-depth",
        "repeated-recursive",
    ],
)
def test_gitignore_pattern_forms_ignore_the_same_workspace_artifact(
    workspace: Path,
    pattern: str,
) -> None:
    """§21.51 / §29.4: every equivalent gitignore form reaches one verdict."""

    private = workspace / "private"
    private.mkdir()
    artifact = private / "Vera Example board.md"
    artifact.write_text("Vera Example private board.\n", encoding="utf-8")
    _set_ignore_paths(workspace, pattern)

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example captured an ignored locator form.",
            artifacts=(str(artifact),),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.exit_code == 2
    assert failure.value.diagnostic_class == "artifact_locator_ignored"
    assert _counts(workspace) == (0, 0)


def test_ignore_wildcards_do_not_cross_path_separators(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: a single-star segment matches exactly one component."""

    nested = workspace / "private" / "one" / "two"
    nested.mkdir(parents=True)
    matched = workspace / "private" / "one" / "Vera Example board.md"
    matched.write_text("Vera Example one-level board.\n", encoding="utf-8")
    deeper = nested / "Vera Example board.md"
    deeper.write_text("Vera Example two-level board.\n", encoding="utf-8")
    _set_ignore_paths(workspace, "private/*/Vera Example board.md")

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example captured the one-level ignored locator.",
            artifacts=(str(matched),),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.diagnostic_class == "artifact_locator_ignored"

    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured the deeper unmatched locator.",
        artifacts=(str(deeper),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_single_star",
            "evi_vera_single_star_manual",
            "evi_vera_single_star_artifact",
        ),
    )
    assert captured.evidence_items[1].path == str(deeper.resolve())


def test_directory_marker_ignores_the_selected_directory_itself(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: a trailing separator covers the directory it names."""

    private = workspace / "private"
    private.mkdir()
    (private / "Vera Example board.md").write_text(
        "Vera Example private board.\n", encoding="utf-8"
    )
    _set_ignore_paths(workspace, "private/")

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example captured the ignored directory itself.",
            artifacts=(str(private),),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.exit_code == 2
    assert failure.value.diagnostic_class == "artifact_locator_ignored"
    assert _counts(workspace) == (0, 0)


def test_ignore_character_class_with_leading_bracket_stays_applicable(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: `[]]` is a class member, not an internal failure."""

    artifact = workspace / "]Vera Example board.md"
    artifact.write_text("Vera Example bracketed board.\n", encoding="utf-8")
    _set_ignore_paths(workspace, "[]]Vera Example board.md")

    with pytest.raises(InvalidInputError) as failure:
        capture_daily(
            workspace,
            raw_text="Vera Example captured a bracket-class ignored locator.",
            artifacts=(str(artifact),),
            clock=lambda: FIXED_NOW,
        )
    assert failure.value.exit_code == 2
    assert failure.value.diagnostic_class == "artifact_locator_ignored"


@pytest.mark.parametrize("boundary", ["capture", "prompt"])
def test_posix_named_ignore_class_applies_at_both_privacy_boundaries(
    workspace: Path,
    boundary: str,
) -> None:
    """§21.51 / §29.4: named classes bind acquisition and prompt re-checks."""

    artifact = workspace / "1Vera Example board.md"
    artifact.write_text("Vera Example numbered board.\n", encoding="utf-8")
    captured = None
    if boundary == "prompt":
        captured = capture_daily(
            workspace,
            raw_text="Vera Example captured provenance before a named-class rule.",
            artifacts=(str(artifact),),
            clock=lambda: FIXED_NOW,
            id_factory=_capture_ids(
                "log_vera_named_class",
                "evi_vera_named_class_manual",
                "evi_vera_named_class_artifact",
            ),
        )

    _set_ignore_paths(workspace, "[[:digit:]]Vera Example board.md")
    if boundary == "capture":
        with pytest.raises(InvalidInputError) as failure:
            capture_daily(
                workspace,
                raw_text="Vera Example captured a named-class ignored locator.",
                artifacts=(str(artifact),),
                clock=lambda: FIXED_NOW,
            )
        assert failure.value.exit_code == 2
        assert failure.value.diagnostic_class == "artifact_locator_ignored"
        assert _counts(workspace) == (0, 0)
        return

    assert captured is not None
    fake = FakeContractRunner([])
    with pytest.raises(LocatorReauthorizationFailedError) as failure:
        run_stage3(workspace, fake, TestIds(), log_id=captured.raw_log.id)
    assert failure.value.exit_code == 7
    assert failure.value.diagnostic_class == "locator_reauthorization_failed"
    assert fake.calls == []
    assert show_log(workspace, log_id=captured.raw_log.id).evidence_items == (
        captured.evidence_items
    )


def test_ignore_matcher_supports_every_git_posix_named_class() -> None:
    """§21.51 / §29.4: the local matcher carries Git's complete class set."""

    cases = {
        "alnum": ("A", "-"),
        "alpha": ("z", "7"),
        "blank": ("\t", "A"),
        "cntrl": ("\x01", " "),
        "digit": ("7", "A"),
        "graph": ("!", " "),
        "lower": ("z", "Z"),
        "print": (" ", "\x01"),
        "punct": ("!", "A"),
        "space": ("\t", "A"),
        "upper": ("Z", "z"),
        "xdigit": ("f", "g"),
    }
    for name, (matching, nonmatching) in cases.items():
        matcher, _anchored, _directory_only = _ignore_matcher(
            f"[[:{name}:]]"
        )
        assert matcher.fullmatch(matching) is not None
        assert matcher.fullmatch(nonmatching) is None

    for name in ("graph", "print", "punct"):
        matcher, _anchored, _directory_only = _ignore_matcher(
            f"[[:{name}:]]"
        )
        assert matcher.fullmatch("/") is None

    folded, _anchored, _directory_only = _ignore_matcher(
        "[[:upper:]]",
        folded=True,
    )
    assert folded.fullmatch("a") is not None

    combined, _anchored, _directory_only = _ignore_matcher(
        "[a-c[:digit:]x-z]"
    )
    assert all(
        combined.fullmatch(value) is not None for value in ("5", "b", "y")
    )
    assert combined.fullmatch("q") is None

    invalid, _anchored, _directory_only = _ignore_matcher(
        "[[:digit:][:spaci:]]"
    )
    assert invalid.fullmatch("1") is None

    negated, _anchored, _directory_only = _ignore_matcher("ab[!x]cd")
    assert negated.fullmatch("ab/cd") is None


def test_trailing_space_ignore_rule_applies_at_both_privacy_boundaries(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: unescaped trailing spaces do not weaken either gate."""

    private = workspace / "private"
    private.mkdir()
    artifact = private / "secret.txt"
    artifact.write_text("Vera Example trailing-space secret.\n", encoding="utf-8")
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured provenance before a trailing-space rule.",
        artifacts=(str(artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_trailing_space",
            "evi_vera_trailing_space_manual",
            "evi_vera_trailing_space_artifact",
        ),
    )

    _set_ignore_paths(workspace, "private/secret.txt ")
    config = load_workspace_config(workspace)
    with pytest.raises(LocatorReauthorizationFailedError) as prompt_failure:
        reauthorize_prompt_locators(
            {"path": captured.evidence_items[1].path},
            config=config,
        )
    assert prompt_failure.value.diagnostic_class == "locator_reauthorization_failed"

    with pytest.raises(InvalidInputError) as capture_failure:
        capture_daily(
            workspace,
            raw_text="Vera Example attempted capture after a trailing-space rule.",
            artifacts=(str(artifact),),
            clock=lambda: FIXED_NOW,
        )
    assert capture_failure.value.diagnostic_class == "artifact_locator_ignored"
    assert _counts(workspace) == (1, 2)

    directory, _anchored, directory_only = _ignore_matcher("private/ ")
    assert directory_only is True
    assert directory.fullmatch("private") is not None


def test_consecutive_ignore_separators_stay_nonmatching_at_both_boundaries(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: `//` never collapses into a broader privacy rule."""

    private = workspace / "private"
    private.mkdir()
    artifact = private / "secret.txt"
    artifact.write_text("Vera Example separator-safe artifact.\n", encoding="utf-8")
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured before a double-separator rule.",
        artifacts=(str(artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_double_separator_before",
            "evi_vera_double_separator_before_manual",
            "evi_vera_double_separator_before_artifact",
        ),
    )

    _set_ignore_paths(workspace, "private//secret.txt")
    config = load_workspace_config(workspace)
    reauthorize_prompt_locators(
        {"path": captured.evidence_items[1].path},
        config=config,
    )
    accepted = capture_daily(
        workspace,
        raw_text="Vera Example captured after a double-separator rule.",
        artifacts=(str(artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_double_separator_after",
            "evi_vera_double_separator_after_manual",
            "evi_vera_double_separator_after_artifact",
        ),
    )
    assert accepted.evidence_items[1].path == str(artifact.resolve())

    repeated_trailing, _anchored, directory_only = _ignore_matcher("private//")
    assert directory_only is True
    assert repeated_trailing.fullmatch("private") is None


def test_ignore_wildcards_compare_utf8_bytes_at_both_privacy_boundaries(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: one `?` consumes one UTF-8 byte at both gates."""

    artifact = workspace / "é.txt"
    artifact.write_text("Vera Example UTF-8 artifact.\n", encoding="utf-8")
    _set_ignore_paths(workspace, "?.txt")
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured through a one-byte wildcard.",
        artifacts=(str(artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_utf8_before",
            "evi_vera_utf8_before_manual",
            "evi_vera_utf8_before_artifact",
        ),
    )

    _set_ignore_paths(workspace, "??.txt")
    config = load_workspace_config(workspace)
    with pytest.raises(LocatorReauthorizationFailedError) as prompt_failure:
        reauthorize_prompt_locators(
            {"path": captured.evidence_items[1].path},
            config=config,
        )
    assert prompt_failure.value.diagnostic_class == "locator_reauthorization_failed"

    with pytest.raises(InvalidInputError) as capture_failure:
        capture_daily(
            workspace,
            raw_text="Vera Example attempted capture through two byte wildcards.",
            artifacts=(str(artifact),),
            clock=lambda: FIXED_NOW,
        )
    assert capture_failure.value.diagnostic_class == "artifact_locator_ignored"
    assert _counts(workspace) == (1, 2)


def test_trailing_separator_anchors_both_privacy_boundaries_to_root(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: `private/` never binds a nested same-name directory."""

    root_private = workspace / "private"
    root_private.mkdir()
    root_artifact = root_private / "Vera Example root.md"
    root_artifact.write_text("Vera Example root-private artifact.\n", encoding="utf-8")
    nested_private = workspace / "nested" / "private"
    nested_private.mkdir(parents=True)
    nested_artifact = nested_private / "Vera Example nested.md"
    nested_artifact.write_text(
        "Vera Example nested-private artifact.\n",
        encoding="utf-8",
    )
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured root provenance before an anchored rule.",
        artifacts=(str(root_artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_trailing_anchor_before",
            "evi_vera_trailing_anchor_before_manual",
            "evi_vera_trailing_anchor_before_artifact",
        ),
    )

    _set_ignore_paths(workspace, "private/")
    config = load_workspace_config(workspace)
    with pytest.raises(LocatorReauthorizationFailedError):
        reauthorize_prompt_locators(
            {"path": captured.evidence_items[1].path},
            config=config,
        )
    reauthorize_prompt_locators(
        {"path": str(nested_artifact.resolve())},
        config=config,
    )
    accepted = capture_daily(
        workspace,
        raw_text="Vera Example captured a nested same-name directory.",
        artifacts=(str(nested_artifact),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_trailing_anchor_nested",
            "evi_vera_trailing_anchor_nested_manual",
            "evi_vera_trailing_anchor_nested_artifact",
        ),
    )
    assert accepted.evidence_items[1].path == str(nested_artifact.resolve())


def test_reversed_ignore_range_keeps_git_semantics_at_both_boundaries(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: `[z-a]` matches only its tested start, never crashes."""

    matched = workspace / "zVera Example.md"
    matched.write_text("Vera Example reversed-range start.\n", encoding="utf-8")
    unmatched = workspace / "aVera Example.md"
    unmatched.write_text("Vera Example reversed-range end.\n", encoding="utf-8")
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured before a reversed-range rule.",
        artifacts=(str(matched),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_reversed_range_before",
            "evi_vera_reversed_range_before_manual",
            "evi_vera_reversed_range_before_artifact",
        ),
    )

    _set_ignore_paths(workspace, "[z-a]Vera Example.md")
    config = load_workspace_config(workspace)
    with pytest.raises(LocatorReauthorizationFailedError):
        reauthorize_prompt_locators(
            {"path": captured.evidence_items[1].path},
            config=config,
        )
    with pytest.raises(InvalidInputError) as matched_failure:
        capture_daily(
            workspace,
            raw_text="Vera Example attempted the reversed-range start.",
            artifacts=(str(matched),),
            clock=lambda: FIXED_NOW,
        )
    assert matched_failure.value.diagnostic_class == "artifact_locator_ignored"

    accepted = capture_daily(
        workspace,
        raw_text="Vera Example captured the reversed-range endpoint.",
        artifacts=(str(unmatched),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_reversed_range_after",
            "evi_vera_reversed_range_after_manual",
            "evi_vera_reversed_range_after_artifact",
        ),
    )
    assert accepted.evidence_items[1].path == str(unmatched.resolve())


def test_unanchored_ignore_uses_absolute_path_only_outside_workspace(
    workspace: Path,
) -> None:
    """§21.51 / §29.4: host ancestors never become in-root ignore targets."""

    ancestor_name = workspace.resolve().parts[1]
    matched_dir = workspace / ancestor_name
    matched_dir.mkdir()
    matched = matched_dir / "Vera Example matched.md"
    matched.write_text("Vera Example in-root component.\n", encoding="utf-8")
    allowed_dir = workspace / "allowed"
    allowed_dir.mkdir()
    allowed = allowed_dir / "Vera Example allowed.md"
    allowed.write_text("Vera Example host-ancestor-safe artifact.\n", encoding="utf-8")
    captured = capture_daily(
        workspace,
        raw_text="Vera Example captured before an unanchored component rule.",
        artifacts=(str(matched),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_unanchored_before",
            "evi_vera_unanchored_before_manual",
            "evi_vera_unanchored_before_artifact",
        ),
    )

    _set_ignore_paths(workspace, ancestor_name)
    config = load_workspace_config(workspace)
    with pytest.raises(LocatorReauthorizationFailedError):
        reauthorize_prompt_locators(
            {"path": captured.evidence_items[1].path},
            config=config,
        )
    reauthorize_prompt_locators(
        {"path": str(allowed.resolve())},
        config=config,
    )
    accepted = capture_daily(
        workspace,
        raw_text="Vera Example ignored the host ancestor during capture.",
        artifacts=(str(allowed),),
        clock=lambda: FIXED_NOW,
        id_factory=_capture_ids(
            "log_vera_unanchored_after",
            "evi_vera_unanchored_after_manual",
            "evi_vera_unanchored_after_artifact",
        ),
    )
    assert accepted.evidence_items[1].path == str(allowed.resolve())


def test_repeated_recursive_segments_collapse_to_one_matcher() -> None:
    """§21.51 / §29.4: adjacent `**` segments cannot multiply the match cost."""

    matcher, _anchored, _directory_only = _ignore_matcher("a/" + "**/" * 14 + "z")
    assert matcher.pattern.count("(?:[^/]+/)*") == 1

    target = "a/" + "/".join(f"component{index}" for index in range(14)) + "/no.md"
    started = time.perf_counter()
    assert matcher.fullmatch(target) is None
    assert time.perf_counter() - started < 1.0
