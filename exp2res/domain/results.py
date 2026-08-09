"""Closed implemented subset of the §14.14 version-1 result envelope."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from exp2res.llm.contracts import ContractWarning

from .enums import (
    AssessmentScope,
    CLIResultStatus,
    EntryType,
    EvidenceStrength,
    SourceType,
    VerificationStatus,
)
from .models import (
    AssessmentSnapshot,
    Contradiction,
    ExperienceFact,
    GapQuestion,
    OccurredAt,
    SelfClaim,
    StrictModel,
    VerificationFinding,
)

CommandPath = Literal[
    "init",
    "db status",
    "db migrate",
    "log today",
    "log retro",
    "correction add",
    "recompute",
    "extract",
    "import ephemeris",
    "import atlas",
    "import github",
    "logs list",
    "logs show",
    "logs delete",
    "workspace purge",
    "facts list",
    "facts show",
    "detections generate",
    "assess generate",
    "assess repair",
    "assess verify",
    "assess list",
    "assess show",
    "export assessment",
    "gaps list",
    "gaps answer",
    "contradictions list",
    "contradictions show",
    "jd add",
    "jd list",
    "jd delete",
    "bullets generate",
    "bullets verify",
    "bullets export",
    "view serve",
]


class EntityIdGroup(StrictModel):
    entity_type: str
    ids: list[str]


class AffectedIds(StrictModel):
    created: list[EntityIdGroup] = Field(default_factory=list)
    superseded: list[EntityIdGroup] = Field(default_factory=list)
    deleted: list[EntityIdGroup] = Field(default_factory=list)


class Retry(StrictModel):
    command: str


class SchemaProjection(StrictModel):
    stored_version: int | None
    supported_version: int
    recognized: bool
    compatible: bool
    migration_path_available: bool | None
    managed_backup_path: str | None


class SchemaResult(StrictModel):
    schema_value: SchemaProjection = Field(alias="schema")


class LogProjection(StrictModel):
    id: str
    recorded_at: datetime
    entry_type: EntryType
    source_type: SourceType
    occurred: OccurredAt
    project: str | None
    corrects_log_id: str | None


class SelectedLogProjection(LogProjection):
    external_ref: str | None


class LogsListResult(StrictModel):
    logs: list[LogProjection]


class ImportRecordResult(StrictModel):
    """§14.14 rule 5's closed §19.4 record projection; no rejection reason.

    `record_number` is this record's stable identity in the report, so a
    repeated source identity stays a distinct entry.
    """

    record_number: int
    source_record_id: str | None
    raw_log_id: str | None


class ImportCounts(StrictModel):
    accepted: int
    duplicate: int
    rejected: int


class ImportRecordGroups(StrictModel):
    accepted: list[ImportRecordResult]
    duplicate: list[ImportRecordResult]
    rejected: list[ImportRecordResult]


class ImportResult(StrictModel):
    counts: ImportCounts
    records: ImportRecordGroups


class EvidenceItemProjection(StrictModel):
    id: str
    created_at: datetime
    raw_log_id: str
    title: str | None
    summary: str
    uri: str | None
    path: str | None
    strength: EvidenceStrength


class LogsShowResult(StrictModel):
    log: SelectedLogProjection
    evidence_items: list[EvidenceItemProjection]


class LogsDeleteResult(StrictModel):
    selected_log: SelectedLogProjection


class FactsListResult(StrictModel):
    facts: list[ExperienceFact]


class DetectionsGenerateResult(StrictModel):
    gaps: list[GapQuestion]
    contradictions: list[Contradiction]


class GapsListResult(StrictModel):
    gaps: list[GapQuestion]


class ContradictionsResult(StrictModel):
    contradictions: list[Contradiction]


class InvalidatedView(StrictModel):
    scope: AssessmentScope
    snapshot_id: str
    regeneration_command: str


def invalidated_view(*, scope: AssessmentScope, snapshot_id: str) -> InvalidatedView:
    return InvalidatedView(
        scope=scope,
        snapshot_id=snapshot_id,
        regeneration_command="exp2res assess generate",
    )


def posix_single_quote(value: str) -> str:
    """Quote one printed argument value under §13.13 rule 9.

    Unconditional single-quote wrapping, with an embedded single quote spelled
    `'\\''`, so a branch name carrying whitespace or shell metacharacters stays
    copy-paste-safe and selects the exact stored value.
    """

    return "'" + value.replace("'", "'\\''") + "'"


class FormerViewProjection(StrictModel):
    scope: AssessmentScope
    snapshot_id: str


class InvalidatedBranch(StrictModel):
    name: str
    job_description_id: str
    former_view: FormerViewProjection
    regeneration_command_shape: str


def invalidated_branch(
    *,
    name: str,
    job_description_id: str,
    scope: AssessmentScope,
    snapshot_id: str,
) -> InvalidatedBranch:
    # §13.13 rule 9: a shape, not an executable command — §14.10 requires a
    # current `--snapshot`, which exists only after the view is regenerated.
    shape = (
        "exp2res bullets generate --jd "
        + posix_single_quote(job_description_id)
        + " --snapshot <new-snapshot-id> --branch "
        + posix_single_quote(name)
    )
    return InvalidatedBranch(
        name=name,
        job_description_id=job_description_id,
        former_view=FormerViewProjection(scope=scope, snapshot_id=snapshot_id),
        regeneration_command_shape=shape,
    )


class SnapshotListItem(StrictModel):
    id: str
    scope: AssessmentScope
    verification_status: VerificationStatus
    created_at: datetime


class AssessListResult(StrictModel):
    snapshots: list[SnapshotListItem]


class AssessShowResult(StrictModel):
    snapshot: AssessmentSnapshot
    claims: list[SelfClaim]
    gaps: list[GapQuestion]
    contradictions: list[Contradiction]


class JobDescriptionProjection(StrictModel):
    """§14.14 rule 5's discovery projection: no `raw_text`, no `parsed`."""

    id: str
    created_at: datetime
    title: str | None
    company: str | None


