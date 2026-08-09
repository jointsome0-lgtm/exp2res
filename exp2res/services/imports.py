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
    Exp2ResError,
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
    committed_import_records,
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
    # §19.4 rule 3's hash, kept beside the identity so an interrupted run can
    # ask the workspace whether a stored row is this record (§19.4 rule 2).
    content_hash: Optional[str] = None


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
        # Inside the guard: a signal during the commit itself must leave the
        # transaction resolved, never open behind a raised exception where a
        # later read could mistake its rows for a committed record.
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _classify(
    connection: sqlite3.Connection,
    parsed: ParsedRecord,
    *,
    contract: SourceContract,
    context: PlanContext,
    retained: dict[str, str],
    recorded_at: datetime,
    id_factory: IdFactory,
    attempted: list[ImportedRecord],
    classified: dict[str, list[ImportedRecord]],
) -> tuple[str, ImportedRecord]:
    def bank(outcome: str, result: ImportedRecord) -> tuple[str, ImportedRecord]:
        """Record one §19.4 rule 5 classification where it is decided.

        A `duplicate` or `rejected` record leaves no row for
        `committed_import_records` to recover, so banking it in the caller
        would leave a window in which a signal discards a classification the
        record had already earned (§14.14 rule 5).
        """

        classified[outcome].append(result)
        return outcome, result

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
    # An `OverflowError` raised inside validation is this record's own value
    # defect — §11 rule 4 admits offset-aware datetimes with no representable
    # UTC instant (#271) — and Pydantic converts only `ValueError`. Catching
    # it here keeps §19.4 rule 5's per-record outcome a property of the
    # classifier rather than of each contract remembering to translate.
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
        # §11 rule 4 accepts every offset-aware datetime, but §19.4 rule 3
        # canonicalizes to UTC, where a value near the representable edge has
        # no form at all. That is this one record's defect, and rule 4 keeps
        # it from aborting the records already committed behind it.
        return bank(
            "rejected",
            ImportedRecord(number, identity, reason="record_invalid"),
        )
    stored = retained.get(identity)
    if stored is not None:
        if stored == digest:
            return bank("duplicate", ImportedRecord(number, identity))
        # Corrected upstream content must arrive under a new identity; the
        # retained rows are never mutated and there is no conflict class.
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
        # Recorded before the transaction opens: a signal can land between
        # `_persist`'s commit and any bookkeeping after it, and the workspace
        # is then the only authority on whether this candidate committed.
        attempted.append(candidate)
        try:
            _persist(connection, raw_log=raw_log, evidence_items=evidence_items)
        except IdCollisionError as error:
            # This candidate's ID belongs to a row that already existed, so
            # leaving it registered would name a row this payload never wrote.
            # A signal can outrun this line while `_persist` unwinds, so it is
            # the cheap path, not the guarantee: `committed_import_records`
            # matches the stored identity and hash and rejects it either way.
            attempted.pop()
            last_collision = error
            continue
        retained[identity] = digest
        return bank("accepted", candidate)
    raise IdCollisionError() from last_collision


