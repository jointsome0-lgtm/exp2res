"""Manual daily and retrospective capture services."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4

from pydantic import ValidationError

from exp2res.domain.results import (
    AffectedIds,
    Outcome,
    carry_committed,
)
from exp2res.config import load_workspace_config, require_timezone
from exp2res.domain.models import (
    EvidenceItem,
    OccurredAt,
    RawLog,
    canonical_project_key,
)
from exp2res.errors import (
    BlankProjectLabelError,
    GapAlreadyAnsweredError,
    IdCollisionError,
    InvalidInputError,
    SelectorNotFoundError,
    WorkspaceBusyError,
)
from exp2res.exports.managed import (
    assessment_set_paths,
    remove_managed_sets_for_locked_database,
)
from exp2res.pipeline.stage1 import FailureHook, persist_manual_capture
from exp2res.services.interrupts import defer_interrupt
from exp2res.services.source_files import (
    ArtifactLocator,
    authorize_artifact_locators,
    read_capture_file,
)
from exp2res.services.time_input import today_occurred, workspace_zone
from exp2res.storage.repository import (
    RawLogBundle,
    get_gap_question,
    insert_evidence_item,
    insert_raw_log,
    mark_gap_answered,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    read_database,
    report_managed_residuals,
    require_compatible,
    withdraw_managed_residuals,
    writer_database,
)

IdFactory = Callable[[str], str]
Clock = Callable[[], datetime]


def new_id(kind: str) -> str:
    prefixes = {
        "raw_log": "log",
        "evidence_item": "evi",
        "fact": "fact",
        "gap": "gap",
        "contradiction": "contradiction",
        "snapshot": "snapshot",
        "claim": "claim",
        "finding": "finding",
        "job_description": "jd",
        "jd_requirement": "jdreq",
        "branch": "branch",
        "bullet": "bullet",
        "run": "run",
        "gen": "gen",
    }
    try:
        prefix = prefixes[kind]
    except KeyError:
        raise ValueError("unknown entity ID kind") from None
    return f"{prefix}_{uuid4().hex}"


def _invalid_capture(error: BaseException) -> InvalidInputError:
    failure = InvalidInputError()
    failure.diagnostic_class = "capture_validation_failed"
    failure.public_message = "Manual capture failed strict validation."
    failure.__cause__ = error
    return failure


def validate_project_label(project: str | None) -> None:
    """Reject §12 rule 14's invalid non-null blank identity at acquisition."""

    if project is not None and not canonical_project_key(project):
        raise BlankProjectLabelError()


def _authorized_artifacts(
    workspace: Path, artifacts: tuple[str, ...]
) -> tuple[ArtifactLocator, ...]:
    if not artifacts:
        return ()
    require_compatible(workspace)
    return authorize_artifact_locators(
        artifacts, config=load_workspace_config(workspace)
    )


def build_capture_evidence_items(
    *,
    raw_log_id: str,
    created_at: datetime,
    artifacts: tuple[ArtifactLocator, ...],
    id_factory: IdFactory,
) -> tuple[EvidenceItem, ...]:
    return (
        EvidenceItem(
            id=id_factory("evidence_item"),
            created_at=created_at,
            raw_log_id=raw_log_id,
            title=None,
            summary="Owner-authored manual claim.",
            uri=None,
            path=None,
            strength="manual_claim",
            metadata={},
        ),
        *(
            EvidenceItem(
                id=id_factory("evidence_item"),
                created_at=created_at,
                raw_log_id=raw_log_id,
                title=None,
                summary="Owner-supplied artifact reference.",
                uri=artifact.uri,
                path=artifact.path,
                strength="artifact_reference",
                metadata={},
            )
            for artifact in artifacts
        ),
    )


