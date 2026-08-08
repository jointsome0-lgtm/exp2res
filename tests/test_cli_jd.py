"""§14.10/§14.15 job-description addition, discovery, and owner deletion."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.services.job_descriptions as jd_service
import exp2res.services.privacy as privacy_service
import exp2res.exports.managed as managed_outputs
from exp2res.cli import app
from exp2res.errors import LLMInvocationError
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


def test_control_bearing_vacancy_text_is_rejected_before_the_writer(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 4: boundary text fails in class 2, not as an internal error."""

    fake = FakeContractRunner([])
    install_fake_execution(monkeypatch, fake)

    result, envelope = invoke_json(
        workspace, ["jd", "add", str(vacancy_file(tmp_path, "Agent engineer\x07"))]
    )

    assert result.exit_code == 2
    assert envelope["status"] == "failed"
    assert fake.calls == []
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM processing_runs"
        ).fetchone()[0] == 0


def test_an_interrupt_as_the_parse_commits_reports_the_created_record(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: a durable Stage 8 commit survives its cancelled envelope."""

    import exp2res.pipeline.stage8 as stage8

    class InterruptOnCommitReturn:
        """Interrupt once, as the business commit returns to Python."""

        def __init__(self, connection) -> None:
            self._connection = connection
            self._fired = False

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def commit(self) -> None:
            self._connection.commit()
            # A real signal fires once, and only the business commit leaves
            # something durable to report: the run-row commit precedes it.
            if self._fired:
                return
            if self._connection.execute(
                "SELECT COUNT(*) FROM job_descriptions"
            ).fetchone()[0]:
                self._fired = True
                raise KeyboardInterrupt()

    real_writer = stage8.writer_database

    @contextmanager
    def wrapped(target: Path, **keywords):
        with real_writer(target, **keywords) as connection:
            yield InterruptOnCommitReturn(connection)

    monkeypatch.setattr(stage8, "writer_database", wrapped)

    result, envelope = add_job_description(workspace, tmp_path, monkeypatch)

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = envelope["affected_ids"]["created"]
    assert [group["entity_type"] for group in created] == ["job_description"]
    with read_database(workspace) as connection:
        stored = connection.execute("SELECT id FROM job_descriptions").fetchall()
    assert [row[0] for row in stored] == created[0]["ids"]


def test_an_interrupt_after_the_checkpoint_still_reports_the_deletion(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the committed outcome spans the whole service teardown."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]

    def interrupt(*_arguments: object, **_keywords: object):
        raise KeyboardInterrupt()

    monkeypatch.setattr(jd_service, "_delete_checkpoint_residuals", interrupt)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["affected_ids"]["deleted"] == [
        {"entity_type": "job_description", "ids": [job_description_id]}
    ]
    assert envelope["result"]["selected_job_description"]["id"] == (
        job_description_id
    )
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_an_undecodable_backup_name_is_reported_in_backslash_form(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 5: an unencodable removed path never breaks the envelope."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    undecodable = os.path.join(os.fsencode(str(backup_root)), b"pre-\xff.sqlite")
    with open(undecodable, "wb") as handle:
        handle.write(b"")

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 0
    assert envelope["result"]["removed_managed_paths"] == [
        str(backup_root / "pre-\\xff.sqlite")
    ]
    assert not os.path.exists(undecodable)


def test_an_interrupt_during_stage_8_teardown_still_reports_the_creation(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the durable parse survives an interrupt after it returns."""

    import exp2res.pipeline.stage8 as stage8

    real_parse = stage8.run_job_description_parse

    def interrupt_on_return(*arguments: object, **keywords: object):
        real_parse(*arguments, **keywords)
        # The whole stage, including its writer teardown, has completed.
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        jd_service, "run_job_description_parse", interrupt_on_return
    )

    result, envelope = add_job_description(workspace, tmp_path, monkeypatch)

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = envelope["affected_ids"]["created"]
    assert [group["entity_type"] for group in created] == ["job_description"]
    assert len(envelope["run_ids"]) == 1
    with read_database(workspace) as connection:
        stored = connection.execute("SELECT id FROM job_descriptions").fetchall()
    assert [row[0] for row in stored] == created[0]["ids"]