def _cancelled_report(
    connection: sqlite3.Connection,
    *,
    attempted: list[ImportedRecord],
    classified: dict[str, list[ImportedRecord]],
) -> ImportOutcome:
    """Report exactly the records whose transaction reached the database.

    The in-memory classification is not the authority: a signal can arrive
    after `_persist` commits and before the loop files the record, which
    would drop a durable §14.14 rule 6 boundary from the report. Every
    candidate this payload tried to persist is therefore checked against the
    workspace under the still held writer lock. A failed read leaves the
    classified accepted records as the reported set, which is a subset of
    what committed and never invents one.
    """

    try:
        if connection.in_transaction:
            # An open transaction at this point is by definition uncommitted:
            # §19.4 rule 4 gives each record its own, and the signal outran
            # its resolution. Resolving it here keeps the query's visibility
            # a statement about committed rows.
            connection.rollback()
        keys = [
            (record.raw_log_id or "", record.source_record_id, record.content_hash)
            for record in attempted
        ]
        committed = committed_import_records(connection, keys)
    except sqlite3.Error:
        accepted = tuple(classified["accepted"])
    else:
        # Matched per candidate, not per ID: two candidates in one run can
        # hold the same generated ID, and only one of them wrote the row.
        accepted = tuple(
            record for record, key in zip(attempted, keys) if key in committed
        )
    return ImportOutcome(
        accepted=accepted,
        duplicate=tuple(classified["duplicate"]),
        rejected=tuple(classified["rejected"]),
    )


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
    attempted: list[ImportedRecord] = []

    def is_complete(outcome: ImportOutcome) -> bool:
        """Do this outcome's three lists partition every parsed record?

        §14.14 rule 5's complete primary result is a property of the outcome
        being reported, so it is derived from that outcome rather than written
        by a separate statement a signal could land in front of. A record the
        loop never reached and a record whose commit the signal outran both
        leave the same gap, and both mean `result = null` (§14.14 rule 4).
        """

        return len(outcome.accepted) + len(outcome.duplicate) + len(
            outcome.rejected
        ) == len(parsed_records)

    def report() -> ImportOutcome:
        return ImportOutcome(
            accepted=tuple(classified["accepted"]),
            duplicate=tuple(classified["duplicate"]),
            rejected=tuple(classified["rejected"]),
        )

    try:
        with writer_database(workspace, timeout_ms=timeout_ms) as connection:
            try:
                # One scan under the §8.1 writer lock: no other writer can add
                # an imported row while this command runs, so the map stays
                # exact as each accepted record extends it.
                retained = retained_import_hashes(
                    connection, contract.source_system
                )
                for parsed in parsed_records:
                    _classify(
                        connection,
                        parsed,
                        contract=contract,
                        context=context,
                        retained=retained,
                        recorded_at=recorded_at,
                        id_factory=id_factory,
                        attempted=attempted,
                        classified=classified,
                    )
            except sqlite3.OperationalError as error:
                progress = _cancelled_report(
                    connection, attempted=attempted, classified=classified
                )
                if "locked" in str(error).lower() or "busy" in str(error).lower():
                    busy = WorkspaceBusyError()
                    busy.import_outcome = progress
                    raise busy from error
                # A non-busy operational failure — a full disk, a damaged
                # file — is still §14.14 class 1, but §19.4 rule 4's committed
                # records survive it. Raw propagation would reach the CLI's
                # untyped handler, which reports no boundary at all; the base
                # class carries the same exit code and diagnostic while
                # keeping the rows reportable.
                internal = Exp2ResError()
                internal.import_outcome = progress
                raise internal from error
            except KeyboardInterrupt:
                # §14.14 rule 6: rule 4 commits each accepted record in its
                # own transaction, so those records are lifecycle boundaries
                # that remain committed and are reported rather than restored.
                # A signal that lands on the last record's own classification
                # leaves nothing unreported, so completeness is read off the
                # outcome instead of assumed from where the signal landed.
                cancelled = OperationCancelledError()
                cancelled.import_outcome = _cancelled_report(
                    connection, attempted=attempted, classified=classified
                )
                cancelled.import_classified = is_complete(cancelled.import_outcome)
                raise cancelled from None
            except Exp2ResError as error:
                # §19.4 rule 4: a failure fails only its own record and never
                # withdraws an accepted one, so a classified failure carries
                # the rows already committed behind it.
                error.import_outcome = _cancelled_report(
                    connection, attempted=attempted, classified=classified
                )
                raise
    except KeyboardInterrupt:
        # The signal landed outside the record loop: either in
        # `writer_database` entry — the §8.1 lock wait or the §13.14 preamble,
        # before any record was read — or in its teardown, after every record
        # was classified. Only the second reports a complete primary result;
        # §14.14 rule 4 gives the first `result = null`. Either way the
        # committed rows are durable and reported, and the closed connection
        # is no longer available to re-read.
        cancelled = OperationCancelledError()
        cancelled.import_outcome = report()
        cancelled.import_classified = is_complete(cancelled.import_outcome)
        raise cancelled from None
    except Exp2ResError as error:
        # A typed failure raised by `writer_database` itself, past every
        # handler that knows this import's progress.
        if getattr(error, "import_outcome", None) is None:
            error.import_outcome = report()
            error.import_classified = is_complete(error.import_outcome)
        raise
    except Exception as error:
        # Teardown — §8.1 lock release, connection close — can fail after
        # every record has already committed. §14.14 rule 6 keeps those
        # boundaries reported, and the base class carries the same class-1
        # exit the raw exception would have produced.
        internal = Exp2ResError()
        internal.import_outcome = report()
        internal.import_classified = is_complete(internal.import_outcome)
        raise internal from error
    return report()
