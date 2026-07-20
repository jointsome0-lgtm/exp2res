"""Strict §11 models implemented through the current Stage 3 schema slice."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import PurePosixPath
import re
import unicodedata
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    ActivityContext,
    ClaimKind,
    Confidence,
    DetectionRefType,
    EntryType,
    EvidenceStrength,
    GapPriority,
    GapTrigger,
    OwnershipLevel,
    SourceType,
    TemporalConfidence,
    TemporalPrecision,
)

RAW_TEXT_LIMIT = 1_048_576
STRING_LIMIT = 16_384
METADATA_LIMIT = 4_096
METADATA_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def canonical_project_key(label: str) -> str:
    """Return §12 rule 14's one canonical project comparison identity."""

    return unicodedata.normalize("NFC", label).strip().casefold()


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


def validate_free_text(value: str, *, raw: bool = False, nonempty: bool = False) -> str:
    if nonempty and not value:
        raise ValueError("empty text")
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


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_assignment=True,
    )


class OccurredAt(StrictModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    precision: TemporalPrecision
    confidence: TemporalConfidence

    @field_validator("start", "end")
    @classmethod
    def aware_datetime(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must carry an offset")
        return value

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
        elif self.precision in {"date_range", "approximate_range"}:
            if self.start is None or self.end is None or self.end <= self.start:
                raise ValueError("invalid temporal range")
        elif self.precision == "unknown":
            if self.start is not None or self.end is not None:
                raise ValueError("unknown precision cannot carry bounds")
        return self


class RawLog(StrictModel):
    id: str
    recorded_at: datetime
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

    @field_validator("recorded_at")
    @classmethod
    def recorded_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must carry an offset")
        return value

    @field_validator("raw_text")
    @classmethod
    def raw_text_policy(cls, value: str) -> str:
        return validate_free_text(value, raw=True, nonempty=True)

    @field_validator("metadata")
    @classmethod
    def metadata_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class EvidenceItem(StrictModel):
    id: str
    created_at: datetime
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

    @field_validator("created_at")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must carry an offset")
        return value

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
        return validate_metadata(value)


class ExperienceFact(StrictModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
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

    @field_validator("created_at", "superseded_at")
    @classmethod
    def timestamps_are_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must carry an offset")
        return value

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


class Contradiction(StrictModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
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

    @field_validator("created_at", "superseded_at")
    @classmethod
    def timestamps_are_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must carry an offset")
        return value

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
    created_at: datetime
    superseded_at: Optional[datetime] = None

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

    @field_validator("created_at", "superseded_at")
    @classmethod
    def timestamps_are_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must carry an offset")
        return value

    @field_validator("question")
    @classmethod
    def question_policy(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)
