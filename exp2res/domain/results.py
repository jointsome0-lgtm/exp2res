"""Closed implemented subset of the §14.14 version-1 result envelope."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
from typing import Iterable, Literal

from pydantic import ConfigDict, Field, model_validator

from exp2res.llm.contracts import ContractWarning

from .canonical import byte_sorted, id_key
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
    BoundaryDatetime,
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
    "import file",
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


def _entity_groups(
    pairs: Iterable[tuple[str, Iterable[str]]]
) -> list[EntityIdGroup]:
    merged: dict[str, set[str]] = {}
    for entity_type, ids in pairs:
        merged.setdefault(entity_type, set()).update(ids)
    return [
        EntityIdGroup(entity_type=entity_type, ids=list(byte_sorted(ids)))
        for entity_type, ids in merged.items()
        if ids
    ]


class AffectedIds(StrictModel):
    created: list[EntityIdGroup] = Field(default_factory=list)
    superseded: list[EntityIdGroup] = Field(default_factory=list)
    deleted: list[EntityIdGroup] = Field(default_factory=list)

    @classmethod
    def of(
        cls,
        *,
        created: Iterable[tuple[str, Iterable[str]]] = (),
        superseded: Iterable[tuple[str, Iterable[str]]] = (),
        deleted: Iterable[tuple[str, Iterable[str]]] = (),
    ) -> AffectedIds:
        """Build the three §14.14 rule 5 lists from `(entity_type, ids)` pairs.

        Rule 5 wants entity groups duplicate-free and deterministically ordered
        by class and identity, and omits a class with no ID to report. All
        three happen here once, instead of being restated correctly at each
        composition: pairs repeating a class merge into its first-seen
        position, IDs order by `id_key`, and an empty class drops out.

        Class ordering is otherwise the caller's pair order, which is fixed per
        command. The one caller that merges several stages' reports has no such
        order to inherit and sorts its class names at the call site.
        """

        return cls(
            created=_entity_groups(created),
            superseded=_entity_groups(superseded),
            deleted=_entity_groups(deleted),
        )


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
    recorded_at: BoundaryDatetime
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
    created_at: BoundaryDatetime
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


def merged_invalidated_views(*collections) -> list[InvalidatedView]:
    by_id = {
        item.snapshot_id: item for collection in collections for item in collection
    }
    return [by_id[key] for key in sorted(by_id, key=id_key)]


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


def merged_invalidated_branches(*collections) -> list[InvalidatedBranch]:
    # §13.13 rule 9 identifies a branch report by the branch name: one command
    # may supersede the same named branch through several lifecycle steps.
    by_name = {item.name: item for collection in collections for item in collection}
    return [by_name[key] for key in sorted(by_name, key=id_key)]


class SnapshotListItem(StrictModel):
    id: str
    scope: AssessmentScope
    verification_status: VerificationStatus
    created_at: BoundaryDatetime


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
    created_at: BoundaryDatetime
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


@dataclass
class Outcome:
    """What a command produced, before §14 turns it into a `CLIEnvelope`.

    Each stage and service that owns a result type also owns the projection
    from that result into this shape, so adding a stage stays a one-file
    change; §14 composes the envelope and never re-derives a projection.
    """

    exit_code: int = 0
    diagnostic_class: str | None = None
    affected_ids: AffectedIds = field(default_factory=AffectedIds)
    generation_ids: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    invalidated_views: list[InvalidatedView] = field(default_factory=list)
    invalidated_branches: list[InvalidatedBranch] = field(default_factory=list)
    findings: list[VerificationFinding] = field(default_factory=list)
    residual_paths: list[str] = field(default_factory=list)
    warnings: list[ContractWarning] = field(default_factory=list)
    result: ResultPayload | None = None
    human_result: str = ""
    retry: Retry | None = None
    # §14.14 rule 4: a nonzero *completion* — today only a fully classified
    # §19.4 import result with rejections — rather than a rejected input.
    # Class 8 covers every non-cancelled completion, so a residual observed
    # beside one promotes exactly as it does for class 0 and class 10.
    completed_report: bool = False


def merge_outcomes(*outcomes: Outcome) -> Outcome:
    """One §14.14 rule 5 report from several stages' projections.

    Effects merge duplicate-free in their identity order: entity classes by
    name (the stages share no pair order to inherit), IDs by `id_key`, paths
    by `os.fsencode`, one view per snapshot and — §13.13 rule 9 — one report
    per branch name. Run IDs keep allocation order; warnings and findings
    concatenate. The scalar fields (exit, result, text) are the first outcome's.
    """

    def classes(field_name: str) -> list[tuple[str, set[str]]]:
        grouped: dict[str, set[str]] = {}
        for outcome in outcomes:
            for group in getattr(outcome.affected_ids, field_name):
                grouped.setdefault(group.entity_type, set()).update(group.ids)
        return sorted(grouped.items())

    return replace(
        outcomes[0],
        affected_ids=AffectedIds.of(
            created=classes("created"),
            superseded=classes("superseded"),
            deleted=classes("deleted"),
        ),
        generation_ids=list(
            byte_sorted({item for outcome in outcomes for item in outcome.generation_ids})
        ),
        run_ids=list(
            dict.fromkeys(item for outcome in outcomes for item in outcome.run_ids)
        ),
        invalidated_views=merged_invalidated_views(
            *(outcome.invalidated_views for outcome in outcomes)
        ),
        invalidated_branches=merged_invalidated_branches(
            *(outcome.invalidated_branches for outcome in outcomes)
        ),
        findings=[item for outcome in outcomes for item in outcome.findings],
        residual_paths=sorted(
            {item for outcome in outcomes for item in outcome.residual_paths},
            key=os.fsencode,
        ),
        warnings=[item for outcome in outcomes for item in outcome.warnings],
    )


# §14.14 rules 5/6 require a failed or cancelled command to report the effects
# it already committed. An exception is the only channel a raising operation
# shares with §14's emitter, so the projection rides on it — as one whole
# `Outcome` under this single slot, never as loose per-field attributes. Keeping
# the slot untyped here is deliberate: `exp2res/errors.py` is the package's only
# import-free leaf, and declaring the field there would bind the bottom of the
# import graph to pydantic, the domain models and the LLM contract layer.
_COMMITTED_SLOT = "committed_outcome"


def committed_outcome(error: BaseException) -> Outcome:
    """What `error` already committed, or an empty projection.

    `ManagedOutputIncompleteError` declares `residual_paths` itself — naming
    the paths it failed to clean up is that error's whole identity, not a
    projection someone attached to it — so the two sources are merged here.
    §14.14 rule 5 wants the complete set, and either alone can be partial.
    """

    carried = getattr(error, _COMMITTED_SLOT, None)
    outcome = carried if isinstance(carried, Outcome) else Outcome()
    declared = getattr(error, "residual_paths", None)
    if declared:
        return replace(
            outcome,
            residual_paths=sorted(
                {*outcome.residual_paths, *declared}, key=os.fsencode
            ),
        )
    return outcome


def carry_committed(error: BaseException, outcome: Outcome) -> None:
    """Record a complete committed projection on `error`, replacing any prior."""

    setattr(error, _COMMITTED_SLOT, outcome)


def extend_committed(error: BaseException, **fields: object) -> None:
    """Merge further committed effects onto what `error` already carries.

    Layering matters: an inner service records the processing runs it created
    before an outer command adds the swap those runs produced, and neither
    knows what the other contributed.
    """

    carry_committed(error, replace(committed_outcome(error), **fields))


def render_text(value: str) -> str:
    """Escape one source-derived string into an unambiguous single line.

    The literal backslash is escaped first, so every escape this renderer
    introduces stays injective: a control byte and a name that literally
    spells its escape keep distinct rendered identities. Control characters
    are then escaped because a legal name may hold a newline, a tab, or a
    terminal escape sequence, and one record must stay one record in both
    the envelope and the human rendering. A real character takes the
    four-digit `\\uNNNN` form, keeping it distinct from the two-digit
    `\\xNN` an undecodable byte takes in `render_path`.
    """

    escaped = value.replace("\\", "\\\\")
    return "".join(
        character
        if not (
            ord(character) < 0x20
            or ord(character) == 0x7F
            or 0x80 <= ord(character) <= 0x9F
        )
        else f"\\u{ord(character):04x}"
        for character in escaped
    )


def render_path(path: str) -> str:
    """Render one filesystem path so the envelope can carry it losslessly.

    Undecodable POSIX names surface as surrogate-escaped strings that neither
    UTF-8 stdout nor the JSON envelope can encode, so they take the same
    backslash form as every other escape `render_text` applies.
    """

    return (
        render_text(path)
        .encode("utf-8", "surrogateescape")
        .decode("utf-8", "backslashreplace")
    )
