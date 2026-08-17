"""§19.2 knowledge-state snapshot importer."""

from __future__ import annotations

from datetime import timezone
import re
from typing import Any, Literal, Mapping, Optional

from pydantic import field_validator, model_validator

from exp2res.domain.models import (
    BoundaryDatetime,
    OccurredAt,
    StrictModel,
    validate_free_text,
    validate_structural,
)
from exp2res.domain.temporal import interval_contains, occurred_interval
from exp2res.errors import InvalidInputError
from exp2res.integrations.records import (
    EvidencePlan,
    ImportPlan,
    PlanContext,
    RecordRejected,
    SourceContract,
    SourceRecord,
    optional_identity,
)
from exp2res.services.source_files import authorize_payload_locator

CONTENT_DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_LIST_ITEMS = 1_000


class KnowledgeState(StrictModel):
    """One opaque Atlas subject/scale/value triple; no Exp2Res ordering."""

    subject: str
    scale: str
    value: str

    @field_validator("subject", "scale", "value")
    @classmethod
    def opaque_strings(cls, value: str) -> str:
        return validate_structural(value)


class TrailSegment(StrictModel):
    label: str
    occurred: OccurredAt

    @field_validator("label")
    @classmethod
    def structural_label(cls, value: str) -> str:
        return validate_structural(value)


class EvidenceReference(StrictModel):
    """An inert logical source ID; never path or fetch authority."""

    reference: str

    @field_validator("reference")
    @classmethod
    def structural_reference(cls, value: str) -> str:
        return validate_structural(value)


class AtlasRecord(SourceRecord):
    """§19.2's complete accepted snapshot record."""

    source: Literal["atlas"]
    record_id: str
    domain: Literal["knowledge_state"]
    as_of: BoundaryDatetime
    occurred: OccurredAt
    text: str
    summary: str
    knowledge_state: list[KnowledgeState]
    trail_segments: list[TrailSegment]
    evidence_references: list[EvidenceReference]
    # Required nullable members: omission and explicit absence must reach one
    # record shape before §19.4 hashing, so neither carries a default.
    path: Optional[str]
    content_digest: Optional[str]

    @field_validator("record_id")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("text")
    @classmethod
    def source_text(cls, value: str) -> str:
        return validate_free_text(value, raw=True, nonempty=True)

    @field_validator("summary")
    @classmethod
    def source_summary(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("knowledge_state", "trail_segments", "evidence_references")
    @classmethod
    def bounded_lists(cls, value: list[Any]) -> list[Any]:
        if len(value) > MAX_LIST_ITEMS:
            raise ValueError("list exceeds the §11 item limit")
        return value

    @field_validator("knowledge_state")
    @classmethod
    def knowledge_state_is_present(
        cls, value: list[KnowledgeState]
    ) -> list[KnowledgeState]:
        if not value:
            raise ValueError("knowledge_state must not be empty")
        return value

    @field_validator("content_digest")
    @classmethod
    def digest_form(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and CONTENT_DIGEST.fullmatch(value) is None:
            raise ValueError("content_digest must be 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def referenced_document(self) -> "AtlasRecord":
        if self.path is None and self.content_digest is not None:
            raise ValueError("content_digest requires a path")
        return self

    @model_validator(mode="after")
    def temporal_constraints(self) -> "AtlasRecord":
        # §11 rule 54 makes every interval below representable, so nothing
        # here raises the `OverflowError` that — not being a `ValueError` —
        # would escape validation and abort the import (§19.4 rule 4).
        snapshot = occurred_interval(self.occurred)
        if snapshot.unbounded:
            raise ValueError("snapshot occurred needs a finite upper bound")
        assert snapshot.end is not None
        for segment in self.trail_segments:
            interval = occurred_interval(segment.occurred)
            if interval.unbounded:
                raise ValueError("trail segment needs a finite upper bound")
            if not interval_contains(snapshot, interval):
                raise ValueError("snapshot must contain every trail segment")
        if self.as_of.astimezone(timezone.utc) < snapshot.end:
            raise ValueError("as_of precedes the snapshot upper bound")
        return self

    @property
    def source_identity(self) -> str:
        return self.record_id


def raw_identity(raw: Mapping[str, Any]) -> Optional[str]:
    return optional_identity(raw.get("record_id"))


def check(record: AtlasRecord, raw: Mapping[str, Any]) -> None:
    """Require every accepted structured value byte-exactly in `text`.

    The adapter's `text` is the authoritative complete source rendering, so
    the persisted projection carries each accepted value rather than only
    its hash. Bounds and `as_of` are compared as their exact accepted input
    strings: two spellings of one instant validate to the same datetime and
    hash alike, but only the supplied one can appear in the source text.
    """

    required: list[str] = [record.summary]
    for state in record.knowledge_state:
        required.extend((state.subject, state.scale, state.value))
    raw_segments = raw["trail_segments"]
    for index, segment in enumerate(record.trail_segments):
        raw_occurred = raw_segments[index]["occurred"]
        required.append(segment.label)
        for bound in ("start", "end"):
            supplied = raw_occurred.get(bound)
            if supplied is not None:
                required.append(supplied)
        required.extend((segment.occurred.precision, segment.occurred.confidence))
    required.extend(
        reference.reference for reference in record.evidence_references
    )
    required.append(raw["as_of"])
    for value in required:
        if value not in record.text:
            raise RecordRejected("atlas_text_fidelity")


def plan(record: AtlasRecord, context: PlanContext) -> ImportPlan:
    path: Optional[str] = None
    if record.path is not None:
        try:
            path = authorize_payload_locator(
                record.path,
                payload_root=context.payload_root,
                config=context.config,
            )
        except InvalidInputError as error:
            raise RecordRejected(error.diagnostic_class) from error
    metadata: dict[str, Any] = {}
    if record.content_digest is not None:
        metadata["content_digest"] = record.content_digest
    return ImportPlan(
        entry_type="atlas_snapshot",
        source_type="imported_artifact",
        occurred=record.occurred,
        raw_text=record.text,
        evidence=(
            EvidencePlan(
                summary=record.summary,
                strength="knowledge_state_snapshot",
                path=path,
                metadata=metadata,
            ),
        ),
    )


CONTRACT = SourceContract(
    source_system="atlas",
    record_model=AtlasRecord,
    multi_record=False,
    raw_identity=raw_identity,
    plan=plan,
    check=check,
)
