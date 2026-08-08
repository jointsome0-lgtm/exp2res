"""Stage 8 execution, job-description inspection, and owner deletion."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable, Iterable

from pydantic import ValidationError

from exp2res import __version__
from exp2res.config import load_workspace_config
from exp2res.domain.models import JobDescription, validate_free_text
from exp2res.domain.results import AffectedIds, EntityIdGroup
from exp2res.errors import (
    IdCollisionError,
    InvalidInputError,
    LLMInvocationError,
    OperationCancelledError,
    SelectorNotFoundError,
    WorkspaceBusyError,
)
from exp2res.pipeline.stage8 import Stage8Result, run_job_description_parse
from exp2res.services.capture import new_id
from exp2res.services.extraction import build_llm_execution
from exp2res.services.source_files import read_capture_file
from exp2res.services.privacy import (
    checkpoint_residuals as _delete_checkpoint_residuals,
    purge_managed_backups as _purge_managed_backups,
)
from exp2res.storage.repository import (
    get_job_description,
    list_job_descriptions as _list_job_descriptions,
)
from exp2res.storage.telemetry import create_processing_run, finish_processing_run
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    read_database,
    require_compatible,
    writer_database,
)


@dataclass(frozen=True)
class PurgedBranch:
    id: str
    name: str


@dataclass(frozen=True)
class JobDescriptionDeleteOutcome:
    run_id: str
    selected: JobDescription
    purged_branches: tuple[PurgedBranch, ...]
    purged_bullet_ids: tuple[str, ...]
    purged_finding_ids: tuple[str, ...]
    removed_managed_paths: tuple[str, ...]
    residual_paths: tuple[str, ...]


@dataclass(frozen=True)
class JobDescriptionCleanupOutcome:
    """Managed cleanup that outlived a cancellation before the deletion.

    §13.13 rule 10 removes managed paths before the database transaction, so
    an interrupt in between leaves the vacancy in place with filesystem work
    already done. §14.14 rule 6 still requires that work reported, and it
    cannot travel as a delete outcome: nothing was deleted.
    """

    selected: JobDescription
    removed_managed_paths: tuple[str, ...]
    residual_paths: tuple[str, ...]


def _path_key(value: str) -> bytes:
    return os.fsencode(value)


def _committed_effects(
    workspace: Path, run_ids: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Report the runs that committed and the records they actually created.

    Creation is read back from each completed run's own `output_ids`, which
    name the rows its commit wrote. Allocated IDs cannot stand in: §12 rule 11
    allocation retries a collision, so a candidate the allocator rejected
    would otherwise be reported as this command's creation.
    """

    if not run_ids:
        return (), ()
    placeholders = ",".join("?" for _ in run_ids)
    with read_database(workspace) as connection:
        rows = connection.execute(
            "SELECT id, status, output_ids_json FROM processing_runs "
            f"WHERE id IN ({placeholders})",
            run_ids,
        ).fetchall()
    committed = {row[0]: (row[1], row[2]) for row in rows}
    created: list[str] = []
    for run_id in run_ids:
        status, output_ids_json = committed.get(run_id, (None, None))
        if status != "completed":
            continue
        created.extend(json.loads(output_ids_json or "[]"))
    return (
        tuple(run_id for run_id in run_ids if run_id in committed),
        tuple(created),
    )


def _created_job_descriptions(created: Iterable[str]) -> AffectedIds:
    return AffectedIds(
        created=[EntityIdGroup(entity_type="job_description", ids=list(created))]
    )


