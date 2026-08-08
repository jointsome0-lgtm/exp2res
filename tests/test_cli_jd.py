"""§14.10/§14.15 job-description addition, discovery, and owner deletion."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.job_descriptions as jd_service
import exp2res.exports.managed as managed_outputs
from exp2res.cli import app
from exp2res.storage.workspace import read_database, writer_database

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage8_jd_parsing import VACANCY, ParserIds, parser_response


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(workspace: Path, arguments: list[str]):
    result = runner.invoke(
        app, ["--json", "--workspace", str(workspace), *arguments]
    )
    return result, json.loads(result.stdout)


def install_fake_execution(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeContractRunner,
    ids: ParserIds | None = None,
) -> None:
    monkeypatch.setattr(
        jd_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    # One counter per workspace: a second command in the same workspace must
    # not re-offer a committed run or entity ID.
    monkeypatch.setattr(jd_service, "new_id", ids or ParserIds())


def vacancy_file(tmp_path: Path, text: str = VACANCY) -> Path:
    path = tmp_path / "agent_engineer.md"
    path.write_text(text, encoding="utf-8")
    return path


def add_job_description(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str = VACANCY,
    warnings: list[dict[str, str]] | None = None,
    ids: ParserIds | None = None,
):
    install_fake_execution(
        monkeypatch,
        FakeContractRunner([parser_response(warnings=warnings)]),
        ids,
    )
    return invoke_json(
        workspace, ["jd", "add", str(vacancy_file(tmp_path, text))]
    )


def test_add_reports_the_created_record_without_exposing_the_parse(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10/§14.14 rule 5: standard envelope fields carry the whole result."""

    result, envelope = add_job_description(
        workspace,
        tmp_path,
        monkeypatch,
        warnings=[
            {
                "type": "vera_note",
                "message": "The supplied vacancy names no seniority level.",
            }
        ],
    )

    assert result.exit_code == 0
    assert envelope["command"] == "jd add"
    assert envelope["status"] == "ok"
    assert envelope["result"] is None
    created = envelope["affected_ids"]["created"]
    assert [group["entity_type"] for group in created] == ["job_description"]
    job_description_id = created[0]["ids"][0]
    assert len(envelope["run_ids"]) == 1
    assert envelope["warnings"][0]["type"] == "vera_note"
    # §14.14 rule 7: the parse and the vacancy text never reach the envelope.
    serialized = json.dumps(envelope)
    assert VACANCY not in serialized
    assert "required_skill" not in serialized

    with read_database(workspace) as connection:
        stored = connection.execute(
            "SELECT raw_text, parsed_json FROM job_descriptions WHERE id = ?",
            (job_description_id,),
        ).fetchone()
    assert stored["raw_text"] == VACANCY
    assert json.loads(stored["parsed_json"])["requirements"]


