"""§19.4 record shape, identity, hashing, and multi-record payload rules."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Protocol

from exp2res.config import WorkspaceConfig
from exp2res.domain.canonical import canonical_model_hash
from exp2res.domain.enums import EntryType, EvidenceStrength, SourceType
from exp2res.domain.models import OccurredAt, StrictModel, validate_structural
from exp2res.errors import (
    ImportPayloadChangedError,
    ImportPayloadInvalidError,
    ImportPayloadTooLargeError,
)

# §11 rule 38's payload bounds. §19.4 rule 4 makes the total-object limit the
# whole payload-size bound and adds no second numeric cap, so nothing here
# bounds the payload's byte length beyond what those object and per-field
# limits already imply.
MAX_PAYLOAD_OBJECTS = 10_000
MAX_JSON_NESTING = 32


class SourceRecord(StrictModel):
    """One complete §19.1–§19.3 source object.

    §19.4 rule 1 accepts exactly the invoked importer's own contract record:
    no wrapping envelope, no shared version field, no envelope-level
    discriminator. `extra = "forbid"` is therefore the whole undeclared-field
    rule, and a record-supplied `content_hash` is one more undeclared field
    (rule 3) rather than a competing hash source.
    """

    @property
    def source_identity(self) -> str:
        raise NotImplementedError


class RecordRejected(Exception):
    """One record's §19.4 rejection, carrying its stable reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class EvidencePlan:
    """One linked §13.1 evidence item an accepted record creates."""

    summary: str
    strength: EvidenceStrength
    uri: Optional[str] = None
    path: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportPlan:
    """The §14.5 mapping of one validated record, before ID assignment."""

    entry_type: EntryType
    source_type: SourceType
    occurred: OccurredAt
    raw_text: str
    evidence: tuple[EvidencePlan, ...]
    project: Optional[str] = None
    external_ref: Optional[str] = None


@dataclass(frozen=True)
class PlanContext:
    """What a record's §14.5 mapping needs beyond the record itself."""

    payload_root: Path
    config: WorkspaceConfig


def _no_contract_check(record: Any, raw: Mapping[str, Any]) -> None:
    return None


@dataclass(frozen=True)
class SourceContract:
    """One §19 importer: its record shape, identity, and §14.5 mapping."""

    source_system: str
    record_model: type[SourceRecord]
    multi_record: bool
    raw_identity: Callable[[Mapping[str, Any]], Optional[str]]
    plan: Callable[[Any, PlanContext], ImportPlan]
    # Contract rules that outlive the validated record because they read the
    # exact accepted input spelling. They run before §19.4 rule 2
    # classification: equal hashes do not imply equal input bytes, so a
    # later record could otherwise inherit an earlier one's verdict.
    check: Callable[[Any, Mapping[str, Any]], None] = _no_contract_check


@dataclass(frozen=True)
class ParsedRecord:
    """One established input record boundary under §19.4 rule 5.

    A `reason` is the parse-time verdict; `value` is whatever decoded, and
    stays populated beside a reason so a rejected record can still report the
    source identity it carries. `identity` carries that same report for a
    record that never decoded into a usable mapping at all.
    """

    record_number: int
    value: Any = None
    reason: Optional[str] = None
    identity: Optional[str] = None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # Silently keeping the last value would let one payload carry two shapes,
    # only one of which the §19.4 rule 3 hash covers.
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate JSON object key")
        seen[key] = value
    return seen