def capture_manual(
    workspace: Path,
    *,
    entry_type: str,
    source_type: str,
    occurred: OccurredAt,
    raw_text: str,
    project: str | None = None,
    external_ref: str | None = None,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
) -> RawLogBundle:
    validate_project_label(project)
    authorized_artifacts = _authorized_artifacts(workspace, artifacts)
    recorded_at = (clock or (lambda: datetime.now(timezone.utc)))()
    last_collision: IdCollisionError | None = None
    for _attempt in range(3):
        raw_id = id_factory("raw_log")
        try:
            raw_log = RawLog(
                id=raw_id,
                recorded_at=recorded_at,
                entry_type=entry_type,
                source_type=source_type,
                occurred=occurred,
                raw_text=raw_text,
                project=project,
                external_ref=external_ref,
                corrects_log_id=None,
                metadata={},
            )
            evidence_items = build_capture_evidence_items(
                raw_log_id=raw_id,
                created_at=recorded_at,
                artifacts=authorized_artifacts,
                id_factory=id_factory,
            )
        except (ValidationError, ValueError, TypeError) as error:
            raise _invalid_capture(error) from error
        bundle = RawLogBundle(raw_log, evidence_items)
        try:
            persist_manual_capture(
                workspace,
                raw_log=raw_log,
                evidence_items=evidence_items,
                timeout_ms=timeout_ms,
                after_raw_insert=after_raw_insert,
                # §14.14 rule 6: the pair is durable before the writer lock is
                # released, so a failure in that teardown still owes the
                # envelope these identities.
                on_committed=lambda error: carry_committed(
                    error, capture_outcome(bundle)
                ),
            )
            return bundle
        except IdCollisionError as error:
            last_collision = error
            continue
    raise IdCollisionError() from last_collision


def capture_daily(
    workspace: Path,
    *,
    raw_text: str,
    project: str | None = None,
    external_ref: str | None = None,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
) -> RawLogBundle:
    validate_project_label(project)
    # Fail closed before reading configuration or owner content (§12.14).
    require_compatible(workspace)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    config = load_workspace_config(workspace)
    occurred = today_occurred(now=now, timezone_name=require_timezone(config))
    return capture_manual(
        workspace,
        entry_type="manual_daily",
        source_type="manual_entry",
        occurred=occurred,
        raw_text=raw_text,
        project=project,
        external_ref=external_ref,
        artifacts=artifacts,
        clock=lambda: now,
        id_factory=id_factory,
        timeout_ms=timeout_ms,
        after_raw_insert=after_raw_insert,
    )


def capture_daily_file(
    workspace: Path,
    *,
    source_path: str,
    project: str | None = None,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
) -> RawLogBundle:
    validate_project_label(project)
    # Fail closed before acquiring the private source file (§12.14, §22);
    # the local-time contract gates source acquisition too (§14.14).
    require_compatible(workspace)
    config = load_workspace_config(workspace)
    workspace_zone(require_timezone(config))
    raw_text, external_ref = read_capture_file(source_path, config=config)
    return capture_daily(
        workspace,
        raw_text=raw_text,
        project=project,
        external_ref=external_ref,
        artifacts=artifacts,
        clock=clock,
        id_factory=id_factory,
        timeout_ms=timeout_ms,
        after_raw_insert=after_raw_insert,
    )


def capture_retro(
    workspace: Path,
    *,
    occurred: OccurredAt,
    raw_text: str,
    project: str | None = None,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
) -> RawLogBundle:
    validate_project_label(project)
    require_compatible(workspace)
    return capture_manual(
        workspace,
        entry_type="manual_retro",
        source_type="user_memory",
        occurred=occurred,
        raw_text=raw_text,
        project=project,
        artifacts=artifacts,
        clock=clock,
        id_factory=id_factory,
        timeout_ms=timeout_ms,
        after_raw_insert=after_raw_insert,
    )


