"""Strict models for the implemented §11 entity subset."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re
import unicodedata
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import core_schema

from .enums import (
    MAX_UNCERTAINTY_WIDTH,
    ActivityContext,
    AssessmentScope,
    ClaimKind,
    Confidence,
    CounterevidenceRefType,
    DetectionRefType,
    EntryType,
    EvidenceStrength,
    GapPriority,
    GapTrigger,
    JDRequirementKind,
    OwnershipLevel,
    ResumeTargetSection,
    SelfClaimDimension,
    SourceType,
    TargetRoleRelevance,
    TemporalConfidence,
    TemporalPrecision,
    VerificationStatus,
    VerificationTargetRefType,
)

RAW_TEXT_LIMIT = 1_048_576
STRING_LIMIT = 16_384
# §11 boundary limits: GapQuestion.question alone carries a 1,024-byte cap so
# the §14.7 question_text metadata copy fits the 4 KiB entity budget.
QUESTION_LIMIT = 1_024
METADATA_LIMIT = 4_096
METADATA_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
# §19.4 rule 3's content-hash form, which §11 rule 30 types the import digest
# keys by. The importer, the model boundary, and the retained-identity scan
# all compare against it, so it has one home here with rule 30.
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def canonical_project_key(label: str) -> str:
    """Return §12 rule 14's one canonical project comparison identity."""

    return unicodedata.normalize("NFC", label).strip().casefold()


def canonical_branch_identity(name: str) -> str:
    """Return §14.10's folded branch replacement/selection identity.

    §11 rule 47 names this point exactly: Unicode NFC followed by
    locale-independent Default Case Folding, with no whitespace trim, and it
    controls replacement and selection only — never a managed path (§13.14).
    """

    return unicodedata.normalize("NFC", name).casefold()


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def validate_structural(value: str, *, nonempty: bool = True) -> str:
    if nonempty and not value:
        raise ValueError("empty structural string")
    if _utf8_size(value) > STRING_LIMIT:
        raise ValueError("structural string too large")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ValueError("control character in structural string")
    return value


def validate_free_text(
    value: str,
    *,
    raw: bool = False,
    nonempty: bool = False,
    limit: int | None = None,
) -> str:
    if nonempty and not value:
        raise ValueError("empty text")
    if limit is None:
        limit = RAW_TEXT_LIMIT if raw else STRING_LIMIT
    if _utf8_size(value) > limit:
        raise ValueError("text too large")
    if any(
        (ord(char) < 32 and char not in "\t\n\r") or 127 <= ord(char) <= 159
        for char in value
    ):
        raise ValueError("control character in free text")
    return value


def validate_posix_path(value: str) -> str:
    validate_structural(value)
    if "\\" in value or WINDOWS_DRIVE.match(value) or value.startswith("//"):
        raise ValueError("unsupported path form")
    PurePosixPath(value)
    return value


def _validate_metadata_scalar(value: Any) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        validate_free_text(value)
        return
    raise ValueError("invalid metadata scalar")


def validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    def validate_object(item: dict[str, Any], *, nested: bool) -> None:
        if len(item) > 16:
            raise ValueError("too many metadata keys")
        for key, child in item.items():
            if not isinstance(key, str) or len(key) > 64 or not METADATA_KEY.fullmatch(key):
                raise ValueError("invalid metadata key")
            validate_structural(key)
            if isinstance(child, list):
                if len(child) > 1_000:
                    raise ValueError("metadata list too large")
                for member in child:
                    _validate_metadata_scalar(member)
            elif isinstance(child, dict):
                if nested:
                    raise ValueError("metadata nesting too deep")
                validate_object(child, nested=True)
            else:
                _validate_metadata_scalar(child)

    validate_object(value, nested=False)
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > METADATA_LIMIT:
        raise ValueError("metadata too large")
    return value