def test_add_fails_closed_on_a_denied_source_path(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§29.4 rule 4: job-description addition passes the acquisition gate."""

    denied = tmp_path / "secrets"
    denied.mkdir()
    vacancy = denied / "agent_engineer.md"
    vacancy.write_text(VACANCY, encoding="utf-8")
    fake = FakeContractRunner([])
    install_fake_execution(monkeypatch, fake)

    result, envelope = invoke_json(workspace, ["jd", "add", str(vacancy)])

    assert result.exit_code == 2
    assert envelope["status"] == "failed"
    # Acquisition fails before the adapter is reached, so neither the vacancy
    # bytes nor its locator can have been serialized into a prompt.
    assert fake.calls == []
    assert envelope["run_ids"] == []
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_list_is_the_raw_text_free_discovery_projection(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.15: `jd list` exposes neither `raw_text` nor `parsed`."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]

    result, envelope = invoke_json(workspace, ["jd", "list"])

    assert result.exit_code == 0
    assert envelope["command"] == "jd list"
    listed = envelope["result"]["job_descriptions"]
    assert len(listed) == 1
    assert sorted(listed[0]) == ["company", "created_at", "id", "title"]
    assert listed[0]["id"] == job_description_id
    assert listed[0]["title"] == "Agent Engineer"
    assert listed[0]["company"] == "Vera Example Systems"
    assert envelope["run_ids"] == []


def test_list_of_an_empty_workspace_is_an_ordinary_success(
    workspace: Path,
) -> None:
    result, envelope = invoke_json(workspace, ["jd", "list"])

    assert result.exit_code == 0
    assert envelope["result"] == {"job_descriptions": []}


def test_delete_fails_closed_without_consent_and_keeps_the_record(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 3: an irreversible action needs `--yes` when non-interactive."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]

    result, envelope = invoke_json(
        workspace, ["--no-input", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 2
    assert envelope["status"] == "failed"
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 1


def test_delete_purges_the_record_and_redacts_retained_call_hashes(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 10: the point deletion reports its closed §14.14 result."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE input_hash IS NOT NULL"
        ).fetchone()[0] == 1

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 0
    assert envelope["command"] == "jd delete"
    selected = envelope["result"]["selected_job_description"]
    assert selected["id"] == job_description_id
    assert sorted(selected) == ["company", "created_at", "id", "title"]
    # No branch table exists yet, so no branch can be captured (see the
    # comment in exp2res/services/job_descriptions.py).
    assert envelope["result"]["purged_branches"] == []
    assert envelope["residual_paths"] == []
    assert envelope["affected_ids"]["deleted"] == [
        {"entity_type": "job_description", "ids": [job_description_id]}
    ]
    assert VACANCY not in json.dumps(envelope)

    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0
        # §13.13 rule 5's global redaction: a hash of guessable vacancy text
        # would survive the deletion as an oracle.
        assert connection.execute(
            "SELECT COUNT(*) FROM llm_calls "
            "WHERE input_hash IS NOT NULL OR output_hash IS NOT NULL"
        ).fetchone()[0] == 0
        # §24.47: exactly one content-free §13.13 orchestration run, no
        # provider call of its own, and no recompute.
        orchestration = connection.execute(
            "SELECT id, status, provider, model, prompt_policy_hash, "
            "output_ids_json FROM processing_runs WHERE stage = '13.13'"
        ).fetchall()
        assert len(orchestration) == 1
        assert tuple(orchestration[0])[1:] == (
            "completed",
            None,
            None,
            None,
            "[]",
        )
        assert envelope["run_ids"] == [orchestration[0]["id"]]
        assert connection.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE stage NOT IN "
            "('13.8', '13.13')"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE run_id = ?",
            (orchestration[0]["id"],),
        ).fetchone()[0] == 0


def test_delete_reports_an_absent_selector_without_touching_the_store(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _result, added = add_job_description(workspace, tmp_path, monkeypatch)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", "job_description_absent"]
    )

    assert result.exit_code == 2
    assert envelope["status"] == "failed"
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 1


def test_a_residual_managed_path_keeps_the_deletion_committed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: exit 8, `deletion_incomplete`, database deletion durable."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    # A directory under the backup root is not a regular file, so removal
    # cannot complete and the path is reported instead of dropped.
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    residual = backup_root / "candidate"
    residual.mkdir()

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["status"] == "failed"
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(residual.absolute())]
    assert envelope["result"]["removed_managed_paths"] == []
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_a_removed_managed_backup_is_reported_by_canonical_path(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: `removed_managed_paths` names what deletion removed."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / "exp2res-9.sqlite"
    backup.write_bytes(b"Vera Example migration backup")

    _result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert envelope["residual_paths"] == []
    assert envelope["result"]["removed_managed_paths"] == [
        str(backup.absolute())
    ]
    assert not backup.exists()


def test_a_managed_symlink_is_reported_without_traversal(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: the link's target survives byte-for-byte."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    target = tmp_path / "outside.txt"
    target.write_bytes(b"Vera Example untouched target")
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    link = backup_root / "linked.sqlite"
    os.symlink(target, link)

    _result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(link.absolute())]
    assert target.read_bytes() == b"Vera Example untouched target"
    assert link.is_symlink()


def test_a_persisted_job_description_is_never_updated_by_a_rerun(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11.11: two additions are two records, never a rewrite of one."""

    ids = ParserIds()
    _first, first_envelope = add_job_description(
        workspace, tmp_path, monkeypatch, ids=ids
    )
    _second, second_envelope = add_job_description(
        workspace, tmp_path, monkeypatch, ids=ids
    )
    first_id = first_envelope["affected_ids"]["created"][0]["ids"][0]
    second_id = second_envelope["affected_ids"]["created"][0]["ids"][0]

    assert first_id != second_id
    _result, listed = invoke_json(workspace, ["jd", "list"])
    assert [item["id"] for item in listed["result"]["job_descriptions"]] == sorted(
        (first_id, second_id), key=lambda value: value.encode("utf-8")
    )
    with writer_database(workspace) as connection:
        with pytest.raises(Exception, match="job_description_immutable"):
            connection.execute(
                "UPDATE job_descriptions SET company = 'Rewritten' WHERE id = ?",
                (first_id,),
            )


def test_a_symlinked_backup_root_is_never_traversed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #252 review: enumeration shares removal's no-follow boundary."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "unrelated.sqlite").write_bytes(b"Vera Example untouched backup")
    backup_root = workspace / ".exp2res" / "backup"
    os.symlink(outside, backup_root)

    _result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(backup_root.absolute())]
    # The external directory is reported by its own name only, never walked.
    assert envelope["result"]["removed_managed_paths"] == []
    assert (outside / "unrelated.sqlite").read_bytes() == (
        b"Vera Example untouched backup"
    )
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_an_unknown_selector_never_reaches_the_writer_preamble(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #252 review: an invalid selector mutates no managed state."""

    add_job_description(workspace, tmp_path, monkeypatch)
    taken: list[Path] = []
    real_writer = jd_service.writer_database

    def recording(target: Path, **keywords):
        taken.append(target)
        return real_writer(target, **keywords)

    monkeypatch.setattr(jd_service, "writer_database", recording)
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / "exp2res-10.sqlite"
    backup.write_bytes(b"Vera Example migration backup")

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", "job_description_absent"]
    )

    assert result.exit_code == 2
    assert envelope["status"] == "failed"
    assert taken == []
    assert backup.read_bytes() == b"Vera Example migration backup"


