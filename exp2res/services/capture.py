"""Manual daily and retrospective capture services."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
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
    InvalidInputError,
    SelectorNotFoundError,
)
from exp2res.exports.managed import (
    assessment_set_paths,
    remove_managed_sets_for_locked_database,
)
from exp2res.pipeline.stage1 import FailureHook, persist_manual_capture
from exp2res.services.privacy import table_ids
from exp2res.services.writers import operation, retry_id_collisions, savepoint
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


def invalid_capture(
    error: BaseException, message: str = "Manual capture failed strict validation."
) -> InvalidInputError:
    failure = InvalidInputError()
    failure.diagnostic_class = "capture_validation_failed"
    failure.public_message = message
    failure.__cause__ = error
    return failure


def validate_project_label(project: str | None) -> None:
    # §12 rule 14: a non-null blank project label is rejected at acquisition.
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


def build_capture_pair(
    *,
    recorded_at: datetime,
    artifacts: tuple[ArtifactLocator, ...],
    id_factory: IdFactory,
    message: str = "Manual capture failed strict validation.",
    **fields,
) -> tuple[RawLog, tuple[EvidenceItem, ...]]:
    """Allocate the raw-log ID and build its §13.1 pair; class 2 on strict failure."""

    raw_id = id_factory("raw_log")
    try:
        raw_log = RawLog(id=raw_id, recorded_at=recorded_at, **fields)
        evidence_items = build_capture_evidence_items(
            raw_log_id=raw_id,
            created_at=recorded_at,
            artifacts=artifacts,
            id_factory=id_factory,
        )
    except (ValidationError, ValueError, TypeError) as error:
        raise invalid_capture(error, message) from error
    return raw_log, evidence_items


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

    def attempt(_index: int) -> RawLogBundle:
        raw_log, evidence_items = build_capture_pair(
            recorded_at=recorded_at,
            artifacts=authorized_artifacts,
            id_factory=id_factory,
            entry_type=entry_type,
            source_type=source_type,
            occurred=occurred,
            raw_text=raw_text,
            project=project,
            external_ref=external_ref,
            corrects_log_id=None,
            metadata={},
        )
        bundle = RawLogBundle(raw_log, evidence_items)
        persist_manual_capture(
            workspace,
            raw_log=raw_log,
            evidence_items=evidence_items,
            timeout_ms=timeout_ms,
            after_raw_insert=after_raw_insert,
            # §14.14 rule 6: a failing lock teardown still reports the pair.
            on_committed=lambda error: carry_committed(error, capture_outcome(bundle)),
        )
        return bundle

    return retry_id_collisions(attempt)


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
    # §12.14: fail closed before reading configuration or owner content.
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
    # §12.14, §14.14: compatibility and timezone gate source acquisition.
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
    # §14.14 rule 4: resolve the selector before answer acquisition.
    require_compatible(workspace)
    with read_database(workspace) as connection:
        _select_answerable_gap(connection, gap_id)


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
    require_compatible(workspace)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    config = load_workspace_config(workspace)
    occurred = today_occurred(now=now, timezone_name=require_timezone(config))
    authorized_artifacts = authorize_artifact_locators(artifacts, config=config)
    pending: tuple[str, ...] = ()
    bundle: RawLogBundle | None = None
    journal = None

    try:
        # §14.14 rule 6: rollback withdraws the pending stale-set report; a
        # commit keeps it through the commit-to-cleanup window.
        with operation(
            writer_database(workspace, timeout_ms=timeout_ms),
            on_rollback=lambda: withdraw_managed_residuals(pending),
        ) as op:
            journal = op.journal
            connection = op.connection
            gap = _select_answerable_gap(connection, gap_id)

            def attempt(index: int) -> tuple[RawLog, tuple[EvidenceItem, ...]]:
                raw_log, evidence_items = build_capture_pair(
                    recorded_at=now,
                    artifacts=authorized_artifacts,
                    id_factory=id_factory,
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

                def insert() -> None:
                    insert_raw_log(connection, raw_log)
                    for evidence_item in evidence_items:
                        insert_evidence_item(connection, evidence_item)
                    mark_gap_answered(
                        connection, gap_id=gap.id, answer_log_id=raw_log.id
                    )

                savepoint(connection, f"gap_answer_{index}", insert)
                return raw_log, evidence_items

            raw_log, evidence_items = retry_id_collisions(attempt)
            # §13 stale-export trigger: stale sets are reported pending before
            # COMMIT so the commit-to-cleanup window still reports them.
            snapshot_ids = table_ids(
                connection, "assessment_snapshots", "superseded_at IS NULL"
            )
            pending = assessment_set_paths(workspace, snapshot_ids)
            report_managed_residuals(pending)
            bundle = RawLogBundle(raw_log, evidence_items)
            op.after_commit(
                lambda: remove_managed_sets_for_locked_database(
                    workspace,
                    snapshot_ids=snapshot_ids,
                    removed_ledger=op.journal.unlinks,
                ),
                unproven=pending,
            )
            bundle = RawLogBundle(raw_log, evidence_items, op.journal.unresolved)
        return bundle
    except BaseException as error:
        # Rule 6: every exit after the commit — cleanup, teardown — reports the pair.
        journal = getattr(error, "operation_journal", journal)
        if journal is None:
            raise
        if journal.committed and bundle is not None:
            carry_committed(
                error,
                capture_outcome(
                    RawLogBundle(bundle.raw_log, bundle.evidence_items, journal.unresolved)
                ),
            )
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
        # §14.14 rule 5: residual paths ride failed and cancelled envelopes too.
        residual_paths=list(bundle.residual_paths),
        human_result=(
            f"Created raw log {bundle.raw_log.id} with evidence "
            f"{', '.join(evidence_ids)}."
        ),
    )
