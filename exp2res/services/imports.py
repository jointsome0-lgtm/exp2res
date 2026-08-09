"""§14.5 source-local import under §19.4's record and identity semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Optional

from pydantic import ValidationError

from exp2res.config import load_workspace_config
from exp2res.domain.models import EvidenceItem, RawLog
from exp2res.errors import (
    IdCollisionError,
    OperationCancelledError,
    WorkspaceBusyError,
)
from exp2res.integrations import CONTRACTS
from exp2res.integrations.records import (
    ImportPlan,
    ParsedRecord,
    PlanContext,
    RecordRejected,
    SourceContract,
    SourceRecord,
    content_hash,
    parse_payload,
)
from exp2res.services.capture import Clock, IdFactory, new_id
from exp2res.services.source_files import read_payload_file
from exp2res.storage.repository import (
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
    """One §19.4 rule 5 record result, plus its non-result reason code."""

    record_number: int
    source_record_id: Optional[str]
    raw_log_id: Optional[str] = None
    reason: Optional[str] = None
    # §14.14 rule 5's closed projection carries neither of these; they reach
    # the envelope's `affected_ids` and its rejection diagnostic instead.
    evidence_item_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportOutcome:
    """The three §14.5 classes, each in input order, partitioning the input."""

    accepted: tuple[ImportedRecord, ...] = ()
    duplicate: tuple[ImportedRecord, ...] = ()
    rejected: tuple[ImportedRecord, ...] = ()


def _validated_record(
    contract: SourceContract, raw: Mapping[str, Any]
) -> SourceRecord:
    """Hydrate one record through the §11 JSON boundary."""

    return contract.record_model.model_validate_json(
        json.dumps(raw, ensure_ascii=False)
    )


def _import_metadata(
    contract: SourceContract, *, identity: str, digest: str
) -> dict[str, Any]:
    # §19.4 rule 2's three reserved keys are the whole imported metadata
    # object: no V1 source contract declares a pass-through `metadata` field,
    # so there is no source value to merge or to collide with them.
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
    """Commit one record's §13.1 rule 5 pair in its own transaction."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        insert_raw_log(connection, raw_log)
        for evidence_item in evidence_items:
            insert_evidence_item(connection, evidence_item)
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


def _classify(
    connection: sqlite3.Connection,
    parsed: ParsedRecord,
    *,
    contract: SourceContract,
    context: PlanContext,
    retained: dict[str, str],
    recorded_at: datetime,
    id_factory: IdFactory,
) -> tuple[str, ImportedRecord]:
    number = parsed.record_number
    raw = parsed.value
    # §19.4 rule 5 reports the source identity of a rejected record too, so it
    # is recovered from the raw object whenever the identity itself is valid —
    # including for a record the parse already rejected, whose defect may sit
    # anywhere but on its identity. A record that never decoded into a mapping
    # carries whatever the parse could still salvage, and null otherwise.
    identity = (
        contract.raw_identity(raw) if isinstance(raw, dict) else parsed.identity
    )
    if parsed.reason is not None:
        return "rejected", ImportedRecord(number, identity, reason=parsed.reason)
    if not isinstance(raw, dict):
        return "rejected", ImportedRecord(number, None, reason="record_not_object")

    supplied_source = raw.get("source")
    if isinstance(supplied_source, str) and supplied_source != contract.source_system:
        return "rejected", ImportedRecord(
            number, identity, reason="record_source_mismatch"
        )
    try:
        record = _validated_record(contract, raw)
    # An `OverflowError` raised inside validation is this record's own value
    # defect — §11 rule 4 admits offset-aware datetimes with no representable
    # UTC instant (#271) — and Pydantic converts only `ValueError`. Catching
    # it here keeps §19.4 rule 5's per-record outcome a property of the
    # classifier rather than of each contract remembering to translate.
    except (ValidationError, OverflowError):
        return "rejected", ImportedRecord(number, identity, reason="record_invalid")
    try:
        contract.check(record, raw)
    except RecordRejected as rejection:
        return "rejected", ImportedRecord(
            number, identity, reason=rejection.reason
        )

    identity = record.source_identity
    try:
        digest = content_hash(record)
    except OverflowError:
        # §11 rule 4 accepts every offset-aware datetime, but §19.4 rule 3
        # canonicalizes to UTC, where a value near the representable edge has
        # no form at all. That is this one record's defect, and rule 4 keeps
        # it from aborting the records already committed behind it.
        return "rejected", ImportedRecord(
            number, identity, reason="record_invalid"
        )
    stored = retained.get(identity)
    if stored is not None:
        if stored == digest:
            return "duplicate", ImportedRecord(number, identity)
        # Corrected upstream content must arrive under a new identity; the
        # retained rows are never mutated and there is no conflict class.
        return "rejected", ImportedRecord(
            number, identity, reason="content_hash_conflict"
        )

    try:
        plan = contract.plan(record, context)
    except RecordRejected as rejection:
        return "rejected", ImportedRecord(number, identity, reason=rejection.reason)

    last_collision: IdCollisionError | None = None
    for _attempt in range(3):
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
            return "rejected", ImportedRecord(
                number, identity, reason="record_invalid"
            )
        try:
            _persist(connection, raw_log=raw_log, evidence_items=evidence_items)
        except IdCollisionError as error:
            last_collision = error
            continue
        retained[identity] = digest
        return "accepted", ImportedRecord(
            number,
            identity,
            raw_log.id,
            evidence_item_ids=tuple(item.id for item in evidence_items),
        )
    raise IdCollisionError() from last_collision


def import_payload(
    workspace: Path,
    *,
    source_system: str,
    payload_path: str,
    clock: Clock | None = None,
    id_factory: IdFactory = new_id,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ImportOutcome:
    """Import one §19-backed payload record by record under one writer lock."""

    contract = CONTRACTS[source_system]
    # Fail closed before acquiring the owner's payload (§12.14, §14.14).
    require_compatible(workspace)
    config = load_workspace_config(workspace)
    text, payload_root = read_payload_file(payload_path, config=config)
    parsed_records = parse_payload(text, contract=contract)
    context = PlanContext(payload_root=payload_root, config=config)
    recorded_at = (clock or (lambda: datetime.now(timezone.utc)))()

    classified: dict[str, list[ImportedRecord]] = {
        "accepted": [],
        "duplicate": [],
        "rejected": [],
    }

    def report() -> ImportOutcome:
        return ImportOutcome(
            accepted=tuple(classified["accepted"]),
            duplicate=tuple(classified["duplicate"]),
            rejected=tuple(classified["rejected"]),
        )

    with writer_database(workspace, timeout_ms=timeout_ms) as connection:
        try:
            # One scan under the §8.1 writer lock: no other writer can add an
            # imported row while this command runs, so the map stays exact as
            # each accepted record extends it.
            retained = retained_import_hashes(connection, contract.source_system)
            for parsed in parsed_records:
                outcome, result = _classify(
                    connection,
                    parsed,
                    contract=contract,
                    context=context,
                    retained=retained,
                    recorded_at=recorded_at,
                    id_factory=id_factory,
                )
                classified[outcome].append(result)
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except KeyboardInterrupt:
            # §14.14 rule 6: rule 4 commits each accepted record in its own
            # transaction, so those records are lifecycle boundaries that
            # remain committed and are reported rather than restored. The
            # records the interrupt never reached leave the classification
            # incomplete, so the caller reports no primary result.
            cancelled = OperationCancelledError()
            cancelled.import_outcome = report()
            raise cancelled from None
    return report()
