"""Stage 8 execution, job-description inspection, and owner deletion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable, Iterable

from pydantic import ValidationError

from exp2res import __version__
from exp2res.config import load_workspace_config
from exp2res.domain.models import JobDescription, validate_free_text
from exp2res.domain.results import (
    AffectedIds,
    JdDeleteResult,
    JobDescriptionProjection,
    Outcome,
    PurgedBranchProjection,
    committed_outcome,
    extend_committed,
    render_path,
)
from exp2res.errors import (
    IdCollisionError,
    InvalidInputError,
    LLMInvocationError,
    OperationCancelledError,
    SelectorNotFoundError,
)
from exp2res.exports.managed import (
    branch_set_paths,
    locked_workspace_predicate,
    remove_branch_sets,
)
from exp2res.pipeline.stage8 import Stage8Result, run_job_description_parse
from exp2res.services.capture import new_id
from exp2res.services import stages
from exp2res.services.stages import Run, RunIds
from exp2res.services.source_files import read_capture_file
from exp2res.services.privacy import (
    cancelled_with,
    checkpoint_residuals as _delete_checkpoint_residuals,
    deletion_outcome,
    generation_ids,
    locked_database_anchor,
    purge_managed_backups as _purge_managed_backups,
    report_unproven_residual,
    sorted_paths,
    wal_path,
)
from exp2res.services.writers import banked_transaction, held_writer, retry_id_collisions
from exp2res.storage.repository import (
    get_job_description,
    list_job_descriptions as _list_job_descriptions,
)
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
    purged_generation_ids: tuple[str, ...]
    removed_managed_paths: tuple[str, ...]
    residual_paths: tuple[str, ...]


@dataclass(frozen=True)
class JobDescriptionCleanupOutcome:
    """§14.14 rule 6: managed cleanup done before a cancelled deletion."""

    selected: JobDescription
    removed_managed_paths: tuple[str, ...]
    residual_paths: tuple[str, ...]


def run_jd_add(workspace: Path, *, raw_text: str) -> Stage8Result:
    require_compatible(workspace)
    # §14.14 rule 4: class 2 before any adapter or writer.
    try:
        validate_free_text(raw_text, raw=True, nonempty=True)
    except (ValidationError, ValueError, TypeError) as error:
        failure = InvalidInputError()
        failure.public_message = "The vacancy text failed strict validation."
        raise failure from error
    selection, budgets, runner = stages.build_llm_execution(workspace)
    ids = RunIds(new_id)
    try:
        return run_job_description_parse(
            workspace,
            raw_text=raw_text,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=ids.allocate,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        # §14.14 rule 6: the row may be durable.
        with read_database(workspace) as connection:
            ids.carry(connection, error, created_as="job_description")
        raise
    except KeyboardInterrupt as error:
        # §14.14 rule 6, one frame out: a durable creation is still reported.
        cancelled = OperationCancelledError()
        with read_database(workspace) as connection:
            if not ids.carry(connection, cancelled, created_as="job_description"):
                raise
        extend_committed(cancelled, warnings=list(committed_outcome(error).warnings))
        raise cancelled from None


def run_jd_add_file(workspace: Path, *, source_path: str) -> Stage8Result:
    # §14.10: path only; stdin is §14.2's surface.
    if source_path == "-":
        raise InvalidInputError()
    # §12.14: fail closed before reading the private file.
    require_compatible(workspace)
    raw_text, _external_ref = read_capture_file(
        source_path, config=load_workspace_config(workspace)
    )
    # §11.13 has no `external_ref`.
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
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        selected = get_job_description(connection, job_description_id)
    if selected is None:
        raise SelectorNotFoundError()
    return selected


def _allocate_run_id(
    connection: sqlite3.Connection, id_factory: Callable[[str], str]
) -> str:
    taken = {
        row[0] for row in connection.execute("SELECT id FROM processing_runs")
    }

    def attempt(_index: int) -> str:
        candidate = id_factory("run")
        if not candidate or candidate in taken:
            raise IdCollisionError()
        return candidate

    return retry_id_collisions(attempt)


# §13.13 rule 10 dependent set by vacancy ID; per-row placeholders could exceed SQLITE_LIMIT_VARIABLE_NUMBER.
_DEPENDENT_BRANCHES = "SELECT id FROM resume_branches WHERE job_description_id = ?"
_DEPENDENT_BULLETS = (
    f"SELECT id FROM resume_bullets WHERE branch_id IN ({_DEPENDENT_BRANCHES})"
)


def _dependent_purge_targets(
    connection: sqlite3.Connection, job_description_id: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    bullet_ids = tuple(
        row[0]
        for row in connection.execute(
            f"{_DEPENDENT_BULLETS} ORDER BY CAST(id AS BLOB)",
            (job_description_id,),
        )
    )
    finding_ids = tuple(
        row[0]
        for row in connection.execute(
            "SELECT id FROM verification_findings "
            "WHERE target_type = 'resume_bullet' "
            f"AND target_id IN ({_DEPENDENT_BULLETS}) ORDER BY CAST(id AS BLOB)",
            (job_description_id,),
        )
    )
    # §12 rule 13: one generation ID spans branch and bullets.
    return bullet_ids, finding_ids, generation_ids(
        connection,
        (
            ("resume_branches", "job_description_id = ?", (job_description_id,)),
            ("resume_bullets", f"branch_id IN ({_DEPENDENT_BRANCHES})", (job_description_id,)),
        ),
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
    """§13.13 rule 10: dependent purge for one vacancy."""

    now = clock or (lambda: datetime.now(timezone.utc))
    allocate_id = id_factory or new_id
    residual_paths: list[str] = []
    removed_paths: list[str] = []
    held = held_writer(
        connection, writer_database, workspace, owner_delete=True, timeout_ms=timeout_ms
    )
    # §14.14 rule 6: one cancellation boundary over checkpoint, result build and
    # teardown that still reports a committed deletion (or earlier managed cleanup).
    committed: list[JobDescriptionDeleteOutcome] = []
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
        if committed:
            raise cancelled_with(delete_outcome=committed[-1]) from None
        if cleaned and (
            cleaned[-1].removed_managed_paths or cleaned[-1].residual_paths
        ):
            raise cancelled_with(cleanup_outcome=cleaned[-1]) from None
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
        database_identity = locked_database_anchor()
        # §12 rule 11: a colliding telemetry ID must not abort the purge.
        run = Run(
            connection,
            stage="13.13",
            input_ids=lambda _held: (job_description_id,),
            metadata={"mode": "job_description"},
            clock=now,
            new_id=lambda _kind: _allocate_run_id(connection, allocate_id),
            own_transaction=False,
        )
        orchestration_run_id = run.id
        # §13.13 rule 10: managed removal precedes the transaction; these IDs name `out/branch/<id>/`.
        purged_branches = tuple(
            PurgedBranch(id=row["id"], name=row["name"])
            for row in connection.execute(
                "SELECT id, name FROM resume_branches WHERE job_description_id = ? "
                "ORDER BY CAST(id AS BLOB)",
                (job_description_id,),
            )
        )
        (
            purged_bullet_ids,
            purged_finding_ids,
            purged_generation_ids,
        ) = _dependent_purge_targets(connection, job_description_id)
        # §14.14 rule 6: names what was unlinked mid-pass.
        unlinked: list[str] = []
        try:
            if database_identity is None:
                removed, backup_residuals = (), (backup_root,)
            else:
                removed, backup_residuals = _purge_managed_backups(
                    workspace,
                    expected_database=database_identity,
                    removed_ledger=unlinked,
                )
        except KeyboardInterrupt:
            cleaned.append(
                JobDescriptionCleanupOutcome(
                    selected=selected,
                    removed_managed_paths=sorted_paths(unlinked),
                    residual_paths=(backup_root,),
                )
            )
            raise
        removed_paths.extend(removed)
        residual_paths.extend(backup_residuals)
        # §13.13 rule 10: exact ID-keyed sets; §13.14 owns path validation.
        branch_ids = tuple(branch.id for branch in purged_branches)
        branch_parent = str((workspace / "out" / "branch").absolute())
        unlinked_sets: list[str] = []
        try:
            database_is_live = locked_workspace_predicate(workspace)

            existing_branch_sets = branch_set_paths(workspace, branch_ids)
            if not database_is_live():
                # §13.13 rule 6: binding lost — removal would purge a foreign tree.
                residual_paths.extend(
                    branch_set_paths(workspace, branch_ids, existing_only=False)
                )
            else:
                residual_paths.extend(
                    remove_branch_sets(
                        workspace,
                        branch_ids,
                        removed_ledger=unlinked_sets,
                        still_live=database_is_live,
                    )
                )
                if database_is_live():
                    surviving = set(branch_set_paths(workspace, branch_ids))
                    removed_paths.extend(
                        path for path in existing_branch_sets if path not in surviving
                    )
                else:
                    # §13.13 rule 6: binding lost mid-pass, nothing proven removed.
                    unlinked_sets.clear()
                    report_unproven_residual(
                        branch_set_paths(workspace, branch_ids, existing_only=False)
                    )
                    residual_paths.extend(
                        branch_set_paths(workspace, branch_ids, existing_only=False)
                    )
        except OSError:
            # §13.13 rule 6: cleanup never blocks deletion.
            removed_paths.extend(unlinked_sets)
            residual_paths.append(branch_parent)
        except KeyboardInterrupt:
            # §14.14 rule 6: only this carries what was unlinked.
            removed_paths.extend(unlinked_sets)
            cleaned.append(
                JobDescriptionCleanupOutcome(
                    selected=selected,
                    removed_managed_paths=sorted_paths(removed_paths),
                    residual_paths=sorted_paths((*residual_paths, branch_parent)),
                )
            )
            raise
        cleaned.append(
            JobDescriptionCleanupOutcome(
                selected=selected,
                removed_managed_paths=sorted_paths(removed_paths),
                residual_paths=sorted_paths(residual_paths),
            )
        )
        def build_outcome(residuals: Iterable[str]) -> JobDescriptionDeleteOutcome:
            return JobDescriptionDeleteOutcome(
                run_id=orchestration_run_id,
                selected=selected,
                purged_branches=purged_branches,
                purged_bullet_ids=purged_bullet_ids,
                purged_finding_ids=purged_finding_ids,
                purged_generation_ids=purged_generation_ids,
                removed_managed_paths=sorted_paths(removed_paths),
                residual_paths=sorted_paths(residuals),
            )

        # Durable: the WAL is residual until a checkpoint proves erasure.
        def bank() -> None:
            committed.append(build_outcome((*residual_paths, wal_path(database))))

        with banked_transaction(connection, bank):
            selected = get_job_description(connection, job_description_id)
            if selected is None:
                raise SelectorNotFoundError()
            # §24.47: telemetry in the deletion's own transaction.
            run.create()
            # §13.13 rule 10: FK order findings → bullets → branches → vacancy.
            connection.execute(
                "DELETE FROM verification_findings "
                "WHERE target_type = 'resume_bullet' "
                f"AND target_id IN ({_DEPENDENT_BULLETS})",
                (job_description_id,),
            )
            connection.execute(
                f"DELETE FROM resume_bullets WHERE branch_id IN ({_DEPENDENT_BRANCHES})",
                (job_description_id,),
            )
            connection.execute(
                "DELETE FROM resume_branches WHERE job_description_id = ?",
                (job_description_id,),
            )
            connection.execute(
                "DELETE FROM job_descriptions WHERE id = ?", (job_description_id,)
            )
            # §13.13 rule 5: a hash is an oracle.
            connection.execute(
                "UPDATE llm_calls SET input_hash = NULL, output_hash = NULL"
            )
            run.finish()
        residual_paths.extend(_delete_checkpoint_residuals(connection, database))
        outcome = build_outcome(tuple(residual_paths))
        committed.append(outcome)
        return outcome


def jd_delete_affected(deleted: JobDescriptionDeleteOutcome) -> AffectedIds:
    classes = (
        ("verification_finding", deleted.purged_finding_ids),
        ("resume_bullet", deleted.purged_bullet_ids),
        (
            "resume_branch",
            tuple(branch.id for branch in deleted.purged_branches),
        ),
        ("job_description", (deleted.selected.id,)),
    )
    return AffectedIds.of(deleted=classes)


def jd_delete_result(deleted: JobDescriptionDeleteOutcome) -> JdDeleteResult:
    return JdDeleteResult(
        selected_job_description=job_description_projection(deleted.selected),
        purged_branches=[
            PurgedBranchProjection(id=branch.id, name=branch.name)
            for branch in deleted.purged_branches
        ],
        # Surrogate-escaped names must render.
        removed_managed_paths=[
            render_path(path) for path in deleted.removed_managed_paths
        ],
    )


def jd_delete_human_result(deleted: JobDescriptionDeleteOutcome) -> str:
    # §14.15: both modes report the same set.
    lines = [
        f"Deleted job description {deleted.selected.id}; no derived "
        "state remained."
        if not deleted.purged_branches
        else (
            f"Deleted job description {deleted.selected.id} and "
            f"{len(deleted.purged_branches)} dependent branch"
            f"{'' if len(deleted.purged_branches) == 1 else 'es'}."
        )
    ]
    lines.extend(
        f"Purged branch: {branch.id}\t{branch.name}"
        for branch in deleted.purged_branches
    )
    lines.extend(
        f"Removed managed path: {path}"
        for path in jd_delete_result(deleted).removed_managed_paths
    )
    return "\n".join(lines)


def jd_delete_outcome(deleted: JobDescriptionDeleteOutcome) -> Outcome:
    return deletion_outcome(
        deleted.residual_paths,
        affected_ids=jd_delete_affected(deleted),
        generation_ids=list(deleted.purged_generation_ids),
        run_ids=[deleted.run_id],
        result=jd_delete_result(deleted),
        human_result=jd_delete_human_result(deleted),
    )


def job_description_projection(
    job_description: JobDescription,
) -> JobDescriptionProjection:
    """§14.15: no `raw_text`, no `parsed`."""

    return JobDescriptionProjection(
        id=job_description.id,
        created_at=job_description.created_at,
        title=job_description.title,
        company=job_description.company,
    )
