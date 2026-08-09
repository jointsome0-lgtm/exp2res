"""§19.1 activity-domain evidence importer."""

from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

from pydantic import field_validator

from exp2res.domain.models import (
    OccurredAt,
    canonical_project_key,
    validate_free_text,
    validate_structural,
)
from exp2res.integrations.records import (
    EvidencePlan,
    ImportPlan,
    PlanContext,
    SourceContract,
    SourceRecord,
    optional_identity,
)

EVIDENCE_SUMMARY = "Imported activity-domain event."


class EphemerisRecord(SourceRecord):
    """§19.1's complete accepted record: six required fields, nothing else."""

    source: Literal["ephemeris"]
    record_id: str
    domain: Literal["activity"]
    occurred: OccurredAt
    project: str
    text: str

    @field_validator("record_id", "project")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("project")
    @classmethod
    def project_is_not_canonical_blank(cls, value: str) -> str:
        if not canonical_project_key(value):
            raise ValueError("project label canonicalizes to blank")
        return value

    @field_validator("text")
    @classmethod
    def source_voice(cls, value: str) -> str:
        # §19 preserves this value as system-of-record source voice, and it
        # maps whole to `RawLog.raw_text` under that field's own bound.
        return validate_free_text(value, raw=True, nonempty=True)

    @property
    def source_identity(self) -> str:
        return self.record_id


def raw_identity(raw: Mapping[str, Any]) -> Optional[str]:
    return optional_identity(raw.get("record_id"))


def plan(record: EphemerisRecord, context: PlanContext) -> ImportPlan:
    return ImportPlan(
        entry_type="ephemeris_event",
        source_type="imported_event",
        occurred=record.occurred,
        raw_text=record.text,
        project=record.project,
        evidence=(
            EvidencePlan(
                summary=EVIDENCE_SUMMARY,
                strength="imported_activity_event",
            ),
        ),
    )


CONTRACT = SourceContract(
    source_system="ephemeris",
    record_model=EphemerisRecord,
    multi_record=True,
    raw_identity=raw_identity,
    plan=plan,
)
