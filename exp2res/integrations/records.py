"""§19.4 record shape, identity, hashing, and multi-record payload rules."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from exp2res.config import WorkspaceConfig
from exp2res.domain.canonical import canonical_model_hash
from exp2res.domain.enums import EntryType, EvidenceStrength, SourceType
from exp2res.domain.models import OccurredAt, StrictModel, validate_structural
from exp2res.errors import ImportPayloadInvalidError, ImportPayloadTooLargeError

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
    source identity it carries.
    """

    record_number: int
    value: Any = None
    reason: Optional[str] = None


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


def parse_payload(text: str, *, multi_record: bool) -> tuple[ParsedRecord, ...]:
    """Establish every §19.4 rule 5 input record boundary, in file order.

    A payload failure too early to establish those boundaries raises instead,
    leaving the command with `result = null`; once a boundary exists its
    record is reported, however invalid its content.
    """

    counter = [0]
    if not multi_record:
        try:
            value = decode_json(text)
        except ValueError as error:
            raise ImportPayloadInvalidError() from error
        # The decoded value is kept even when the scan rejects it: §19.4 rule 5
        # nulls `source_record_id` only for a missing or invalid identity, and
        # a record can carry a valid identity beside the defect.
        return (ParsedRecord(1, value=value, reason=scan_record(value, counter=counter)),)

    records: list[ParsedRecord] = []
    # JSONL delimits records by LF alone. `splitlines()` would also break on
    # U+2028 and friends, splitting one record whose source voice legitimately
    # contains them into two unparseable halves.
    for line in text.split("\n"):
        # A blank separator line establishes no record: JSONL writers append
        # one freely, and counting it would renumber every later record.
        if not line.strip():
            continue
        number = len(records) + 1
        try:
            value = decode_json(line)
        except ValueError:
            # Nothing decoded, so this record has no recoverable identity.
            records.append(ParsedRecord(number, reason="record_not_json"))
            continue
        records.append(
            ParsedRecord(number, value=value, reason=scan_record(value, counter=counter))
        )
    return tuple(records)


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
