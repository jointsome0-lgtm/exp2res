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

from exp2res.domain.canonical import id_key
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
    WorkspaceBusyError,
)
from exp2res.exports.managed import (
    branch_set_paths,
    locked_workspace_predicate,
    remove_branch_sets,
)
from exp2res.pipeline.stage8 import Stage8Result, run_job_description_parse
from exp2res.services.capture import new_id
from exp2res.services.stages import RunTracking, build_llm_execution
from exp2res.services.source_files import read_capture_file
from exp2res.services.privacy import (
    checkpoint_residuals as _delete_checkpoint_residuals,
    locked_database_anchor,
    purge_managed_backups as _purge_managed_backups,
    report_unproven_residual,
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
    purged_generation_ids: tuple[str, ...]
    removed_managed_paths: tuple[str, ...]
    residual_paths: tuple[str, ...]


@dataclass(frozen=True)
class JobDescriptionCleanupOutcome:
    """§14.14 rule 6 carrier for managed cleanup done before a cancelled deletion."""

    selected: JobDescription
    removed_managed_paths: tuple[str, ...]
    residual_paths: tuple[str, ...]


def _path_key(value: str) -> bytes:
    return os.fsencode(value)


def _committed_effects(
    workspace: Path, run_ids: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # Created rows come from each completed run's `output_ids`, not allocated
    # IDs: §12 rule 11 retries collisions, so a rejected candidate is not a creation.

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
    return AffectedIds.of(created=(("job_description", created),))


def run_jd_add(workspace: Path, *, raw_text: str) -> Stage8Result:
    require_compatible(workspace)
    # §14.14 rule 4: boundary text fails in class 2 before any adapter or writer.
    try:
        validate_free_text(raw_text, raw=True, nonempty=True)
    except (ValidationError, ValueError, TypeError) as error:
        failure = InvalidInputError()
        failure.public_message = "The vacancy text failed strict validation."
        raise failure from error
    selection, budgets, runner = build_llm_execution(workspace)
    tracking = RunTracking(new_id)
    try:
        return run_job_description_parse(
            workspace,
            raw_text=raw_text,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=tracking.allocate,
            cli_version=__version__,
        )
    except LLMInvocationError as error:
        runs, created = _committed_effects(workspace, tracking.allocated_runs)
        # §14.14 rule 6: an interrupt as the commit returns leaves the row durable.
        extend_committed(
            error,
            run_ids=list(runs),
            **(
                {"affected_ids": _created_job_descriptions(created)}
                if created
                else {}
            ),
        )
        raise
    except KeyboardInterrupt as error:
        # Same rule one frame out: an interrupt during result build or lock
        # teardown is unclassified here, but a durable creation is still reported.
        runs, created = _committed_effects(workspace, tracking.allocated_runs)
        if not created:
            raise
        cancelled = OperationCancelledError()
        extend_committed(
            cancelled,
            run_ids=list(runs),
            affected_ids=_created_job_descriptions(created),
            warnings=list(committed_outcome(error).warnings),
        )
        raise cancelled from None


def run_jd_add_file(workspace: Path, *, source_path: str) -> Stage8Result:
    # §14.10 declares a path only; stdin (`-`) is §14.2's surface, not this one.
    if source_path == "-":
        raise InvalidInputError()
    # Fail closed before acquiring the private source file (§12.14, §22).
    require_compatible(workspace)
    raw_text, _external_ref = read_capture_file(
        source_path, config=load_workspace_config(workspace)
    )
    # §11.13 has no `external_ref`, so the locator is dropped.
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


# §13.13 rule 10's dependent set, derived from the one vacancy ID: a placeholder
# per row could exceed SQLITE_LIMIT_VARIABLE_NUMBER on a large branch.
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
    # §14.14 rule 5 / §12 rule 13: one generation ID spans branch and bullets.
    generation_ids = tuple(
        sorted(
            {
                row[0]
                for statement in (
                    "SELECT DISTINCT generation_id FROM resume_branches "
                    "WHERE job_description_id = ?",
                    "SELECT DISTINCT generation_id FROM resume_bullets "
                    f"WHERE branch_id IN ({_DEPENDENT_BRANCHES})",
                )
                for row in connection.execute(statement, (job_description_id,))
            },
            key=id_key,
        )
    )
    return bullet_ids, finding_ids, generation_ids


def _committed_outcome(
    *,
    run_id: str,
    selected: JobDescription,
    purged_branches: tuple[PurgedBranch, ...],
    purged_bullet_ids: tuple[str, ...],
    purged_finding_ids: tuple[str, ...],
    purged_generation_ids: tuple[str, ...],
    removed_paths: Iterable[str],
    residuals: Iterable[str],
) -> JobDescriptionDeleteOutcome:
    return JobDescriptionDeleteOutcome(
        run_id=run_id,
        selected=selected,
        purged_branches=purged_branches,
        purged_bullet_ids=purged_bullet_ids,
        purged_finding_ids=purged_finding_ids,
        purged_generation_ids=purged_generation_ids,
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
    """§13.13 rule 10: dependent purge for one vacancy."""

    now = clock or (lambda: datetime.now(timezone.utc))
    allocate_id = id_factory or new_id
    residual_paths: list[str] = []
    removed_paths: list[str] = []
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, owner_delete=True, timeout_ms=timeout_ms)
    )
    # §14.14 rule 6: these cells make checkpoint, result build, and lock
    # teardown one cancellation boundary that still reports a committed
    # deletion — or, one step earlier, managed cleanup done before it.
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
        database_identity = locked_database_anchor()
        # §12 rule 11 / §13.13 rule 6: allocate against the retained set so a
        # colliding telemetry ID cannot abort the purge.
        orchestration_run_id = _allocate_run_id(connection, allocate_id)
        # §13.13 rule 10: managed removal precedes the transaction, and these
        # captured branch IDs are the only source for `out/branch/<id>/`.
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
        # §14.14 rule 6: a mid-pass interrupt still names what was unlinked.
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
                    removed_managed_paths=tuple(sorted(set(unlinked), key=_path_key)),
                    residual_paths=(backup_root,),
                )
            )
            raise
        removed_paths.extend(removed)
        residual_paths.extend(backup_residuals)
        # §13.13 rule 10: exact ID-keyed sets only; §13.14 owns path validation.
        branch_ids = tuple(branch.id for branch in purged_branches)
        branch_parent = str((workspace / "out" / "branch").absolute())
        unlinked_sets: list[str] = []
        try:
            database_is_live = locked_workspace_predicate(workspace)

            existing_branch_sets = branch_set_paths(workspace, branch_ids)
            if not database_is_live():
                # §13.13 rule 6: the pathname no longer binds to the held
                # database, so removal would purge a foreign tree.
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
                    # Removed only when proven gone.
                    surviving = set(branch_set_paths(workspace, branch_ids))
                    removed_paths.extend(
                        path for path in existing_branch_sets if path not in surviving
                    )
                else:
                    # §13.13 rule 6: binding lost mid-pass — the names now
                    # address a replacement, so nothing is proven removed.
                    unlinked_sets.clear()
                    report_unproven_residual(
                        branch_set_paths(workspace, branch_ids, existing_only=False)
                    )
                    residual_paths.extend(
                        branch_set_paths(workspace, branch_ids, existing_only=False)
                    )
        except OSError:
            # §13.13 rule 6: cleanup never blocks the deletion.
            removed_paths.extend(unlinked_sets)
            residual_paths.append(branch_parent)
        except KeyboardInterrupt:
            # §14.14 rule 6: what was unlinked is durable; only this carries it.
            removed_paths.extend(unlinked_sets)
            cleaned.append(
                JobDescriptionCleanupOutcome(
                    selected=selected,
                    removed_managed_paths=tuple(
                        sorted(set(removed_paths), key=_path_key)
                    ),
                    residual_paths=tuple(
                        sorted({*residual_paths, branch_parent}, key=_path_key)
                    ),
                )
            )
            raise
        cleaned.append(
            JobDescriptionCleanupOutcome(
                selected=selected,
                removed_managed_paths=tuple(
                    sorted(set(removed_paths), key=_path_key)
                ),
                residual_paths=tuple(sorted(set(residual_paths), key=_path_key)),
            )
        )
        write_ahead_log = str(database.with_name(database.name + "-wal"))

        def build_outcome(residuals: Iterable[str]) -> JobDescriptionDeleteOutcome:
            return _committed_outcome(
                run_id=orchestration_run_id,
                selected=selected,
                purged_branches=purged_branches,
                purged_bullet_ids=purged_bullet_ids,
                purged_finding_ids=purged_finding_ids,
                purged_generation_ids=purged_generation_ids,
                removed_paths=removed_paths,
                residuals=residuals,
            )

        # `in_transaction` is false both before BEGIN and after COMMIT.
        commit_reached = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Revalidated under writer authority.
            selected = get_job_description(connection, job_description_id)
            if selected is None:
                raise SelectorNotFoundError()
            # §13.13 rule 10 / §24.47: content-free telemetry, committed with
            # the deletion so a rollback leaves no run claiming it happened.
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
            # §13.13 rule 10: findings → bullets → branches → vacancy, so no
            # foreign key blocks it; snapshots and claims are untouched.
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
            # §13.13 rule 10 applies rule 5's redaction: a hash is an oracle.
            connection.execute(
                "UPDATE llm_calls SET input_hash = NULL, output_hash = NULL"
            )
            finish_processing_run(
                connection,
                run_id=orchestration_run_id,
                finished_at=now(),
                status="completed",
            )
            commit_reached = True
            connection.commit()
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except BaseException:
            # §14.14 rule 6: an interrupt as `commit()` returns is durable;
            # the WAL stays residual until a checkpoint proves erasure.
            if connection.in_transaction or not commit_reached:
                connection.rollback()
                raise
            committed.append(build_outcome((*residual_paths, write_ahead_log)))
            raise
        # Durable from here: bank the pessimistic outcome before any more work.
        committed.append(build_outcome((*residual_paths, write_ahead_log)))
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
        # Surrogate-escaped names must render or stdout encoding fails.
        removed_managed_paths=[
            render_path(path) for path in deleted.removed_managed_paths
        ],
    )


def jd_delete_human_result(deleted: JobDescriptionDeleteOutcome) -> str:
    # §14.15: both modes report the same branches and paths.
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
    exit_code = 8 if deleted.residual_paths else 0
    return Outcome(
        exit_code=exit_code,
        diagnostic_class="deletion_incomplete" if exit_code else None,
        affected_ids=jd_delete_affected(deleted),
        generation_ids=list(deleted.purged_generation_ids),
        run_ids=[deleted.run_id],
        residual_paths=list(deleted.residual_paths),
        result=jd_delete_result(deleted),
        human_result=jd_delete_human_result(deleted),
    )


def job_description_projection(
    job_description: JobDescription,
) -> JobDescriptionProjection:
    """§14.15 discovery projection: no `raw_text`, no `parsed`."""

    return JobDescriptionProjection(
        id=job_description.id,
        created_at=job_description.created_at,
        title=job_description.title,
        company=job_description.company,
    )
