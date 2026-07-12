## §11. Pydantic Domain Models

§11 defines every persisted §9.1 ontology entity. `VerificationFinding` (§11.14) is append-only verifier-attempt history; denormalized verification fields on its targets remain the operational state. Storage-only artifacts (join tables, telemetry, and recomputable-row production provenance) have no models here; their DDL and derivation are normative in §12.

Every top-level entity `id` is a service-assigned, opaque, non-empty value that is immutable for the lifetime of the workspace, unique within its entity table, and never reused in that table, including after supersession or owner deletion (§12 rule 11). No producer contract may let a model author an entity ID; for an LLM-backed producer, the service assigns it only after a valid model response as deterministic enrichment under §15.1. This per-table contract does not weaken the stronger global uniqueness of embedded `JDRequirement.id` (§11.13).

Every persisted entity model below other than `RawLog` carries a system-assigned `created_at: datetime`, set when the entity is first persisted. `RawLog.recorded_at` retains its §5.4 meaning as the time the raw record entered Exp2Res. A creation timestamp does not substitute for the production provenance defined below.

Every recomputable entity — `ExperienceFact`, `SelfSignal`, `SelfClaim`, `AssessmentSnapshot`, `ResumeBullet`, `Contradiction`, `GapQuestion`, and `ResumeBranch` — also carries `superseded_at: Optional[datetime] = None`. `None` means the row belongs to the one current generation for its replacement identity — the correction lineage for facts, the global Stage 4 generation for gaps and contradictions, the global Stage 5 generation for signals, the assessment view (§11.7) for claims and snapshots, and the branch name for branches and bullets; a timestamp makes it historical. A normal rerun or correction sets this field once instead of rewriting payload or provenance. New stages, verification, generation, and export use only current rows. `JobDescription` is retained context, not a recomputed interpretation. Owner deletion is the privacy exception: §13.13 purges current and historical recomputable rows rather than retaining superseded copies.

Production provenance for those eight recomputable entities is storage-level under §12 rule 13: `produced_by_run_id` and `generation_id` have no §11 model counterpart and are hydrated only by inspection surfaces. §15 LLM contracts receive complete persisted §11 shapes and never see or set either storage-only value.

An `AssessmentSnapshot`'s assessment payload and provenance are immutable after creation. Stage 7 alone may update `verification_status` while the snapshot is current, and `superseded_at` may make its one-way lifecycle transition; neither field may rewrite the document or its source lists. A superseded snapshot remains inspectable history after correction but cannot feed verification, resume generation, or export. Owner deletion may purge it under the stronger privacy rule. `SelfClaim`, `AssessmentSnapshot`, and `ResumeBullet` are the only entities whose denormalized `VerificationStatus` is operational state; `VerificationFinding.status` is inspect-only history, and §16.11 defines the meanings and consumer gates.

## §11.1 OccurredAt

```python
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

class OccurredAt(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    precision: TemporalPrecision
    confidence: TemporalConfidence
```

`OccurredAt.precision` is the sole discriminator for temporal shape; there is no separate `kind`. For `exact_datetime`, `exact_day`, `week`, `month`, `quarter`, and `year`, `start` is required and `end` must be `None`. For `date_range` and `approximate_range`, both bounds are required and `end` must be strictly after `start` — a zero-width period is not a range and must be expressed as `exact_datetime` or `exact_day`, so range widths under §16.7 are always positive. For `unknown`, both bounds must be `None`. `OccurredAt.confidence` expresses confidence only in temporal placement; it is independent of general claim `Confidence`.

For provenance containment, a non-range `start` is the anchor of the normative uncertainty interval defined in §16.7; it is not silently re-aligned by an extractor.

## §11.2 RawLog

```python
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

class RawLog(BaseModel):
    id: str
    recorded_at: datetime
    entry_type: EntryType
    source_type: SourceType
    occurred: OccurredAt
    raw_text: str
    project: Optional[str] = None
    external_ref: Optional[str] = None
    corrects_log_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

`corrects_log_id` is a capture-time requirement, not a standing invariant: §14.4 must set it when a correction is captured (`entry_type == "correction"`), it must then resolve to an existing `RawLog`, and it must not create a correction cycle. Correction text must be self-contained. A correction row with `corrects_log_id = None` is nevertheless a valid model state that hydration, §12 rule 10 validation, and the §13.13 rebuild accept: it arises only when owner deletion removes the target (§12 rule 6 `ON DELETE SET NULL`), and such an orphaned correction is the root of its own correction lineage (§13.3 rule 10). No flow other than owner deletion may null or rewrite the field.

## §11.3 EvidenceItem

```python
class EvidenceItem(BaseModel):
    id: str
    created_at: datetime
    raw_log_id: str
    title: Optional[str] = None
    summary: str
    uri: Optional[str] = None
    path: Optional[str] = None
    strength: EvidenceStrength
    metadata: dict = Field(default_factory=dict)