class JdListResult(StrictModel):
    job_descriptions: list[JobDescriptionProjection]


class PurgedBranchProjection(StrictModel):
    id: str
    name: str


class JdDeleteResult(StrictModel):
    selected_job_description: JobDescriptionProjection
    purged_branches: list[PurgedBranchProjection]
    removed_managed_paths: list[str]


class AssessmentExportResult(StrictModel):
    manifest_path: str
    managed_paths: list[str]


ResultPayload = (
    SchemaResult
    | ImportResult
    | LogsListResult
    | LogsShowResult
    | LogsDeleteResult
    | FactsListResult
    | DetectionsGenerateResult
    | GapsListResult
    | ContradictionsResult
    | AssessListResult
    | AssessShowResult
    | JdListResult
    | JdDeleteResult
    | AssessmentExportResult
)


class CLIEnvelope(StrictModel):
    # The envelope is immutable like other strict boundary objects, but complete
    # lists are not capped because §14.14 explicitly exempts local result output.
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, validate_assignment=True
    )

    envelope_version: Literal[2] = 2
    command: CommandPath | None
    status: CLIResultStatus
    exit_code: int
    diagnostic_class: str | None
    workspace: str | None
    affected_ids: AffectedIds = Field(default_factory=AffectedIds)
    generation_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    invalidated_views: list[InvalidatedView] = Field(default_factory=list)
    invalidated_branches: list[InvalidatedBranch] = Field(default_factory=list)
    findings: list[VerificationFinding] = Field(default_factory=list)
    residual_paths: list[str] = Field(default_factory=list)
    warnings: list[ContractWarning] = Field(default_factory=list)
    retry: Retry | None = None
    result: ResultPayload | None = None

    @model_validator(mode="after")
    def status_matches_exit(self) -> "CLIEnvelope":
        expected = (
            "ok"
            if self.exit_code == 0
            else "cancelled"
            if self.exit_code == 9
            else "blocked"
            if self.exit_code == 10
            else "failed"
        )
        if self.status != expected:
            raise ValueError("status and exit code disagree")
        if (self.diagnostic_class is None) != (self.exit_code == 0):
            raise ValueError("diagnostic class and exit code disagree")
        return self