def validate_import_identity(
    metadata: dict[str, Any], keys: tuple[str, ...]
) -> dict[str, Any]:
    """§11 rules 30 and 55: type whichever named import keys are carried.

    Rule 29 requires none of them — `import file` writes an imported `RawLog`
    with no identity keys at all — so absence is legal and only a present
    value is typed. Typing does not consume: the key stays inert under rules
    31 and 33, and this is only rule 44's structural contract for it.
    """

    for key in keys:
        if key not in metadata:
            continue
        value = metadata[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        validate_structural(value)
        if key in ("content_hash", "content_digest") and not SHA256_HEX.fullmatch(
            value
        ):
            raise ValueError(f"{key} must be a lowercase SHA-256 hexadecimal digest")
    return metadata


_ISO_8601_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]")


def _iso_8601_only(value: str) -> str:
    """§11 rules 3 and 6: the one granted bridge is an ISO 8601 string.

    Pydantic's JSON-mode parser reads a numeric string as a Unix timestamp
    too — `"1780272000"`, but equally `".5"`, `"1."`, and `"20260601"` — which
    is a second string-to-`datetime` bridge rule 6 forbids. The test is the
    ISO date-and-separator prefix rather than any enumeration of numeric
    spellings, so the grant stays an allowlist and no future spelling opens a
    third bridge; the parse itself still belongs to the schema below. The
    separators are ISO 8601's `T` plus the lower-case and space forms RFC 3339
    sanctions for it, and not Pydantic's `_`, which no standard defines. It is
    closed here rather than downstream because by then the value is an
    instant indistinguishable from a spelled-out one.
    """

    if not _ISO_8601_DATETIME.match(value):
        raise ValueError("datetime must be an ISO 8601 string")
    return value


def _boundary_datetime(value: datetime) -> datetime:
    """§11 rules 4 and 54: offset-aware, and with a UTC instant that exists.

    Rule 4's awareness is what makes §11 rule 13's UTC normalization
    meaningful; rule 54 is what makes it total. An offset-aware value at the
    far edge of the calendar — `0001-01-01T00:00:00+14:00`, whose UTC form
    would be year 0 — is aware and still has no canonical form, and every
    hash, comparison, and export downstream needs one.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must carry an offset")
    try:
        value.astimezone(timezone.utc)
    except OverflowError as error:
        raise ValueError("datetime has no representable UTC instant") from error
    return value


class _BoundaryDatetime:
    """§11's one datetime boundary, applied identically wherever one arrives.

    The two input modes stay separate deliberately. A Python-level before- or
    wrap-validator would hand its result to the inner schema as a Python
    object, which drops JSON mode and takes rule 3's ISO grant down with the
    epoch reading it was meant to close; only a JSON/Python schema pair keeps
    both halves. The JSON schema override restores the `date-time` format
    annotation the custom core schema would otherwise omit, which §15's
    provider-facing `schema_bytes` carries.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        return core_schema.no_info_after_validator_function(
            _boundary_datetime,
            core_schema.json_or_python_schema(
                json_schema=core_schema.chain_schema(
                    [
                        core_schema.str_schema(),
                        core_schema.no_info_plain_validator_function(_iso_8601_only),
                        core_schema.datetime_schema(strict=False),
                    ]
                ),
                python_schema=core_schema.datetime_schema(strict=True),
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler):
        return handler(core_schema.datetime_schema())


BoundaryDatetime = Annotated[datetime, _BoundaryDatetime]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_assignment=True,
    )


class OccurredAt(StrictModel):
    start: Optional[BoundaryDatetime] = None
    end: Optional[BoundaryDatetime] = None
    precision: TemporalPrecision
    confidence: TemporalConfidence

    @model_validator(mode="after")
    def valid_shape(self) -> "OccurredAt":
        non_range = {
            "exact_datetime",
            "exact_day",
            "week",
            "month",
            "quarter",
            "year",
        }
        if self.precision in non_range:
            if self.start is None or self.end is not None:
                raise ValueError("invalid non-range temporal shape")
            # §11 rule 54. The width goes on the UTC instant, as §16.7 rule 3
            # requires: a west-of-UTC offset shifts the anchor later, so a
            # start carrying its width in its own spelling can still overflow.
            try:
                self.start.astimezone(timezone.utc) + MAX_UNCERTAINTY_WIDTH[
                    self.precision
                ]
            except OverflowError as error:
                raise ValueError(
                    "placement has no representable uncertainty interval"
                ) from error
        elif self.precision in {"date_range", "approximate_range"}:
            if self.start is None or (
                self.end is not None and self.end <= self.start
            ):
                raise ValueError("invalid temporal range")
        elif self.precision == "unknown":
            if self.start is not None or self.end is not None:
                raise ValueError("unknown precision cannot carry bounds")
        return self


