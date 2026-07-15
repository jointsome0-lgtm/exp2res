"""Manual daily and retrospective capture services."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from pydantic import ValidationError

from exp2res.config import load_workspace_config, require_timezone
from exp2res.domain.models import EvidenceItem, OccurredAt, RawLog
from exp2res.errors import IdCollisionError, InvalidInputError
from exp2res.pipeline.stage1 import FailureHook, persist_manual_capture
from exp2res.services.source_files import read_capture_file
from exp2res.services.time_input import today_occurred
from exp2res.storage.repository import RawLogBundle
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, require_compatible

IdFactory = Callable[[str], str]
Clock = Callable[[], datetime]


def new_id(kind: str) -> str:
    prefix = "log" if kind == "raw_log" else "evi"
    return f"{prefix}_{uuid4().hex}"


def _invalid_capture(error: BaseException) -> InvalidInputError:
    failure = InvalidInputError()
    failure.diagnostic_class = "capture_validation_failed"
    failure.public_message = "Manual capture failed strict validation."
    failure.__cause__ = error
    return failure


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
    # Fail closed before acquiring the private source file (§12.14, §22).
    require_compatible(workspace)
    config = load_workspace_config(workspace)
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
