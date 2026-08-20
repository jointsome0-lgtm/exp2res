"""§14.5 source-local import under §19.4."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Optional

from pydantic import ValidationError

from exp2res.domain.results import (
    AffectedIds,
    carry_committed,
    ImportCounts,
    ImportRecordGroups,
    ImportRecordResult,
    ImportResult,
    Outcome,
)
from exp2res.config import load_workspace_config
from exp2res.domain.models import EvidenceItem, OccurredAt, RawLog
from exp2res.errors import (
    Exp2ResError,
    IdCollisionError,
    ImportDocumentInvalidError,
    OperationCancelledError,
    WorkspaceBusyError,
)
from exp2res.integrations import CONTRACTS
from exp2res.integrations.records import (
    ImportPlan,
    ParsedRecord,
    PayloadRecords,
    PlanContext,
    RecordRejected,
    SourceContract,
    SourceRecord,
    content_hash,
)
from exp2res.pipeline.stage1 import persist_manual_capture
from exp2res.services.interrupts import defer_interrupt
from exp2res.services.capture import (
    Clock,
    IdFactory,
    capture_outcome,
    new_id,
    validate_project_label,
)
from exp2res.services.source_files import open_payload_file, read_document_file
from exp2res.services.writers import is_busy, operation, retry_id_collisions
from exp2res.storage.repository import (
    RawLogBundle,
    insert_evidence_item,
    insert_raw_log,
    retained_import_hashes,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    require_compatible,
    writer_database,
)


@dataclass(frozen=True)
class ImportedRecord:
    """One §19.4 rule 5 record result."""

    record_number: int
    source_record_id: Optional[str]
    raw_log_id: Optional[str] = None
    reason: Optional[str] = None
    # Not in §14.14 rule 5's projection.
    evidence_item_ids: tuple[str, ...] = ()
    # §19.4 rule 3 hash: identifies this record's row after an interrupt.
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class ImportOutcome:
    """The three §14.5 classes, each in input order."""

    accepted: tuple[ImportedRecord, ...] = ()
    duplicate: tuple[ImportedRecord, ...] = ()
    rejected: tuple[ImportedRecord, ...] = ()


def _validated_record(
    contract: SourceContract, raw: Mapping[str, Any]
) -> SourceRecord:
    return contract.record_model.model_validate_json(
        json.dumps(raw, ensure_ascii=False)
    )


def _import_metadata(
    contract: SourceContract, *, identity: str, digest: str
) -> dict[str, Any]:
    # §19.4 rule 2: the whole metadata object.
    return {
        "source_system": contract.source_system,
        "source_record_id": identity,
        "content_hash": digest,
    }


def _build_bundle(
    plan: ImportPlan,
    *,
    contract: SourceContract,
    identity: str,
    digest: str,
    recorded_at: datetime,
    id_factory: IdFactory,
) -> tuple[RawLog, tuple[EvidenceItem, ...]]:
    raw_log = RawLog(
        id=id_factory("raw_log"),
        recorded_at=recorded_at,
        entry_type=plan.entry_type,
        source_type=plan.source_type,
        occurred=plan.occurred,
        raw_text=plan.raw_text,
        project=plan.project,
        external_ref=plan.external_ref,
        corrects_log_id=None,
        metadata=_import_metadata(contract, identity=identity, digest=digest),
    )
    evidence_items = tuple(
        EvidenceItem(
            id=id_factory("evidence_item"),
            created_at=recorded_at,
            raw_log_id=raw_log.id,
            title=None,
            summary=item.summary,
            uri=item.uri,
            path=item.path,
            strength=item.strength,
            metadata=dict(item.metadata),
        )
        for item in plan.evidence
    )
    return raw_log, evidence_items


def _persist(
    connection: sqlite3.Connection,
    *,
    raw_log: RawLog,
    evidence_items: tuple[EvidenceItem, ...],
) -> None:
    """§13.1 rule 5 pair, one transaction per record (§19.4 rule 4)."""
    with operation(nullcontext(connection)):
        insert_raw_log(connection, raw_log)
        for evidence_item in evidence_items:
            insert_evidence_item(connection, evidence_item)


def _classify(
    connection: sqlite3.Connection,
    parsed: ParsedRecord,
    *,
    contract: SourceContract,
    context: PlanContext,
    retained: dict[str, str],
    recorded_at: datetime,
    id_factory: IdFactory,
    classified: dict[str, list[ImportedRecord]],
) -> tuple[str, ImportedRecord]:
    def bank(outcome: str, result: ImportedRecord) -> tuple[str, ImportedRecord]:
        # §14.14 rule 5: banked where decided — a non-accepted record leaves no row to recover.
        classified[outcome].append(result)
        return outcome, result

    number = parsed.record_number
    raw = parsed.value
    # §19.4 rule 5: a rejected record still reports a valid identity.
    identity = (
        contract.raw_identity(raw) if isinstance(raw, dict) else parsed.identity
    )
    if parsed.reason is not None:
        return bank("rejected", ImportedRecord(number, identity, reason=parsed.reason))
    if not isinstance(raw, dict):
        return bank(
            "rejected", ImportedRecord(number, None, reason="record_not_object")
        )

    supplied_source = raw.get("source")
    if isinstance(supplied_source, str) and supplied_source != contract.source_system:
        return bank(
            "rejected",
            ImportedRecord(number, identity, reason="record_source_mismatch"),
        )
    try:
        record = _validated_record(contract, raw)
    # §11 rule 54 / §19.4 rule 5: no known input, kept so one escape cannot abort the import.
    except (ValidationError, OverflowError):
        return bank(
            "rejected",
            ImportedRecord(number, identity, reason="record_invalid"),
        )
    try:
        contract.check(record, raw)
    except RecordRejected as rejection:
        return bank(
            "rejected",
            ImportedRecord(number, identity, reason=rejection.reason),
        )

    identity = record.source_identity
    try:
        digest = content_hash(record)
    except OverflowError:
        return bank(
            "rejected",
            ImportedRecord(number, identity, reason="record_invalid"),
        )
    stored = retained.get(identity)
    if stored is not None:
        if stored == digest:
            return bank("duplicate", ImportedRecord(number, identity))
        # Retained rows are never mutated.
        return bank(
            "rejected",
            ImportedRecord(number, identity, reason="content_hash_conflict"),
        )

    try:
        plan = contract.plan(record, context)
    except RecordRejected as rejection:
        return bank(
            "rejected",
            ImportedRecord(number, identity, reason=rejection.reason),
        )

    def attempt(_index: int) -> ImportedRecord:
        try:
            raw_log, evidence_items = _build_bundle(
                plan,
                contract=contract,
                identity=identity,
                digest=digest,
                recorded_at=recorded_at,
                id_factory=id_factory,
            )
        except (ValidationError, ValueError, TypeError, OverflowError):
            return bank(
                "rejected",
                ImportedRecord(number, identity, reason="record_invalid"),
            )
        candidate = ImportedRecord(
            number,
            identity,
            raw_log.id,
            evidence_item_ids=tuple(item.id for item in evidence_items),
            content_hash=digest,
        )
        accepted = classified["accepted"]
        try:
            accepted.append(candidate)
            _persist(connection, raw_log=raw_log, evidence_items=evidence_items)
        except BaseException as error:
            journal = getattr(error, "operation_journal", None)
            if (journal is None or not journal.committed) and candidate in accepted:
                accepted.remove(candidate)
            raise
        retained[identity] = digest
        return "accepted", candidate

    return retry_id_collisions(attempt)


def import_design_document(
    workspace: Path,
    *,
    source_path: str,
    project: str | None = None,
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> RawLogBundle:
    """§14.5 `import file`: a §13.1 rule 5 capture pair, not a §19 record."""

    validate_project_label(project)
    # §12.14: fail closed before reading the document.
    require_compatible(workspace)
    config = load_workspace_config(workspace)
    raw_text, canonical_path = read_document_file(source_path, config=config)
    recorded_at = (clock or (lambda: datetime.now(timezone.utc)))()

    def attempt(_index: int) -> RawLogBundle:
        raw_id = id_factory("raw_log")
        try:
            raw_log = RawLog(
                id=raw_id,
                recorded_at=recorded_at,
                entry_type="design_doc",
                source_type="imported_artifact",
                # §13.1 rule 3: no occurrence from file metadata.
                occurred=OccurredAt(
                    start=None,
                    end=None,
                    precision="unknown",
                    confidence="unknown",
                ),
                raw_text=raw_text,
                project=project,
                external_ref=canonical_path,
                corrects_log_id=None,
                metadata={},
            )
            evidence_items = (
                EvidenceItem(
                    id=id_factory("evidence_item"),
                    created_at=recorded_at,
                    raw_log_id=raw_id,
                    title=None,
                    summary="Imported local design document.",
                    uri=None,
                    path=canonical_path,
                    strength="design_doc",
                    metadata={},
                ),
            )
        except (ValidationError, ValueError, TypeError) as error:
            raise ImportDocumentInvalidError() from error
        bundle = RawLogBundle(raw_log, evidence_items)
        persist_manual_capture(
            workspace,
            raw_log=raw_log,
            evidence_items=evidence_items,
            timeout_ms=timeout_ms,
            # §14.14 rule 6: report the durable pair.
            on_committed=lambda error: carry_committed(error, capture_outcome(bundle)),
        )
        return bundle

    return retry_id_collisions(attempt)


def import_payload(
    workspace: Path,
    *,
    source_system: str,
    payload_path: str,
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ImportOutcome:
    """Import one §19 payload record by record under one lock."""

    contract = CONTRACTS[source_system]
    # §12.14: fail closed before reading the payload.
    require_compatible(workspace)
    config = load_workspace_config(workspace)

    records: PayloadRecords | None = None
    classified: dict[str, list[ImportedRecord]] = {
        "accepted": [],
        "duplicate": [],
        "rejected": [],
    }

    def report() -> ImportOutcome:
        # §14.14 rule 5: banked where decided, so the classification is the report.
        return ImportOutcome(
            accepted=tuple(classified["accepted"]),
            duplicate=tuple(classified["duplicate"]),
            rejected=tuple(classified["rejected"]),
        )

    def attach(error: Exp2ResError) -> Exp2ResError:
        outcome = error.operation_result = report()
        # §14.14 rules 4–5: derived from the outcome, not set by a statement a signal could precede.
        error.import_classified = records is not None and len(outcome.accepted) + len(
            outcome.duplicate
        ) + len(outcome.rejected) == records.total
        return error

    try:
        with open_payload_file(payload_path, config=config) as (payload, root):
            records = PayloadRecords(payload, contract=contract)
            context = PlanContext(payload_root=root, config=config)
            recorded_at = (clock or (lambda: datetime.now(timezone.utc)))()
            # Before the §8.1 lock: refused with nothing committed.
            replay = iter(records)
            with writer_database(workspace, timeout_ms=timeout_ms) as connection:
                # One scan under the §8.1 lock, extended as records are accepted.
                retained = retained_import_hashes(connection, contract.source_system)
                for parsed in replay:
                    _classify(
                        connection,
                        parsed,
                        contract=contract,
                        context=context,
                        retained=retained,
                        recorded_at=recorded_at,
                        id_factory=id_factory,
                        classified=classified,
                    )
    except KeyboardInterrupt:
        # §14.14 rule 6: reported, not restored.
        raise attach(OperationCancelledError()) from None
    except sqlite3.OperationalError as error:
        if is_busy(error):
            raise attach(WorkspaceBusyError()) from error
        # Class 1, but the base class keeps §19.4 rule 4's rows reportable.
        raise attach(Exp2ResError()) from error
    except Exp2ResError as error:
        # §19.4 rule 4: a failure never withdraws an accepted record.
        raise attach(error)
    except Exception as error:
        # §14.14 rule 6: a teardown failure still reports.
        raise attach(Exp2ResError()) from error
    # §14.14 rule 6: durable and unreported, hold delivery.
    defer_interrupt()
    return report()


def import_record_line(record: ImportedRecord, outcome_name: str) -> str:
    return (
        f"{record.record_number}\t{outcome_name}\t"
        f"{record.source_record_id or '-'}\t{record.raw_log_id or '-'}"
    )


def import_created(imported: ImportOutcome) -> AffectedIds:
    # §14.14 rule 5: by identity, not input order.
    return AffectedIds.of(
        created=(
            (
                "evidence_item",
                [
                    item_id
                    for record in imported.accepted
                    for item_id in record.evidence_item_ids
                ],
            ),
            (
                "raw_log",
                [
                    record.raw_log_id
                    for record in imported.accepted
                    if record.raw_log_id is not None
                ],
            ),
        )
    )


def import_result(imported: ImportOutcome) -> ImportResult:
    return ImportResult(
        counts=ImportCounts(
            accepted=len(imported.accepted),
            duplicate=len(imported.duplicate),
            rejected=len(imported.rejected),
        ),
        records=ImportRecordGroups(
            accepted=import_record_group(imported.accepted),
            duplicate=import_record_group(imported.duplicate),
            rejected=import_record_group(imported.rejected),
        ),
    )


def import_outcome(imported: ImportOutcome) -> Outcome:
    # §14.14 rule 5: local stdout is exempt from §11's list caps.
    result = import_result(imported)
    lines = [
        f"accepted {result.counts.accepted}, "
        f"duplicate {result.counts.duplicate}, "
        f"rejected {result.counts.rejected}"
    ]
    for outcome_name, group in (
        ("accepted", imported.accepted),
        ("duplicate", imported.duplicate),
        ("rejected", imported.rejected),
    ):
        lines.extend(import_record_line(record, outcome_name) for record in group)
    # §14.14 rule 5: class 2 with the complete result.
    rejected = result.counts.rejected > 0
    return Outcome(
        exit_code=2 if rejected else 0,
        diagnostic_class="import_records_rejected" if rejected else None,
        affected_ids=import_created(imported),
        result=result,
        # §14.14 rule 4: a completion; incomplete cleanup promotes to class 8.
        completed_report=True,
        human_result="\n".join(lines),
    )


def import_record_group(
    records: tuple[ImportedRecord, ...],
) -> list[ImportRecordResult]:
    return [
        ImportRecordResult(
            record_number=record.record_number,
            source_record_id=record.source_record_id,
            raw_log_id=record.raw_log_id,
        )
        for record in records
    ]