class RawLog(StrictModel):
    id: str
    recorded_at: BoundaryDatetime
    entry_type: EntryType
    source_type: SourceType
    occurred: OccurredAt
    raw_text: str
    project: Optional[str] = None
    external_ref: Optional[str] = None
    corrects_log_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "project", "external_ref", "corrects_log_id")
    @classmethod
    def structural_fields(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_structural(value)

    @field_validator("project")
    @classmethod
    def project_is_not_canonical_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not canonical_project_key(value):
            raise ValueError("project label canonicalizes to blank")
        return value

    @field_validator("raw_text")
    @classmethod
    def raw_text_policy(cls, value: str) -> str:
        return validate_free_text(value, raw=True, nonempty=True)

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)

    @model_validator(mode="after")
    def typed_import_identity(self) -> "RawLog":
        # §11 rule 55: `source_type` is what tells the record it is imported,
        # so rule 33 holds without inspecting key names alone.
        if self.source_type in ("imported_artifact", "imported_event"):
            validate_import_identity(
                self.metadata, ("source_system", "source_record_id", "content_hash")
            )
        return self


class EvidenceItem(StrictModel):
    id: str
    created_at: BoundaryDatetime
    raw_log_id: str
    title: Optional[str] = None
    summary: str
    uri: Optional[str] = None
    path: Optional[str] = None
    strength: EvidenceStrength
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "raw_log_id", "uri")
    @classmethod
    def structural_fields(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_structural(value)

    @field_validator("title", "summary")
    @classmethod
    def text_fields(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_free_text(value)

    @field_validator("path")
    @classmethod
    def path_policy(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_posix_path(value)

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        # §11 rule 55: this entity carries no provenance field, so the digest
        # is typed wherever it appears rather than for importers alone.
        return validate_import_identity(validate_metadata(value), ("content_digest",))


class ExperienceFact(StrictModel):
    id: str
    created_at: BoundaryDatetime
    superseded_at: Optional[BoundaryDatetime] = None
    claim: str
    claim_kind: ClaimKind = "observed_fact"

    project: Optional[str] = None
    role: Optional[str] = None
    company: Optional[str] = None
    context: ActivityContext
    ownership_level: OwnershipLevel

    action: Optional[str] = None
    object: Optional[str] = None
    outcome: Optional[str] = None

    skills: list[str] = Field(default_factory=list, max_length=1_000)
    technologies: list[str] = Field(default_factory=list, max_length=1_000)
    themes: list[str] = Field(default_factory=list, max_length=1_000)

    occurred: OccurredAt
    source_log_ids: list[str] = Field(min_length=1, max_length=1_000)
    evidence_item_ids: list[str] = Field(min_length=1, max_length=1_000)

    confidence: Confidence
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("claim")
    @classmethod
    def claim_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("project")
    @classmethod
    def project_policy(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        validate_structural(value)
        if not canonical_project_key(value):
            raise ValueError("project label canonicalizes to blank")
        return value

    @field_validator("role", "company", "action", "object", "outcome")
    @classmethod
    def optional_text_policy(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_free_text(value, nonempty=True)

    @field_validator("skills", "technologies", "themes")
    @classmethod
    def text_list_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_free_text(member, nonempty=True)
        return value

    @field_validator("source_log_ids", "evidence_item_ids")
    @classmethod
    def typed_id_list_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_structural(member)
        if len(value) != len(set(value)):
            raise ValueError("duplicate typed ID")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class CounterevidenceItem(StrictModel):
    statement: str
    source_ref_type: CounterevidenceRefType
    source_ref_id: str

    @field_validator("statement")
    @classmethod
    def statement_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("source_ref_id")
    @classmethod
    def source_ref_id_policy(cls, value: str) -> str:
        return validate_structural(value)


class SelfClaim(StrictModel):
    id: str
    created_at: BoundaryDatetime
    superseded_at: Optional[BoundaryDatetime] = None
    snapshot_id: str
    claim: str
    claim_kind: ClaimKind
    dimension: SelfClaimDimension
    source_fact_ids: list[str] = Field(max_length=1_000)
    counter_fact_ids: list[str] = Field(default_factory=list, max_length=1_000)
    confidence: Confidence
    verification_status: VerificationStatus
    counterevidence: list[CounterevidenceItem] = Field(
        default_factory=list, max_length=1_000
    )
    uncertainty: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "snapshot_id")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("claim")
    @classmethod
    def claim_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("uncertainty")
    @classmethod
    def uncertainty_policy(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_free_text(value, nonempty=True)

    @field_validator("source_fact_ids", "counter_fact_ids")
    @classmethod
    def typed_id_list_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_structural(member)
        if len(value) != len(set(value)):
            raise ValueError("duplicate typed ID")
        return value

    @model_validator(mode="after")
    def counter_facts_are_sources(self) -> "SelfClaim":
        if not set(self.counter_fact_ids).issubset(self.source_fact_ids):
            raise ValueError("counter fact is not a source fact")
        return self

    @field_validator("counterevidence")
    @classmethod
    def counterevidence_policy(
        cls, value: list[CounterevidenceItem]
    ) -> list[CounterevidenceItem]:
        keys = [(item.source_ref_type, item.source_ref_id) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate counterevidence reference")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class AssessmentSnapshot(StrictModel):
    id: str
    created_at: BoundaryDatetime
    superseded_at: Optional[BoundaryDatetime] = None
    scope: AssessmentScope
    title: str
    summary: str
    gap_question_ids: list[str] = Field(default_factory=list, max_length=1_000)
    contradiction_ids: list[str] = Field(default_factory=list, max_length=1_000)
    verification_status: VerificationStatus
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("title", "summary")
    @classmethod
    def text_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("gap_question_ids", "contradiction_ids")
    @classmethod
    def typed_id_list_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_structural(member)
        if len(value) != len(set(value)):
            raise ValueError("duplicate typed ID")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class VerificationFinding(StrictModel):
    id: str
    created_at: BoundaryDatetime
    produced_by_run_id: str
    target_type: VerificationTargetRefType
    target_id: str
    status: VerificationStatus
    reason: str
    unsupported_phrases: list[str] = Field(default_factory=list, max_length=1_000)
    suggested_rewrite: Optional[str] = None
    counterevidence: list[CounterevidenceItem] = Field(
        default_factory=list, max_length=1_000
    )

    @field_validator("id", "produced_by_run_id", "target_id")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("reason")
    @classmethod
    def reason_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("suggested_rewrite")
    @classmethod
    def suggested_rewrite_policy(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_free_text(value, nonempty=True)

    @field_validator("unsupported_phrases")
    @classmethod
    def unsupported_phrase_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_free_text(member, nonempty=True)
        return value

    @field_validator("counterevidence")
    @classmethod
    def counterevidence_policy(
        cls, value: list[CounterevidenceItem]
    ) -> list[CounterevidenceItem]:
        keys = [(item.source_ref_type, item.source_ref_id) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate counterevidence reference")
        return value


class Contradiction(StrictModel):
    id: str
    created_at: BoundaryDatetime
    superseded_at: Optional[BoundaryDatetime] = None
    title: str
    description: str

    left_ref_type: DetectionRefType
    left_ref_id: str
    right_ref_type: DetectionRefType
    right_ref_id: str

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "left_ref_id", "right_ref_id")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("title", "description")
    @classmethod
    def text_fields(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class GapQuestion(StrictModel):
    id: str
    created_at: BoundaryDatetime
    superseded_at: Optional[BoundaryDatetime] = None

    target_type: DetectionRefType
    target_id: str

    question: str
    reason: GapTrigger
    priority: GapPriority

    answered: bool = False
    answer_log_id: Optional[str] = None

    @field_validator("id", "target_id", "answer_log_id")
    @classmethod
    def structural_fields(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_structural(value)

    @field_validator("question")
    @classmethod
    def question_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True, limit=QUESTION_LIMIT)


class JDRequirement(StrictModel):
    id: str = Field(min_length=1)
    kind: JDRequirementKind
    text: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list, max_length=1_000)

    @field_validator("id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("text")
    @classmethod
    def text_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("keywords")
    @classmethod
    def keyword_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_free_text(member, nonempty=True)
        return value


class ParsedJD(StrictModel):
    requirements: list[JDRequirement] = Field(default_factory=list, max_length=1_000)
    seniority_signals: list[str] = Field(default_factory=list, max_length=1_000)
    domain_signals: list[str] = Field(default_factory=list, max_length=1_000)
    keywords: list[str] = Field(default_factory=list, max_length=1_000)
    red_flags: list[str] = Field(default_factory=list, max_length=1_000)

    @field_validator("seniority_signals", "domain_signals", "keywords", "red_flags")
    @classmethod
    def context_list_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_free_text(member, nonempty=True)
        return value

    @field_validator("requirements")
    @classmethod
    def requirement_ids_are_unique(cls, value: list[JDRequirement]) -> list[JDRequirement]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate requirement ID")
        return value


class JobDescription(StrictModel):
    id: str
    created_at: BoundaryDatetime

    title: Optional[str] = None
    company: Optional[str] = None
    raw_text: str
    parsed: ParsedJD

    @field_validator("id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("title", "company")
    @classmethod
    def text_fields(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_free_text(value, nonempty=True)

    @field_validator("raw_text")
    @classmethod
    def raw_text_policy(cls, value: str) -> str:
        return validate_free_text(value, raw=True, nonempty=True)


class ResumeBranch(StrictModel):
    id: str
    name: str
    assessment_snapshot_id: str
    job_description_id: str

    created_at: BoundaryDatetime
    superseded_at: Optional[BoundaryDatetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "assessment_snapshot_id", "job_description_id")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("name")
    @classmethod
    def name_policy(cls, value: str) -> str:
        # §14.10: a non-blank display name under structural hygiene, stored at
        # the owner's exact spelling. No path-specific rejection applies —
        # `/`, `\`, dot segments, surrounding whitespace or `.`, and the name
        # `assessment` are ordinary names because §13.14 publishes only under
        # `out/branch/<branch-id>/`.
        validate_structural(value)
        if not value.strip():
            raise ValueError("blank branch name")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class ResumeBullet(StrictModel):
    id: str
    created_at: BoundaryDatetime
    superseded_at: Optional[BoundaryDatetime] = None
    branch_id: str
    text: str
    target_section: ResumeTargetSection
    target_role_relevance: TargetRoleRelevance
    matched_jd_requirements: list[str] = Field(default_factory=list, max_length=1_000)
    source_fact_ids: list[str] = Field(min_length=1, max_length=1_000)
    source_log_ids: list[str] = Field(min_length=1, max_length=1_000)
    source_self_claim_ids: list[str] = Field(default_factory=list, max_length=1_000)
    verification_status: VerificationStatus
    unsupported_phrases: list[str] = Field(default_factory=list, max_length=1_000)
    verifier_reason: Optional[str] = None

    @field_validator("id", "branch_id")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("text")
    @classmethod
    def text_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator(
        "matched_jd_requirements",
        "source_fact_ids",
        "source_log_ids",
        "source_self_claim_ids",
    )
    @classmethod
    def typed_id_list_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_structural(member)
        if len(value) != len(set(value)):
            raise ValueError("duplicate typed ID")
        return value

    @field_validator("unsupported_phrases")
    @classmethod
    def unsupported_phrase_policy(cls, value: list[str]) -> list[str]:
        for member in value:
            validate_free_text(member, nonempty=True)
        return value

    @field_validator("verifier_reason")
    @classmethod
    def verifier_reason_policy(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_free_text(value, nonempty=True)