def test_a_colliding_candidate_id_is_never_reported_as_this_run_s_creation(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a completed run's own output IDs name what this command created."""

    ids = ParserIds()
    _result, added = add_job_description(workspace, tmp_path, monkeypatch, ids=ids)
    retained = added["affected_ids"]["created"][0]["ids"][0]

    def failing_parse(*_arguments: object, **_keywords: object):
        # The allocator offered the retained ID, rejected it, and the run then
        # failed: the pre-existing row is not this invocation's creation.
        jd_service.new_id("job_description")
        raise LLMInvocationError("business_commit_failed")

    install_fake_execution(monkeypatch, FakeContractRunner([]), ids)
    monkeypatch.setattr(jd_service, "new_id", lambda _kind: retained)
    monkeypatch.setattr(jd_service, "run_job_description_parse", failing_parse)

    result, envelope = invoke_json(
        workspace, ["jd", "add", str(vacancy_file(tmp_path))]
    )

    assert result.exit_code != 0
    assert envelope["status"] == "failed"
    assert envelope["affected_ids"]["created"] == []
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 1


def test_a_backup_recreated_during_the_purge_is_reported_as_residual(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: completeness is proven by re-enumeration, not by one pass."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = backup_root / "pre-migration.sqlite"
    backup.write_bytes(b"")

    real_unlink = os.unlink

    def racing_unlink(name, *, dir_fd=None):
        real_unlink(name, dir_fd=dir_fd)
        # A concurrent writer recreates the name the pass just removed.
        backup.write_bytes(b"")

    monkeypatch.setattr(os, "unlink", racing_unlink)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(backup.absolute())]
    assert envelope["result"]["removed_managed_paths"] == []
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_a_cancelled_add_still_reports_its_parser_warnings(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the advisories describing a durable parse survive."""

    import exp2res.pipeline.stage8 as stage8

    class InterruptOnCommitReturn:
        """Interrupt once, as the business commit returns to Python."""

        def __init__(self, connection) -> None:
            self._connection = connection
            self._fired = False

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def commit(self) -> None:
            self._connection.commit()
            if self._fired:
                return
            if self._connection.execute(
                "SELECT COUNT(*) FROM job_descriptions"
            ).fetchone()[0]:
                self._fired = True
                raise KeyboardInterrupt()

    real_writer = stage8.writer_database

    @contextmanager
    def wrapped(target: Path, **keywords):
        with real_writer(target, **keywords) as connection:
            yield InterruptOnCommitReturn(connection)

    monkeypatch.setattr(stage8, "writer_database", wrapped)

    result, envelope = add_job_description(
        workspace,
        tmp_path,
        monkeypatch,
        warnings=[{"type": "vera_note", "message": "Vera Example advisory"}],
    )

    assert result.exit_code == 9
    assert [warning["type"] for warning in envelope["warnings"]] == ["vera_note"]
    assert envelope["affected_ids"]["created"][0]["entity_type"] == (
        "job_description"
    )


def test_human_mode_delete_names_every_removed_managed_path(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.15 reports the same removals without `--json`."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = backup_root / "pre-migration.sqlite"
    backup.write_bytes(b"")

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "--yes",
            "jd",
            "delete",
            "--jd",
            job_description_id,
        ],
    )

    assert result.exit_code == 0
    assert f"Removed managed path: {backup.absolute()}" in result.stdout


def test_two_backup_names_that_differ_only_in_escaping_stay_distinct(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.15 reports each removed path by identity, so rendering is injective."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    undecodable = os.path.join(os.fsencode(str(backup_root)), b"pre-\xff.sqlite")
    with open(undecodable, "wb") as handle:
        handle.write(b"")
    literal = backup_root / "pre-\\xff.sqlite"
    literal.write_bytes(b"")

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 0
    removed = envelope["result"]["removed_managed_paths"]
    assert len(set(removed)) == 2
    assert str(backup_root / "pre-\\\\xff.sqlite") in removed
    assert str(backup_root / "pre-\\xff.sqlite") in removed
    assert not os.path.exists(undecodable)
    assert not literal.exists()


def test_control_bytes_in_a_backup_name_are_escaped_in_both_modes(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legal newline in a managed name never fabricates a reported line."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    (backup_root / "pre\nmigration.sqlite").write_bytes(b"")

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 0
    assert envelope["result"]["removed_managed_paths"] == [
        str(backup_root / "pre") + "\\u000amigration.sqlite"
    ]


def test_human_listing_keeps_one_job_description_on_one_line(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16.13 permits a newline in parsed text; the listing stays unambiguous."""

    add_job_description(
        workspace,
        tmp_path,
        monkeypatch,
        text=VACANCY.replace(
            "Agent Engineer", "Agent Engineer\njd_spoofed\t2026-01-01T00:00:00+00:00"
        ),
    )

    result = runner.invoke(app, ["--workspace", str(workspace), "jd", "list"])

    assert result.exit_code == 0
    assert len(result.stdout.strip().splitlines()) == 1


def test_a_backup_directory_flush_failure_is_reported_as_residual(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: an unproven durable removal is residual, not success."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    (backup_root / "pre-migration.sqlite").write_bytes(b"")

    def failing_fsync(_descriptor: int) -> None:
        raise OSError("flush refused")

    monkeypatch.setattr(privacy_service.os, "fsync", failing_fsync)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(backup_root.absolute())]


def test_a_backup_replaced_by_a_fifo_is_skipped_without_blocking(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed entry is reported as residual, never waited on."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = backup_root / "pre-migration.sqlite"
    backup.write_bytes(b"")
    real_stat = os.stat

    def swapping_stat(path, *args, **keywords):
        recorded = real_stat(path, *args, **keywords)
        if path == backup.name and backup.is_file():
            # The entry becomes a FIFO between the scan and the open.
            backup.unlink()
            os.mkfifo(backup, 0o600)
        return recorded

    monkeypatch.setattr(privacy_service.os, "stat", swapping_stat)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["residual_paths"] == [str(backup.absolute())]
    assert envelope["result"]["removed_managed_paths"] == []


def test_a_cancelled_delete_reports_its_committed_effect_in_human_mode(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the committed lifecycle effect is reported in both modes."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]

    def interrupt(*_arguments: object, **_keywords: object):
        raise KeyboardInterrupt()

    monkeypatch.setattr(jd_service, "_delete_checkpoint_residuals", interrupt)

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "--yes",
            "jd",
            "delete",
            "--jd",
            job_description_id,
        ],
    )

    assert result.exit_code == 9
    assert f"Deleted job description {job_description_id}" in result.stdout


def test_a_real_c1_character_and_an_undecodable_byte_stay_distinct(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.15 reports each removed path by identity across both escape forms."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    literal = "pre\u0085.sqlite"
    (backup_root / literal).write_bytes(b"")
    undecodable = os.path.join(os.fsencode(str(backup_root)), b"pre\x85.sqlite")
    with open(undecodable, "wb") as handle:
        handle.write(b"")

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 0
    removed = envelope["result"]["removed_managed_paths"]
    assert len(set(removed)) == 2
    assert str(backup_root / "pre") + "\\u0085.sqlite" in removed
    assert str(backup_root / "pre") + "\\x85.sqlite" in removed


def test_a_colliding_orchestration_run_id_never_blocks_the_deletion(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: telemetry allocation never leaves the vacancy in place."""

    ids = ParserIds()
    _result, added = add_job_description(workspace, tmp_path, monkeypatch, ids=ids)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    with read_database(workspace) as connection:
        retained = connection.execute("SELECT id FROM processing_runs").fetchone()[0]

    offered = [retained, retained, "run_vera_fresh"]

    def colliding_factory(kind: str) -> str:
        assert kind == "run"
        return offered.pop(0)

    monkeypatch.setattr(jd_service, "new_id", colliding_factory)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 0
    assert envelope["run_ids"] == ["run_vera_fresh"]
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


def test_a_backup_root_appearing_after_its_absence_is_reported(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store created between the probe and the commit is residual."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    assert not backup_root.exists()
    real_open = privacy_service.os.open

    def creating_open(path, flags, **keywords):
        if path == "backup" and not backup_root.exists():
            try:
                return real_open(path, flags, **keywords)
            except FileNotFoundError:
                # A concurrent process installs the store right after the probe.
                backup_root.mkdir(mode=0o700)
                raise

        return real_open(path, flags, **keywords)

    monkeypatch.setattr(privacy_service.os, "open", creating_open)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(backup_root.absolute())]


def test_an_unreadable_backup_entry_is_never_read_as_absence(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: a failed recheck is not evidence the store is gone."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    assert not backup_root.exists()
    real_stat = privacy_service.os.stat

    def refusing_stat(path, **keywords):
        if path == "backup":
            raise PermissionError(13, "Permission denied")

        return real_stat(path, **keywords)

    monkeypatch.setattr(privacy_service.os, "stat", refusing_stat)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(backup_root.absolute())]


def test_a_cleanup_only_cancellation_still_reports_its_removals(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: pre-transaction cleanup outlives an early interrupt."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = backup_root / "schema-10.sqlite"
    backup.write_bytes(b"Vera Example migration backup")

    def interrupting_run(*_arguments, **_keywords):
        raise KeyboardInterrupt()

    monkeypatch.setattr(jd_service, "create_processing_run", interrupting_run)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["affected_ids"]["deleted"] == []
    assert envelope["run_ids"] == []
    assert envelope["result"]["removed_managed_paths"] == [
        str(backup.absolute())
    ]
    assert not backup.exists()
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 1


def test_a_workspace_swapped_under_the_lock_is_never_purged(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.13 rule 6: cleanup is bound to the locked database's identity."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = backup_root / "schema-10.sqlite"
    backup.write_bytes(b"Vera Example migration backup")
    real_stat = privacy_service.os.stat
    decoy = tmp_path / "decoy.sqlite"
    decoy.write_bytes(b"")

    def swapped_stat(path, **keywords):
        if path == privacy_service.DATABASE_NAME:
            # The marker directory now belongs to a replacement workspace.
            return real_stat(decoy)

        return real_stat(path, **keywords)

    monkeypatch.setattr(privacy_service.os, "stat", swapped_stat)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["diagnostic_class"] == "deletion_incomplete"
    assert envelope["residual_paths"] == [str(backup_root.absolute())]
    assert envelope["result"]["removed_managed_paths"] == []
    assert backup.read_bytes() == b"Vera Example migration backup"


def test_an_unreadable_database_anchor_refuses_the_purge(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An anchor that cannot be established is never permission to purge."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = backup_root / "schema-10.sqlite"
    backup.write_bytes(b"Vera Example migration backup")
    anchor = str(workspace / ".exp2res" / "exp2res.sqlite")

    class RefusingOs:
        """Stand in for one module's `os`, not the process-global module.

        Rebinding `jd_service.os` keeps the substitution inside the service
        under test: the CLI and the storage layer keep the real `os.stat`,
        which a `setattr` on the shared module would have taken away from
        them as well.
        """

        def __getattr__(self, name: str):
            return getattr(os, name)

        def stat(self, path, **keywords):
            if str(path) == anchor:
                raise PermissionError(13, "Permission denied")

            return os.stat(path, **keywords)

    monkeypatch.setattr(jd_service, "os", RefusingOs())

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 8
    assert envelope["residual_paths"] == [str(backup_root.absolute())]
    assert backup.read_bytes() == b"Vera Example migration backup"


def test_an_interrupt_entering_the_transaction_reports_no_deletion(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`in_transaction` false before BEGIN is not proof of a commit."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]

    class InterruptOnBegin:
        """Refuse the transaction exactly as it opens."""

        def __init__(self, connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        @property
        def in_transaction(self) -> bool:
            return self._connection.in_transaction

        def execute(self, statement: str, *arguments):
            if statement == "BEGIN IMMEDIATE":
                raise KeyboardInterrupt()

            return self._connection.execute(statement, *arguments)

    real_writer = jd_service.writer_database

    @contextmanager
    def wrapped(target: Path, **keywords):
        with real_writer(target, **keywords) as connection:
            yield InterruptOnBegin(connection)

    monkeypatch.setattr(jd_service, "writer_database", wrapped)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["affected_ids"]["deleted"] == []
    assert envelope["run_ids"] == []
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE stage = '13.13'"
        ).fetchone()[0] == 0


def test_an_interrupt_inside_the_purge_still_names_what_it_removed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: a removal made mid-pass is a durable effect."""

    _result, added = add_job_description(workspace, tmp_path, monkeypatch)
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    backup_root = workspace / ".exp2res" / "backup"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    first = backup_root / "schema-09.sqlite"
    second = backup_root / "schema-10.sqlite"
    first.write_bytes(b"Vera Example migration backup")
    second.write_bytes(b"Vera Example migration backup")
    real_unlink = privacy_service.os.unlink
    unlinked: list[str] = []

    def interrupting_unlink(name, **keywords):
        if unlinked:
            raise KeyboardInterrupt()

        unlinked.append(name)
        real_unlink(name, **keywords)

    monkeypatch.setattr(privacy_service.os, "unlink", interrupting_unlink)

    result, envelope = invoke_json(
        workspace, ["--yes", "jd", "delete", "--jd", job_description_id]
    )

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["result"]["removed_managed_paths"] == [str(first.absolute())]
    assert envelope["residual_paths"] == [str(backup_root.absolute())]
    assert not first.exists()
    assert second.exists()