def test_an_interrupt_as_commit_returns_still_reports_the_deletion(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the durable deletion survives its cancelled envelope."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]

    class InterruptOnCommitReturn:
        """Commit for real, then raise as the C call returns to Python."""

        def __init__(self, connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def commit(self) -> None:
            self._connection.commit()
            raise KeyboardInterrupt()

    real_writer = jd_service.writer_database

    @contextmanager
    def wrapped(target: Path, **keywords):
        with real_writer(target, **keywords) as connection:
            yield InterruptOnCommitReturn(connection)

    monkeypatch.setattr(jd_service, "writer_database", wrapped)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["result"]["selected_job_description"]["id"] == (
        job_description_id
    )
    assert envelope["affected_ids"]["deleted"] == [
        {"entity_type": "job_description", "ids": [job_description_id]}
    ]
    assert len(envelope["run_ids"]) == 1
    database = workspace / ".exp2res" / "exp2res.sqlite"
    assert str(database.with_name(database.name + "-wal")) in (
        envelope["residual_paths"]
    )
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_add_rejects_the_stdin_spelling(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.10 declares a positional path; stdin belongs to §14.2's `--file -`."""

    fake = FakeContractRunner([])
    install_fake_execution(monkeypatch, fake)

    result, envelope = invoke_json(workspace, ["jd", "add", "-"])

    assert result.exit_code == 2
    assert envelope["status"] == "failed"
    assert fake.calls == []
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_a_preamble_residual_is_a_deletion_failure_not_an_output_failure(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.15: every `jd delete` residual reports `deletion_incomplete`."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    stranded = workspace / "out" / "assessment"
    stranded.mkdir(mode=0o700, parents=True, exist_ok=True)
    monkeypatch.setattr(
        managed_outputs,
        "reconcile_managed_outputs",
        lambda _workspace: (str(stranded),),
    )

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(stranded)]
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0