def capture_retro_file(
    workspace: Path,
    *,
    source_path: str,
    occurred: OccurredAt,
    project: str | None = None,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
) -> RawLogBundle:
    """Acquire retrospective text after compatibility and timezone gates."""

    validate_project_label(project)
    require_compatible(workspace)
    config = load_workspace_config(workspace)
    workspace_zone(require_timezone(config))
    raw_text, external_ref = read_capture_file(source_path, config=config)
    return capture_manual(
        workspace,
        entry_type="manual_retro",
        source_type="user_memory",
        occurred=occurred,
        raw_text=raw_text,
        project=project,
        external_ref=external_ref,
        artifacts=artifacts,
        clock=clock,
        id_factory=id_factory,
        timeout_ms=timeout_ms,
        after_raw_insert=after_raw_insert,
    )


def _select_answerable_gap(connection, gap_id: str):
    gap = get_gap_question(connection, gap_id, current_only=True)
    if gap is None:
        raise SelectorNotFoundError()
    if gap.answered:
        raise GapAlreadyAnsweredError()
    return gap


def validate_gap_answer_selection(workspace: Path, *, gap_id: str) -> None:
    """Resolve the selector before answer acquisition (§14.14 rule 4)."""

    require_compatible(workspace)
    with read_database(workspace) as connection:
        _select_answerable_gap(connection, gap_id)


def _gap_answer_is_durable(connection, gap_id: str) -> bool | None:
    """Answer from the database whether the transition survived, or None.

    `commit()` can raise after SQLite has already made the transaction
    durable, so the caller reads the answered gap rather than a flag. The two
    duties that consult it default in opposite directions, which is why
    unknown is its own answer rather than either boolean.
    """

    try:
        if connection.in_transaction:
            return False
        row = connection.execute(
            "SELECT answered FROM gap_questions WHERE id = ?", (gap_id,)
        ).fetchone()
        return bool(row and row[0])
    except Exception:
        return None


