"""Stage 8 execution, job-description inspection, and owner deletion."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Callable, Iterable

from exp2res import __version__
from exp2res.config import load_workspace_config
from exp2res.domain.models import JobDescription
from exp2res.errors import (
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


def _path_key(value: str) -> bytes:
    return os.fsencode(value)


def _committed_runs(workspace: Path, run_ids: list[str]) -> tuple[str, ...]:
    if not run_ids:
        return ()
    placeholders = ",".join("?" for _ in run_ids)
    with read_database(workspace) as connection:
        rows = connection.execute(
            f"SELECT id FROM processing_runs WHERE id IN ({placeholders})",
            run_ids,
        ).fetchall()
    committed = {row[0] for row in rows}
    return tuple(run_id for run_id in run_ids if run_id in committed)


def run_jd_add(workspace: Path, *, raw_text: str) -> Stage8Result:
    """Resolve configured execution lazily and run the one Stage 8 parse."""

    require_compatible(workspace)
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
        error.run_ids = _committed_runs(workspace, allocated_runs)
        raise


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
    orchestration_run_id = (id_factory or new_id)("run")
    residual_paths: list[str] = []
    removed_paths: list[str] = []
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, owner_delete=True, timeout_ms=timeout_ms)
    )
    with held as connection:
        selected = get_job_description(connection, job_description_id)
        if selected is None:
            raise SelectorNotFoundError()
        # §13.13 rule 10 orders managed-path removal before the database
        # transaction, so the writer lock is never held across filesystem I/O
        # and an interrupt between the two leaves no half-open transaction.
        removed, backup_residuals = _purge_managed_backups(workspace)
        removed_paths.extend(removed)
        residual_paths.extend(backup_residuals)
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
            # §13.13 rule 10 captures every current or historical branch naming
            # this job description, then deletes that branch state before the
            # job description itself. `resume_branches`, `resume_bullets`, and
            # their findings arrive with §22 Phase 4's Stages 10-12; until
            # then no branch can exist, so the captured set is empty, the
            # dependent deletes have no table to address, and the
            # `out/branch/<branch-id>/` half of the managed removal below has
            # no ID source. All three join here with those tables.
            purged_branches: tuple[PurgedBranch, ...] = ()
            purged_bullet_ids: tuple[str, ...] = ()
            purged_finding_ids: tuple[str, ...] = ()

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
            # discard the report of something that already happened.
            if connection.in_transaction:
                connection.rollback()
                raise
            cancelled = OperationCancelledError()
            cancelled.delete_outcome = _committed_outcome(
                run_id=orchestration_run_id,
                selected=selected,
                purged_branches=purged_branches,
                purged_bullet_ids=purged_bullet_ids,
                purged_finding_ids=purged_finding_ids,
                removed_paths=removed_paths,
                residuals=(
                    *residual_paths,
                    str(
                        (workspace / ".exp2res" / "exp2res.sqlite-wal").absolute()
                    ),
                ),
            )
            raise cancelled from None

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

        database = workspace / ".exp2res" / "exp2res.sqlite"
        try:
            residual_paths.extend(
                _delete_checkpoint_residuals(connection, database)
            )
        except KeyboardInterrupt:
            # §14.14 rule 6: the purge committed before checkpoint work, so
            # cancellation carries the durable deletion and reports the WAL as
            # residual until a later writer proves erasure.
            cancelled = OperationCancelledError()
            cancelled.delete_outcome = build_outcome(
                (
                    *residual_paths,
                    str(database.with_name(database.name + "-wal")),
                )
            )
            raise cancelled from None

        return build_outcome(tuple(residual_paths))
