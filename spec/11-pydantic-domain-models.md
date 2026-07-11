## §11. Pydantic Domain Models

§11 defines the persisted domain entities: every §9.1 ontology entity except VerificationFinding, which is not persisted as its own entity — its transport shape is fixed by the verifier contracts (§15.5, §15.7) and its results are stored denormalized on the verified targets (verification_status fields, SelfClaim.counterevidence, ResumeBullet.unsupported_phrases / verifier_reason). Storage-only artifacts (join tables, telemetry) have no models here; their DDL is normative in §12.

Every persisted entity model below other than `RawLog` carries a system-assigned `created_at: datetime`, set when the entity is first persisted. `RawLog.recorded_at` retains its §5.4 meaning as the time the raw record entered Exp2Res. `processing_runs` records execution telemetry, not entity creation provenance.

Every recomputable entity — `ExperienceFact`, `SelfSignal`, `SelfClaim`, `AssessmentSnapshot`, `ResumeBullet`, `Contradiction`, `GapQuestion`, and `ResumeBranch` — also carries `superseded_at: Optional[datetime] = None`. `None` means the row belongs to the one current generation for its scope; a timestamp makes it historical. A normal rerun or correction sets this field once instead of rewriting payload or provenance. New stages, verification, generation, and export use only current rows. `JobDescription` is retained context, not a recomputed interpretation. Owner deletion is the privacy exception: §13.13 purges current and historical recomputable rows rather than retaining superseded copies.

An `AssessmentSnapshot`'s assessment payload and provenance are immutable after creation. Stage 7 alone may update `verification_status` while the snapshot is current, and `superseded_at` may make its one-way lifecycle transition; neither field may rewrite the document or its source lists. A superseded snapshot remains inspectable history after correction but cannot feed verification, resume generation, or export. Owner deletion may purge it under the stronger privacy rule.

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
    verification_status: VerificationStatus
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
    counterevidence: list[str] = Field(default_factory=list)
    uncertainty: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

## §11.7 AssessmentSnapshot

```python
class AssessmentSnapshot(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    scope: AssessmentScope
    title: str
    summary: str
    self_claim_ids: list[str]
    gap_question_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    metadata: dict = Field(default_factory=dict)
```

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
    source_fact_ids: list[str]
    source_log_ids: list[str]
    source_self_claim_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    unsupported_phrases: list[str] = Field(default_factory=list)
    verifier_reason: Optional[str] = None
```

## §11.9 Contradiction

```python
class Contradiction(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    title: str
    description: str

    left_ref_type: EntityRefType
    left_ref_id: str
    right_ref_type: EntityRefType
    right_ref_id: str

    status: ContradictionStatus
    resolution_note: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

## §11.10 GapQuestion

```python
class GapQuestion(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None

    target_type: EntityRefType
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
    parsed: dict = Field(default_factory=dict)
```

## §11.12 ResumeBranch

```python
class ResumeBranch(BaseModel):
    id: str
    name: str
    job_description_id: Optional[str] = None
    assessment_snapshot_id: Optional[str] = None

    created_at: datetime
    superseded_at: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)
```

---