def capture_gap_answer(
    workspace: Path,
    *,
    gap_id: str,
    raw_text: str,
    external_ref: str | None = None,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> RawLogBundle:
    """Persist the answer bundle and gap transition in one transaction."""

    require_compatible(workspace)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    config = load_workspace_config(workspace)
    occurred = today_occurred(now=now, timezone_name=require_timezone(config))
    authorized_artifacts = authorize_artifact_locators(artifacts, config=config)
    last_collision: IdCollisionError | None = None
    # §14.14 rule 6: set once the answer is provably durable, so every exit
    # below — the managed cleanup, the connection close, the lock release —
    # reports the pair rather than cancelling as though nothing had happened.
    answered: Outcome | None = None

    try:
        with writer_database(workspace, timeout_ms=timeout_ms) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                gap = _select_answerable_gap(connection, gap_id)
                for attempt in range(3):
                    raw_id = id_factory("raw_log")
                    try:
                        raw_log = RawLog(
                            id=raw_id,
                            recorded_at=now,
                            entry_type="gap_answer",
                            source_type="manual_entry",
                            occurred=occurred,
                            raw_text=raw_text,
                            project=None,
                            external_ref=external_ref,
                            corrects_log_id=None,
                            metadata={
                                "question_text": gap.question,
                                "question_reason": gap.reason,
                            },
                        )
                        evidence_items = build_capture_evidence_items(
                            raw_log_id=raw_id,
                            created_at=now,
                            artifacts=authorized_artifacts,
                            id_factory=id_factory,
                        )
                    except (ValidationError, ValueError, TypeError) as error:
                        raise _invalid_capture(error) from error

                    savepoint = f"gap_answer_{attempt}"
                    connection.execute(f"SAVEPOINT {savepoint}")
                    try:
                        insert_raw_log(connection, raw_log)
                        for evidence_item in evidence_items:
                            insert_evidence_item(connection, evidence_item)
                        mark_gap_answered(
                            connection, gap_id=gap.id, answer_log_id=raw_log.id
                        )
                    except IdCollisionError as error:
                        connection.execute(f"ROLLBACK TO {savepoint}")
                        connection.execute(f"RELEASE {savepoint}")
                        last_collision = error
                        continue
                    connection.execute(f"RELEASE {savepoint}")
                    # §13 stale-export trigger: answering a gap keeps every view
                    # current, but its rendered answer state changed. The current
                    # snapshot sets are enumerated and reported pending before
                    # COMMIT, so an interrupt in the commit-to-cleanup window
                    # still reports the retained stale set; rollback withdraws
                    # the report. Cleanup failure never rolls the answer back.
                    snapshot_ids = tuple(
                        row[0]
                        for row in connection.execute(
                            "SELECT id FROM assessment_snapshots "
                            "WHERE superseded_at IS NULL ORDER BY CAST(id AS BLOB)"
                        )
                    )
                    pending = assessment_set_paths(workspace, snapshot_ids)
                    try:
                        report_managed_residuals(pending)
                        defer_interrupt()
                        connection.commit()
                    except BaseException:
                        # Two duties with opposite defaults. A reported stale set
                        # is withdrawn only on a proven rollback, because a
                        # spurious residual is recoverable and a dropped one is
                        # not. An identity is named only on a proven commit,
                        # because rule 6 owes the envelope a commit and never an
                        # attempt. Unknown therefore keeps the report and names
                        # nothing.
                        durable = _gap_answer_is_durable(connection, gap.id)
                        if durable is False:
                            withdraw_managed_residuals(pending)
                        elif durable:
                            answered = capture_outcome(
                                RawLogBundle(raw_log, evidence_items)
                            )
                        raise
                    answered = capture_outcome(
                        RawLogBundle(raw_log, evidence_items)
                    )
                    residuals = remove_managed_sets_for_locked_database(
                        workspace,
                        snapshot_ids=snapshot_ids,
                    )
                    bundle = RawLogBundle(raw_log, evidence_items, residuals)
                    # The teardown below can still fail, so the report it would
                    # carry names what the cleanup could not resolve as well.
                    answered = capture_outcome(bundle)
                    return bundle
                raise IdCollisionError() from last_collision
            except sqlite3.OperationalError as error:
                connection.rollback()
                if "locked" in str(error).lower() or "busy" in str(error).lower():
                    raise WorkspaceBusyError() from error
                raise
            except BaseException:
                connection.rollback()
                raise
    except BaseException as error:
        # The writer teardown raises outside the block above — a lock the
        # platform could not release, a connection that would not close — and
        # rule 6 owes the envelope the durable pair on that exit too.
        if answered is not None:
            carry_committed(error, answered)
        raise


def capture_gap_answer_file(
    workspace: Path,
    *,
    gap_id: str,
    source_path: str,
    artifacts: tuple[str, ...] = (),
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> RawLogBundle:
    """Acquire a local answer file only after compatibility/timezone gates."""

    require_compatible(workspace)
    config = load_workspace_config(workspace)
    workspace_zone(require_timezone(config))
    raw_text, external_ref = read_capture_file(source_path, config=config)
    return capture_gap_answer(
        workspace,
        gap_id=gap_id,
        raw_text=raw_text,
        external_ref=external_ref,
        artifacts=artifacts,
        clock=clock,
        id_factory=id_factory,
        timeout_ms=timeout_ms,
    )


def capture_outcome(bundle) -> Outcome:
    evidence_ids = [item.id for item in bundle.evidence_items]
    return Outcome(
        affected_ids=AffectedIds.of(
            created=(
                ("evidence_item", evidence_ids),
                ("raw_log", [bundle.raw_log.id]),
            )
        ),
        # A capture that cleaned up managed output and could not finish carries
        # those paths; §14.14 rule 5 wants them on the failed and cancelled
        # envelopes this projection becomes, not only on the successful one.
        residual_paths=list(bundle.residual_paths),
        human_result=(
            f"Created raw log {bundle.raw_log.id} with evidence "
            f"{', '.join(evidence_ids)}."
        ),
    )