def run_jd_add(workspace: Path, *, raw_text: str) -> Stage8Result:
    """Resolve configured execution lazily and run the one Stage 8 parse."""

    require_compatible(workspace)
    # §14.14 rule 4: boundary text is rejected in exit class 2 before any
    # adapter is built or any writer authority is taken, so an empty or
    # control-bearing vacancy never reaches §15.9's payload as an
    # unclassified internal failure after the writer preamble ran.
    try:
        validate_free_text(raw_text, raw=True, nonempty=True)
    except (ValidationError, ValueError, TypeError) as error:
        failure = InvalidInputError()
        failure.public_message = "The vacancy text failed strict validation."
        raise failure from error
    selection, budgets, runner = build_llm_execution(workspace)
    allocated_runs: list[str] = []

    def tracking_id_factory(kind: str) -> str:
        value = new_id(kind)
        if kind == "run":
            allocated_runs.append(value)
        return value

    try:
        return run_job_description_parse(
            workspace,
            raw_text=raw_text,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking_id_factory,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        runs, created = _committed_effects(workspace, allocated_runs)
        error.run_ids = runs
        # §14.14 rule 6: an interrupt delivered as Stage 8's business commit
        # returns leaves the row durable, so cancellation reports the created
        # job description rather than an effect-free envelope.
        if created:
            error.affected_ids = _created_job_descriptions(created)
        raise
    except KeyboardInterrupt as error:
        # The same rule one frame further out: the interrupt may also land
        # after Stage 8 returned from its commit — while its result is built,
        # or while the writer lock and connection tear down — where nothing
        # has classified it yet. A durable creation still gets reported.
        runs, created = _committed_effects(workspace, allocated_runs)
        if not created:
            raise
        cancelled = OperationCancelledError()
        cancelled.run_ids = list(runs)
        cancelled.affected_ids = _created_job_descriptions(created)
        cancelled.warnings = list(getattr(error, "warnings", ()) or ())
        raise cancelled from None


def run_jd_add_file(workspace: Path, *, source_path: str) -> Stage8Result:
    """Acquire the vacancy file under §29.4, then run the one Stage 8 parse."""

    # §14.10 declares a positional filesystem path; stdin belongs to §14.2's
    # explicitly declared `--file -` capture forms, and accepting it here
    # would be an undeclared input surface that never reaches §29.4's
    # path gate at all.
    if source_path == "-":
        raise InvalidInputError()
    # Fail closed before acquiring the private source file (§12.14, §22).
    require_compatible(workspace)
    raw_text, _external_ref = read_capture_file(
        source_path, config=load_workspace_config(workspace)
    )
    # A job description records no source locator: §11.13 has no
    # `external_ref`, and §14.14 rule 5's projection would not expose one.
    return run_jd_add(workspace, raw_text=raw_text)


def list_job_descriptions(
    workspace: Path, *, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> tuple[JobDescription, ...]:
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        return _list_job_descriptions(connection)


def show_job_description(
    workspace: Path,
    *,
    job_description_id: str,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> JobDescription:
    """Resolve one selector read-only, before any writer authority is taken."""

    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        selected = get_job_description(connection, job_description_id)
    if selected is None:
        raise SelectorNotFoundError()
    return selected


_RUN_ID_ATTEMPTS = 8


def _allocate_run_id(
    connection: sqlite3.Connection, id_factory: Callable[[str], str]
) -> str:
    taken = {
        row[0] for row in connection.execute("SELECT id FROM processing_runs")
    }
    for _attempt in range(_RUN_ID_ATTEMPTS):
        candidate = id_factory("run")
        if candidate and candidate not in taken:
            return candidate
    raise IdCollisionError()


def _committed_outcome(
    *,
    run_id: str,
    selected: JobDescription,
    purged_branches: tuple[PurgedBranch, ...],
    purged_bullet_ids: tuple[str, ...],
    purged_finding_ids: tuple[str, ...],
    removed_paths: Iterable[str],
    residuals: Iterable[str],
) -> JobDescriptionDeleteOutcome:
    """Report one committed deletion, however it reached its end."""

    return JobDescriptionDeleteOutcome(
        run_id=run_id,
        selected=selected,
        purged_branches=purged_branches,
        purged_bullet_ids=purged_bullet_ids,
        purged_finding_ids=purged_finding_ids,
        removed_managed_paths=tuple(sorted(set(removed_paths), key=_path_key)),
        residual_paths=tuple(sorted(set(residuals), key=_path_key)),
    )


def delete_job_description(
    workspace: Path,
    *,
    job_description_id: str,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    connection: sqlite3.Connection | None = None,
    id_factory: Callable[[str], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> JobDescriptionDeleteOutcome:
    """Run §13.13 rule 10's dependent purge for one vacancy."""

    now = clock or (lambda: datetime.now(timezone.utc))
    allocate_id = id_factory or new_id
    residual_paths: list[str] = []
    removed_paths: list[str] = []
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, owner_delete=True, timeout_ms=timeout_ms)
    )
    # §14.14 rule 6: once the deletion commits, no later interrupt may produce
    # an empty cancelled envelope. This cell makes the whole remaining region —
    # checkpoint, result construction, lock and connection teardown — one
    # cancellation boundary instead of a sequence of narrower guarded blocks.
    committed: list[JobDescriptionDeleteOutcome] = []
    # The same guarantee one step earlier: managed cleanup runs before the
    # transaction, so its effects have to survive a cancellation that reaches
    # the command before anything was deleted.
    cleaned: list[JobDescriptionCleanupOutcome] = []
    try:
        return _delete_locked(
            workspace,
            held=held,
            job_description_id=job_description_id,
            allocate_id=allocate_id,
            residual_paths=residual_paths,
            removed_paths=removed_paths,
            committed=committed,
            cleaned=cleaned,
            now=now,
        )
    except KeyboardInterrupt:
        cancelled = OperationCancelledError()
        if committed:
            cancelled.delete_outcome = committed[-1]
            raise cancelled from None
        if cleaned and (
            cleaned[-1].removed_managed_paths or cleaned[-1].residual_paths
        ):
            cancelled.cleanup_outcome = cleaned[-1]
            raise cancelled from None
        raise


def _delete_locked(
    workspace: Path,
    *,
    held,
    job_description_id: str,
    allocate_id: Callable[[str], str],
    residual_paths: list[str],
    removed_paths: list[str],
    committed: list[JobDescriptionDeleteOutcome],
    cleaned: list[JobDescriptionCleanupOutcome],
    now: Callable[[], datetime],
) -> JobDescriptionDeleteOutcome:
    database = workspace / ".exp2res" / "exp2res.sqlite"
    backup_root = str((workspace / ".exp2res" / "backup").absolute())
    with held as connection:
        selected = get_job_description(connection, job_description_id)
        if selected is None:
            raise SelectorNotFoundError()
        # Identity of the database this command is about to delete from,
        # taken under the §8.1 writer authority. Managed cleanup is bound to
        # it, so a workspace renamed and replaced after the lock cannot make
        # this command purge one tree while deleting from another.
        # An unreadable database file means that anchor cannot be established,
        # which is never treated as permission to purge (§13.13 rule 6).
        try:
            database_identity: os.stat_result | None = os.stat(database)
        except OSError:
            database_identity = None
        # §13.13 rule 10 orders managed-path removal before the database
        # transaction, so the writer lock is never held across filesystem I/O
        # and an interrupt between the two leaves no half-open transaction.
        # §12 rule 11 / §13.13 rule 6: a telemetry ID that collided with a
        # retained run would abort the transaction and leave the vacancy in
        # place, so the value is allocated against the retained set with the
        # same bounded local retry Stage 8 uses.
        orchestration_run_id = _allocate_run_id(connection, allocate_id)
        try:
            if database_identity is None:
                removed, backup_residuals = (), (backup_root,)
            else:
                removed, backup_residuals = _purge_managed_backups(
                    workspace, expected_database=database_identity
                )
        except KeyboardInterrupt:
            # The pass was cut mid-flight, so which names it had already
            # removed is unknown and the store's state is unproven: the root
            # is the honest report (§13.13 rule 6).
            cleaned.append(
                JobDescriptionCleanupOutcome(
                    selected=selected,
                    removed_managed_paths=(),
                    residual_paths=(backup_root,),
                )
            )
            raise
        removed_paths.extend(removed)
        residual_paths.extend(backup_residuals)
        cleaned.append(
            JobDescriptionCleanupOutcome(
                selected=selected,
                removed_managed_paths=tuple(
                    sorted(set(removed_paths), key=_path_key)
                ),
                residual_paths=tuple(sorted(set(residual_paths), key=_path_key)),
            )
        )
        # §13.13 rule 10 captures every current or historical branch naming
        # this job description, then deletes that branch state before the job
        # description itself. `resume_branches`, `resume_bullets`, and their
        # findings arrive with §22 Phase 4's Stages 10-12; until then no branch
        # can exist, so the captured set is empty, the dependent deletes have
        # no table to address, and the `out/branch/<branch-id>/` half of the
        # managed removal above has no ID source. All three join here with
        # those tables.
        purged_branches: tuple[PurgedBranch, ...] = ()
        purged_bullet_ids: tuple[str, ...] = ()
        purged_finding_ids: tuple[str, ...] = ()
        write_ahead_log = str(database.with_name(database.name + "-wal"))

        def build_outcome(residuals: Iterable[str]) -> JobDescriptionDeleteOutcome:
            return _committed_outcome(
                run_id=orchestration_run_id,
                selected=selected,
                purged_branches=purged_branches,
                purged_bullet_ids=purged_bullet_ids,
                purged_finding_ids=purged_finding_ids,
                removed_paths=removed_paths,
                residuals=residuals,
            )

        try:
            connection.execute("BEGIN IMMEDIATE")
            # The selector is revalidated under the writer authority: the
            # read above could not hold it.
            selected = get_job_description(connection, job_description_id)
            if selected is None:
                raise SelectorNotFoundError()
            # §13.13 rule 10 / §24.47: the deletion's own content-free
            # orchestration telemetry, committed with the deletion so a
            # rolled-back purge leaves no run claiming it happened. No
            # provider is involved and no recompute follows.
            create_processing_run(
                connection,
                run_id=orchestration_run_id,
                stage="13.13",
                started_at=now(),
                provider=None,
                model=None,
                prompt_policy_hash=None,
                input_ids=(job_description_id,),
                metadata={"mode": "job_description"},
            )
            connection.execute(
                "DELETE FROM job_descriptions WHERE id = ?", (job_description_id,)
            )
            # §13.13 rule 10 applies rule 5's global redaction: a deterministic
            # hash of guessable purged vacancy text would remain an oracle.
            connection.execute(
                "UPDATE llm_calls SET input_hash = NULL, output_hash = NULL"
            )
            finish_processing_run(
                connection,
                run_id=orchestration_run_id,
                finished_at=now(),
                status="completed",
            )
            connection.commit()
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except BaseException:
            # §14.14 rule 6: an interrupt delivered as `commit()` returns
            # leaves the deletion durable, so a rollback here would only
            # discard the report of something that already happened. The WAL
            # stays residual until a checkpoint proves erasure.
            if connection.in_transaction:
                connection.rollback()
                raise
            committed.append(build_outcome((*residual_paths, write_ahead_log)))
            raise
        # From here the deletion is durable. The pessimistic outcome is banked
        # before any further work, so an interrupt anywhere below — checkpoint,
        # teardown, or the caller's own result assembly — still reports it.
        committed.append(build_outcome((*residual_paths, write_ahead_log)))
        residual_paths.extend(_delete_checkpoint_residuals(connection, database))
        outcome = build_outcome(tuple(residual_paths))
        committed.append(outcome)
        return outcome
