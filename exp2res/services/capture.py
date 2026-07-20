"""Manual daily and retrospective capture services."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4

from pydantic import ValidationError

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
from exp2res.pipeline.stage1 import FailureHook, persist_manual_capture
from exp2res.services.source_files import read_capture_file
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
    require_compatible,
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
        "signal": "signal",
        "snapshot": "snapshot",
        "claim": "claim",
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


def capture_manual(
    workspace: Path,
    *,
    entry_type: str,
    source_type: str,
    occurred: OccurredAt,
    raw_text: str,
    project: str | None = None,
    external_ref: str | None = None,
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
) -> RawLogBundle:
    validate_project_label(project)
    recorded_at = (clock or (lambda: datetime.now(timezone.utc)))()
    last_collision: IdCollisionError | None = None
    for _attempt in range(3):
        raw_id = id_factory("raw_log")
        evidence_id = id_factory("evidence_item")
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
            evidence_item = EvidenceItem(
                id=evidence_id,
                created_at=recorded_at,
                raw_log_id=raw_id,
                title=None,
                summary="Owner-authored manual claim.",
                uri=None,
                path=None,
                strength="manual_claim",
                metadata={},
            )
        except (ValidationError, ValueError, TypeError) as error:
            raise _invalid_capture(error) from error
        try:
            persist_manual_capture(
                workspace,
                raw_log=raw_log,
                evidence_item=evidence_item,
                timeout_ms=timeout_ms,
                after_raw_insert=after_raw_insert,
            )
            return RawLogBundle(raw_log, (evidence_item,))
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
    """Resolve the selector before answer acquisition (§14.14 rule 3)."""

    require_compatible(workspace)
    with read_database(workspace) as connection:
        _select_answerable_gap(connection, gap_id)


def capture_gap_answer(
    workspace: Path,
    *,
    gap_id: str,
    raw_text: str,
    external_ref: str | None = None,
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> RawLogBundle:
    """Persist the answer bundle and gap transition in one transaction."""

    require_compatible(workspace)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    config = load_workspace_config(workspace)
    occurred = today_occurred(now=now, timezone_name=require_timezone(config))
    last_collision: IdCollisionError | None = None

    with writer_database(workspace, timeout_ms=timeout_ms) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            gap = _select_answerable_gap(connection, gap_id)
            for attempt in range(3):
                raw_id = id_factory("raw_log")
                evidence_id = id_factory("evidence_item")
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
                    evidence_item = EvidenceItem(
                        id=evidence_id,
                        created_at=now,
                        raw_log_id=raw_id,
                        title=None,
                        summary="Owner-authored manual claim.",
                        uri=None,
                        path=None,
                        strength="manual_claim",
                        metadata={},
                    )
                except (ValidationError, ValueError, TypeError) as error:
                    raise _invalid_capture(error) from error

                savepoint = f"gap_answer_{attempt}"
                connection.execute(f"SAVEPOINT {savepoint}")
                try:
                    insert_raw_log(connection, raw_log)
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
                # §13's stale-export trigger is vacuous in schema v4: no
                # managed snapshot or branch export tables exist yet.
                connection.commit()
                return RawLogBundle(raw_log, (evidence_item,))
            raise IdCollisionError() from last_collision
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except BaseException:
            connection.rollback()
            raise


def capture_gap_answer_file(
    workspace: Path,
    *,
    gap_id: str,
    source_path: str,
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
        clock=clock,
        id_factory=id_factory,
        timeout_ms=timeout_ms,
    )