def decode_json(text: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except RecursionError as error:
        raise ValueError("JSON nesting too deep") from error


def salvaged_identity(text: str, contract: SourceContract) -> Optional[str]:
    """Read the source identity out of a record the strict decode rejected.

    §19.4 rule 5 nulls `source_record_id` only when that record's own identity
    is missing or invalid. A key declared twice somewhere else in the line
    leaves the identity determinate — every decoding order agrees on it — so
    the record is still rejected but reports what it names.

    Only the identity string survives this pass. The tree is discarded and no
    object reaches `scan_record`'s counter, so the payload-wide object cap
    still counts exactly the records that decoded under one unambiguous
    shape, and a rejected record cannot smuggle an unbounded graph past it.
    """

    doubled: list[frozenset[str]] = [frozenset()]

    def record_doubled_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        repeated: set[str] = set()
        for key, value in pairs:
            if key in seen:
                repeated.add(key)
            seen[key] = value
        # json completes the outermost object last, so after the decode this
        # holds that object's repeats — the only ones an identity field can
        # sit under.
        doubled[0] = frozenset(repeated)
        return seen

    try:
        value = json.loads(text, object_pairs_hook=record_doubled_keys)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    # A doubled key is dropped rather than resolved: two competing values are
    # an invalid identity, not a coin toss between them.
    return contract.raw_identity(
        {key: item for key, item in value.items() if key not in doubled[0]}
    )


def scan_record(value: Any, *, counter: list[int]) -> Optional[str]:
    """Count one record's objects and return its first rejection reason.

    The object count is payload-wide (§19.4 rule 4 makes §11 rule 38's cap the
    whole payload-size bound), so it must not stop at a record-level defect:
    returning at the first float or over-deep node would leave everything
    behind it uncounted, and a payload could carry an unbounded object graph
    behind one cheap rejected record. Counting therefore always completes,
    while the record's own verdict is the first reason found in traversal
    order. The traversal is iterative because it no longer stops at
    `MAX_JSON_NESTING`, and a decoded value may nest far deeper than that.
    """

    reason: Optional[str] = None
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        children: Any = ()
        if isinstance(current, dict):
            counter[0] += 1
            if counter[0] > MAX_PAYLOAD_OBJECTS:
                raise ImportPayloadTooLargeError()
            children = list(current.values())
        elif isinstance(current, list):
            children = current
        if reason is None:
            if depth > MAX_JSON_NESTING:
                reason = "record_nesting_too_deep"
            elif isinstance(current, float):
                # §11 deliberately leaves float rendering unpinned, so hashing
                # one would be implementation-dependent (§19.4 rule 3).
                reason = "record_float_value"
        # Reversed, so popping walks the children in their source order and
        # the reported reason stays the document-order first one.
        for child in reversed(children):
            stack.append((child, depth + 1))
    return reason


class PayloadSource(Protocol):
    """One selected payload, re-readable as lines or as one whole document."""

    def lines(self) -> Iterator[str]: ...

    def text(self) -> str: ...

    def identity(self) -> tuple[int, int, int]: ...


class PayloadRecords:
    """Every §19.4 rule 5 input record boundary in one payload, in file order.

    Construction establishes all of them once and raises the failures that are
    the payload's rather than a record's — a decode failure, a single-record
    document that is not JSON, §11 rule 38's object cap. Those must land
    before any record commits, so nothing here waits for the import loop to
    reach them. Iteration then replays the boundaries; once a boundary exists
    its record is reported, however invalid its content.

    A multi-record payload is re-read per pass rather than retained, so no
    more than one record's decoded value is ever resident: §19.4 rule 4 makes
    the object cap the whole payload bound, and a conforming JSONL file can
    spend it on records whose text fields alone exhaust memory. One record is
    bounded by §11's own field limits, so a single-record payload is held.

    Rule 4 partitions one payload, so every pass must see the same one. An
    open descriptor fixes the inode but not the bytes, and a payload rewritten
    under it would otherwise be reported as a complete result over a boundary
    set no pass ever saw whole. Every pass is therefore bracketed: the
    baseline is taken before the boundaries are established and rechecked once
    they are, so a rewrite that lands mid-scan cannot become the baseline, and
    each replay rechecks before it yields and again once it is exhausted.
    """

    def __init__(self, payload: PayloadSource, *, contract: SourceContract) -> None:
        self._payload = payload
        self._contract = contract
        self._held: tuple[ParsedRecord, ...] | None = None
        self._identity = payload.identity()
        if contract.multi_record:
            self.total = sum(1 for _ in self._parse())
        else:
            self._held = tuple(self._parse())
            self.total = len(self._held)
        self._reconfirm()

    def __iter__(self) -> Iterator[ParsedRecord]:
        """Replay the boundaries, reconfirming the payload before yielding.

        The check is eager rather than deferred into the generator so that a
        caller taking this iterator before the §8.1 writer lock refuses an
        already-rewritten payload with nothing committed.
        """

        self._reconfirm()
        if self._held is not None:
            return iter(self._held)
        return self._replay()

    def _reconfirm(self) -> None:
        if self._payload.identity() != self._identity:
            raise ImportPayloadChangedError()

    def _replay(self) -> Iterator[ParsedRecord]:
        """Yield each boundary, then reconfirm the exhausted payload.

        A rewrite that landed during the import is past preventing, but §19.4
        rule 4 keeps the records already committed reportable, so the torn
        payload is reported rather than passed off as a partition.
        """

        yield from self._parse()
        self._reconfirm()

    def _parse(self) -> Iterator[ParsedRecord]:
        counter = [0]
        if not self._contract.multi_record:
            try:
                value = decode_json(self._payload.text())
            except ValueError as error:
                raise ImportPayloadInvalidError() from error
            # The decoded value is kept even when the scan rejects it: §19.4
            # rule 5 nulls `source_record_id` only for a missing or invalid
            # identity, and a record can carry a valid identity beside the
            # defect.
            yield ParsedRecord(1, value=value, reason=scan_record(value, counter=counter))
            return

        number = 0
        for line in self._payload.lines():
            # A blank separator line establishes no record: JSONL writers
            # append one freely, and counting it would renumber every later
            # record.
            if not line.strip():
                continue
            number += 1
            try:
                value = decode_json(line)
            except ValueError:
                yield ParsedRecord(
                    number,
                    reason="record_not_json",
                    identity=salvaged_identity(line, self._contract),
                )
                continue
            yield ParsedRecord(
                number, value=value, reason=scan_record(value, counter=counter)
            )


def content_hash(record: SourceRecord) -> str:
    """§19.4 rule 3: SHA-256 over the validated record's §11 canonical bytes."""

    return canonical_model_hash(record)


def optional_identity(value: Any) -> Optional[str]:
    """Return one raw record's identity string, or None when it is unusable."""

    if not isinstance(value, str):
        return None
    try:
        return validate_structural(value)
    except (UnicodeError, ValueError, TypeError):
        return None