```

## §11.4 ExperienceFact

```python
class ExperienceFact(BaseModel):
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

    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)

    occurred: OccurredAt
    source_log_ids: list[str] = Field(min_length=1)
    evidence_item_ids: list[str] = Field(min_length=1)

    confidence: Confidence
    metadata: dict = Field(default_factory=dict)
```

`source_log_ids` and `evidence_item_ids` are non-empty, duplicate-free views hydrated from §12.4 rather than stored on `experience_facts`. Every selected evidence item belongs to a raw log in `source_log_ids`, and every listed source log is represented by at least one selected evidence item. The two views must agree with the `fact_sources → evidence_items` relation exactly.

## §11.5 SelfSignal

```python
class SelfSignal(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    signal_type: SignalType
    statement: str
    supporting_fact_ids: list[str]
    counter_fact_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    metadata: dict = Field(default_factory=dict)
```

## §11.6 SelfClaim

```python
class CounterevidenceItem(BaseModel):
    statement: str = Field(min_length=1)
    source_ref_type: CounterevidenceRefType
    source_ref_id: str = Field(min_length=1)

class SelfClaim(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    claim: str
    claim_kind: ClaimKind
    dimension: SelfClaimDimension
    source_signal_ids: list[str]
    source_fact_ids: list[str]
    confidence: Confidence
    verification_status: VerificationStatus
    counterevidence: list[CounterevidenceItem] = Field(default_factory=list)
    uncertainty: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

`CounterevidenceItem` is an embedded typed annotation, not an ontology entity. `statement` is the verifier-authored contrary-evidence prose and remains generated voice under §16.12; (`source_ref_type`, `source_ref_id`) is its polymorphic grounding reference. Stage 7 persists the validated §15.5 list: each reference must resolve under §12 rule 10 to the table its type selects and must be a member of that claim's supplied §15.5 bundle — closure, `scope_facts`, or `scope_signals` — so the verifier cannot ground contrary evidence outside what it received, while an omitted contrary view member stays navigably citable. Entries are duplicate-free by (`source_ref_type`, `source_ref_id`); one grounding source carries one consolidated statement.

## §11.7 AssessmentSnapshot

```python
class AssessmentSnapshot(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    scope: AssessmentScope
    scope_target: Optional[str] = None
    title: str
    summary: str
    self_claim_ids: list[str]
    gap_question_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    metadata: dict = Field(default_factory=dict)
```

`contradiction_ids` is the complete duplicate-free set of current Stage 4 contradictions at this snapshot's synthesis boundary. Stage 6 does not scope-filter that set. There is no contradiction-status filter; §12 rule 10 rejects duplicate, missing, or superseded IDs.

For `scope = "project"`, `scope_target` is required and is the canonical §14.9 `--project` value — Unicode NFC, leading/trailing whitespace trimmed, non-blank — persisted before case folding; the assessment writer cannot author or normalize it. For `global` it is `None`. A (`scope`, case-folded canonical `scope_target`) pair is an assessment view, the snapshot replacement identity under §13.6: one snapshot is current per view, and distinct views — `global` and each project target — are simultaneously current. The target remains a user-supplied scope label, not an entity reference; renaming a project starts a new view rather than migrating an old one.

`gap_question_ids` is the duplicate-free complete set of current unanswered (`answered = false`) Stage 4 gaps at the snapshot's synthesis boundary and must exactly match the validated §15.4 `unknowns` output. Stage 6 does not scope-filter or permit the writer to omit an open gap; an answered current row is deliberately excluded because it is no longer an unknown even before Stage 4 regeneration. The output carries references only: the stored unknown content remains the referenced current `GapQuestion.question`, `reason`, `priority`, and target. Known-gap assertions are status-bearing `SelfClaim` rows, not free prose on the snapshot. At the Stage 6 transaction boundary, missing, duplicate, superseded, answered, omitted, or output-inconsistent gap references fail under §12 rule 10 and the Stage 6 transaction checks.

A later `gaps answer` on a referenced question is normal owner activity, not snapshot corruption: it supersedes no derived row and blocks no consumer, and `gap_question_ids` remains the honest record of what was unknown at synthesis; stale managed exports are refreshed by §14.7's removal-and-report rule, not by superseding the snapshot. The answered state becomes visible read-time context under §17, and the next Stage 6 generation excludes the answered row. Read-time consumers re-validate reference integrity — resolvable, duplicate-free, current rows — but never fail a current snapshot because a referenced gap was answered after synthesis.

Exactly one claim in `self_claim_ids` has `claim_kind = "narrative_summary"`, and its `claim` equals `AssessmentSnapshot.summary`. The summary is therefore ordinary verified claim prose, not an unverified snapshot-level escape hatch.

## §11.8 ResumeBullet

```python
class ResumeBullet(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    branch_id: str
    text: str
    target_section: ResumeTargetSection
    target_role_relevance: TargetRoleRelevance
    matched_jd_requirements: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(min_length=1)
    source_log_ids: list[str] = Field(min_length=1)
    source_self_claim_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    unsupported_phrases: list[str] = Field(default_factory=list)
    verifier_reason: Optional[str] = None
```

Stage 6 initializes new `SelfClaim.verification_status` and `AssessmentSnapshot.verification_status` values; Stage 7 owns their verifier transitions. Stage 10 initializes `ResumeBullet.verification_status`; Stage 11 owns its verifier transition. The exact initial value and all consumer permissions are defined in §13.6–§13.11 and §16.11.

`source_self_claim_ids` follows the exact-use contract in §13.10/§15.6: it is the duplicate-free exact set of self-claims passed to the writer for that bullet and is empty iff the bullet was generated from facts alone.

Every `matched_jd_requirements` entry is a stable `JDRequirement.id` from the exact `ParsedJD` supplied to Stage 10. The list is duplicate-free and is validated under §12 rule 10 plus the Stage 10 selected-job-description check; free-form requirement labels are invalid in this field.

## §11.9 Contradiction

```python
class Contradiction(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    title: str
    description: str

    left_ref_type: DetectionRefType
    left_ref_id: str
    right_ref_type: DetectionRefType
    right_ref_id: str

    metadata: dict = Field(default_factory=dict)
```

A current `Contradiction` is an immutable conflict detection owned by Stage 4, not a workflow item with an in-place verdict. Stage 4 owns the current set under §13.4's retain-or-replace rule: a content-equivalent rerun that qualifies for retention under that rule retains the current rows, while on a replacing regeneration a continuing conflict receives a replacement row and a conflict absent from current evidence is omitted. Prior rows become superseded inspect-only history; owner deletion may purge them under §13.13.

## §11.10 GapQuestion

```python
class GapQuestion(BaseModel):
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
```

## §11.11 JobDescription

```python
class JobDescription(BaseModel):
    id: str
    created_at: datetime

    title: Optional[str] = None
    company: Optional[str] = None
    raw_text: str
    parsed: "ParsedJD"
```

## §11.12 ResumeBranch

```python
class ResumeBranch(BaseModel):
    id: str
    name: str
    assessment_snapshot_id: str
    job_description_id: str

    created_at: datetime
    superseded_at: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)
```

`assessment_snapshot_id` is the required exact anchor selected under the canonical resume rule in §18. It has no implicit-latest or absent state.

`job_description_id` is the required exact §14.10 `--jd` selection copied by Stage 10; verification and export recover the typed requirements through that persisted ID. It has no implicit or absent state.

## §11.13 Parsed Job Description

```python
class JDRequirement(BaseModel):
    id: str = Field(min_length=1)
    kind: JDRequirementKind
    text: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)

class ParsedJD(BaseModel):
    requirements: list[JDRequirement] = Field(default_factory=list)
    seniority_signals: list[str] = Field(default_factory=list)
    domain_signals: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)

JobDescription.model_rebuild()
```

`JDRequirement.id` is a service-assigned opaque ID, globally unique and immutable after its containing `JobDescription` is persisted; it is never an array index or model-authored prose. Requirement IDs are duplicate-free within `ParsedJD`. Required skills, preferred skills, and responsibilities are represented only as `requirements` with the canonical `JDRequirementKind` (§10); signals, keywords, and red flags are typed context but are not matchable requirement targets.

`ParsedJD` is an embedded Pydantic model, not an independently persisted ontology entity. `JobDescription.parsed` is quoted because this appended subsection defines the type later in the module; `model_rebuild()` resolves that forward reference after both classes exist. Stage 8 validates the parser candidate, assigns requirement IDs, validates the final `ParsedJD`, and persists it atomically with its `JobDescription` under §12 and §15.9.

## §11.14 VerificationFinding

```python
class VerificationFinding(BaseModel):
    id: str
    created_at: datetime
    produced_by_run_id: str
    target_type: VerificationTargetRefType
    target_id: str
    status: VerificationStatus
    reason: str
    unsupported_phrases: list[str] = Field(default_factory=list)
    suggested_rewrite: Optional[str] = None
    counterevidence: list[CounterevidenceItem] = Field(default_factory=list)
```

Verification findings are append-only history with no `superseded_at`; their payload is immutable after persistence until the §13.13 owner-deletion purge. Each completed Stage 7 or Stage 11 verifier attempt writes exactly one finding per verified target in the same transaction as that target's denormalized status update. Stage 7 findings target `SelfClaim`, Stage 11 findings target `ResumeBullet`, and the derived §16.11 snapshot aggregate receives no finding row. A failed attempt writes no finding (§13.7, §13.11).

`produced_by_run_id` is an explicit model field here, unlike production provenance on the eight recomputable entities: the persisted finding shape never crosses the LLM boundary, and the owning verifier run is part of the finding's semantics. The verifier contract returns only its declared payload; the service assigns the finding ID, creation time, owning run, and typed target.

The denormalized fields on `SelfClaim`, `ResumeBullet`, and `AssessmentSnapshot` remain the sole operational state consumed by §16.11 gates. Findings are inspect-only: they are never a writer input, never a §15.4 or §15.6 input, never any later prompt input, and never §17 or §18 export content. `suggested_rewrite` is persisted only in this history; it remains advisory and is never applied.

---
