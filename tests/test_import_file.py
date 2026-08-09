"""Offline §14.5 `import file` coverage: the non-§19 design-document form."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from exp2res.cli import app
from exp2res.domain.models import RAW_TEXT_LIMIT
from exp2res.errors import (
    BlankProjectLabelError,
    ForbiddenPathError,
    ImportDocumentInvalidError,
    InvalidInputError,
)
from exp2res.services.imports import import_design_document

from conftest import FIXED_NOW
from test_imports_phase5 import evidence_rows, raw_rows


pytestmark = [pytest.mark.unit, pytest.mark.lifecycle]
runner = CliRunner()

DOCUMENT = "# Ingress rollout design\n\nDrafted by Vera Example.\n"


def write_document(directory: Path, name: str = "design.md") -> Path:
    path = directory / name
    path.write_text(DOCUMENT, encoding="utf-8")
    return path


def run_import(workspace: Path, source_path: str, **keywords):
    return import_design_document(
        workspace,
        source_path=source_path,
        clock=lambda: FIXED_NOW,
        **keywords,
    )


def test_the_document_lands_as_one_typed_stage_one_pair(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.5's row: `design_doc` text plus its `design_doc` evidence."""
    document = write_document(tmp_path)

    bundle = run_import(workspace, str(document), project="Exp2Res")

    assert bundle.raw_log.entry_type == "design_doc"
    assert bundle.raw_log.source_type == "imported_artifact"
    assert bundle.raw_log.raw_text == DOCUMENT
    assert bundle.raw_log.project == "Exp2Res"
    # §14.5 says this form is not a §19 record, so none of §19.4 rule 2's
    # reserved identity keys may appear on it.
    assert bundle.raw_log.metadata == {}
    assert len(bundle.evidence_items) == 1
    assert bundle.evidence_items[0].strength == "design_doc"
    assert bundle.evidence_items[0].raw_log_id == bundle.raw_log.id

    stored = raw_rows(workspace)
    assert len(stored) == 1
    assert stored[0]["entry_type"] == "design_doc"
    assert len(evidence_rows(workspace)) == 1


def test_no_occurrence_is_invented_from_the_file(
    workspace: Path, tmp_path: Path
) -> None:
    """§13.1 rule 3 with §5: unknown is stated, not guessed from mtime."""
    bundle = run_import(workspace, str(write_document(tmp_path)))

    assert bundle.raw_log.occurred.precision == "unknown"
    assert bundle.raw_log.occurred.confidence == "unknown"
    assert bundle.raw_log.occurred.start is None
    assert bundle.raw_log.occurred.end is None


def test_the_project_label_is_optional_and_never_canonically_blank(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.5 shows `--project`; §12 rule 14 bans a blank non-null label."""
    document = write_document(tmp_path)

    assert run_import(workspace, str(document)).raw_log.project is None

    with pytest.raises(BlankProjectLabelError):
        run_import(workspace, str(document), project="   ")
    assert len(raw_rows(workspace)) == 1


@pytest.mark.parametrize("spelling", ["symlink", "dot-dot"])
def test_both_locator_spellings_persist_the_one_canonical_path(
    workspace: Path, tmp_path: Path, spelling: str
) -> None:
    """§14.5 with §14.2: the record names the object, not the spelling."""
    documents = tmp_path / "documents"
    documents.mkdir()
    document = write_document(documents)
    if spelling == "symlink":
        supplied = tmp_path / "link.md"
        supplied.symlink_to(document)
    else:
        supplied = documents / ".." / "documents" / "design.md"

    bundle = run_import(workspace, str(supplied))

    canonical = document.resolve().as_posix()
    assert bundle.raw_log.external_ref == canonical
    assert bundle.evidence_items[0].path == canonical
    assert str(supplied) != canonical


def test_an_oversize_document_fails_closed_rather_than_truncating(
    workspace: Path, tmp_path: Path
) -> None:
    """§11's `raw_text` bound is a limit on acceptance, not on storage."""
    oversize = tmp_path / "oversize.md"
    oversize.write_bytes(b"Vera Example\n" + b"x" * RAW_TEXT_LIMIT)

    with pytest.raises(InvalidInputError) as failure:
        run_import(workspace, str(oversize))

    assert failure.value.diagnostic_class == "input_too_large"
    assert raw_rows(workspace) == []


def test_a_non_utf8_document_is_a_typed_rejection(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.5 rejects other local-file categories rather than guessing."""
    binary = tmp_path / "design.bin"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")

    with pytest.raises(InvalidInputError) as failure:
        run_import(workspace, str(binary))

    assert failure.value.diagnostic_class == "input_not_utf8"
    assert raw_rows(workspace) == []


def test_an_empty_document_establishes_no_record(
    workspace: Path, tmp_path: Path
) -> None:
    """§13.1 rule 1: a record's raw text is never empty."""
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(ImportDocumentInvalidError) as failure:
        run_import(workspace, str(empty))

    assert failure.value.diagnostic_class == "import_document_invalid"
    assert raw_rows(workspace) == []


def test_denied_ignored_and_non_file_selections_are_refused(
    workspace: Path, tmp_path: Path
) -> None:
    """§29.4: the deny set, workspace privacy rules, and directories."""
    denied = tmp_path / ".env"
    denied.write_text("Vera Example synthetic secret stand-in\n", encoding="utf-8")
    with pytest.raises(ForbiddenPathError):
        run_import(workspace, str(denied))

    directory = tmp_path / "documents"
    directory.mkdir()
    with pytest.raises(ForbiddenPathError):
        run_import(workspace, str(directory))

    with pytest.raises(InvalidInputError):
        run_import(workspace, str(tmp_path / "absent.md"))

    assert raw_rows(workspace) == []


def test_no_managed_source_copy_is_created(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.5: the document stays where the owner put it."""
    document = write_document(tmp_path)
    before = sorted(
        path.relative_to(workspace).as_posix()
        for path in (workspace / ".exp2res").rglob("*")
    )

    run_import(workspace, str(document))

    after = sorted(
        path.relative_to(workspace).as_posix()
        for path in (workspace / ".exp2res").rglob("*")
    )
    assert [name for name in after if name not in before] == []
    assert document.read_text(encoding="utf-8") == DOCUMENT


def test_the_command_reports_a_null_result_with_the_created_pair(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 5 lists only the §19 forms, so this one is `result = null`."""
    document = write_document(tmp_path)

    result = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "import",
            "file",
            str(document),
            "--project",
            "Exp2Res",
        ],
    )
    envelope = json.loads(result.stdout)

    assert result.exit_code == 0
    assert envelope["command"] == "import file"
    assert envelope["status"] == "ok"
    assert envelope["diagnostic_class"] is None
    assert envelope["result"] is None
    created = {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }
    assert set(created) == {"raw_log", "evidence_item"}
    assert len(created["raw_log"]) == 1
    assert len(created["evidence_item"]) == 1
    assert created["raw_log"][0] == raw_rows(workspace)[0]["id"]


def test_the_command_reports_a_refused_document_as_a_typed_failure(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 4: a rejection is exit class 2, never a silent skip."""
    denied = tmp_path / ".env"
    denied.write_text("Vera Example synthetic secret stand-in\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), "import", "file", str(denied)],
    )
    envelope = json.loads(result.stdout)

    assert result.exit_code == 2
    assert envelope["command"] == "import file"
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "forbidden_path"
    assert envelope["result"] is None
    assert raw_rows(workspace) == []
