"""Closed implemented subset of the §14.14 version-1 result envelope."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
import shlex

from pydantic import ConfigDict, Field, model_validator

from exp2res.llm.contracts import ContractWarning

from .enums import (
    AssessmentScope,
    CLIResultStatus,
    EntryType,
    SourceType,
    VerificationStatus,
)
from .models import (
    AssessmentSnapshot,
    Contradiction,
    ExperienceFact,
    GapQuestion,
    OccurredAt,
    SelfSignal,
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
    "extract",
    "logs list",
    "logs delete",
    "facts list",
    "facts show",
    "detections generate",
    "signals generate",
    "signals list",
    "assess generate",
    "assess verify",
    "assess list",
    "assess show",
    "export assessment",
    "gaps list",
    "gaps answer",
    "contradictions list",
    "contradictions show",
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


class SignalsListResult(StrictModel):
    signals: list[SelfSignal]


class InvalidatedView(StrictModel):
    scope: AssessmentScope
    scope_target: str | None
    snapshot_id: str
    regeneration_command: str


def invalidated_view(
    *, scope: AssessmentScope, scope_target: str | None, snapshot_id: str
) -> InvalidatedView:
    command = "exp2res assess generate"
    if scope == "project":
        assert scope_target is not None
        command += f" --scope project --project {shlex.quote(scope_target)}"
    return InvalidatedView(
        scope=scope,
        scope_target=scope_target,
        snapshot_id=snapshot_id,
        regeneration_command=command,
    )


class SnapshotListItem(StrictModel):
    id: str
    scope: AssessmentScope
    scope_target: str | None
    verification_status: VerificationStatus
    created_at: datetime


class AssessListResult(StrictModel):
    snapshots: list[SnapshotListItem]


class AssessShowResult(StrictModel):
    snapshot: AssessmentSnapshot
    claims: list[SelfClaim]
    gaps: list[GapQuestion]
    contradictions: list[Contradiction]


class AssessmentExportResult(StrictModel):
    manifest_path: str
    managed_paths: list[str]


ResultPayload = (
    SchemaResult
    | LogsListResult
    | LogsDeleteResult
    | FactsListResult
    | DetectionsGenerateResult
    | GapsListResult
    | ContradictionsResult
    | SignalsListResult
    | AssessListResult
    | AssessShowResult
    | AssessmentExportResult
)


class CLIEnvelope(StrictModel):
    # The envelope is immutable like other strict boundary objects, but complete
    # lists are not capped because §14.14 explicitly exempts local result output.
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, validate_assignment=True
    )

    envelope_version: Literal[1] = 1
    command: CommandPath | None
    status: CLIResultStatus
    exit_code: int
    diagnostic_class: str | None
    workspace: str | None
    affected_ids: AffectedIds = Field(default_factory=AffectedIds)
    generation_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    invalidated_views: list[InvalidatedView] = Field(default_factory=list)
    invalidated_branches: list[Any] = Field(default_factory=list)
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
