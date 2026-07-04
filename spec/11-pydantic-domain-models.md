## §11. Pydantic Domain Models

§11 defines the persisted domain entities: every §9.1 ontology entity except VerificationFinding, which is not persisted as its own entity — its transport shape is fixed by the verifier contracts (§15.5, §15.7) and its results are stored denormalized on the verified targets (verification_status fields, SelfClaim.counterevidence, ResumeBullet.unsupported_phrases / verifier_reason). Storage-only artifacts (join tables, telemetry) have no models here; their DDL is normative in §12.

## §11.1 OccurredAt

```python
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, Literal

class OccurredAt(BaseModel):
    kind: Literal[
        "exact_datetime",
        "exact_day",
        "date_range",
        "approximate_range",
        "unknown",
    ]
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    precision: TemporalPrecision
    confidence: TemporalConfidence
```

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
    metadata: dict = Field(default_factory=dict)
```

## §11.3 EvidenceItem

```python
class EvidenceItem(BaseModel):
    id: str
    raw_log_id: str
    evidence_type: str
    title: Optional[str] = None
    summary: str
    uri: Optional[str] = None
    path: Optional[str] = None
    strength: str
    metadata: dict = Field(default_factory=dict)
```

## §11.4 ExperienceFact

```python
class ExperienceFact(BaseModel):
    id: str
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
    source_log_ids: list[str]
    evidence_item_ids: list[str] = Field(default_factory=list)

    confidence: TemporalConfidence
    verification_status: VerificationStatus
    metadata: dict = Field(default_factory=dict)
```

## §11.5 SelfSignal

```python
class SelfSignal(BaseModel):
    id: str
    signal_type: Literal[
        "skill_signal",
        "interest_signal",
        "direction_signal",
        "execution_pattern",
        "avoidance_pattern",
        "constraint_signal",
        "capacity_signal",
        "contradiction_signal",
    ]
    statement: str
    supporting_fact_ids: list[str]
    counter_fact_ids: list[str] = Field(default_factory=list)
    confidence: TemporalConfidence
    metadata: dict = Field(default_factory=dict)
```

## §11.6 SelfClaim

```python
class SelfClaim(BaseModel):
    id: str
    claim: str
    claim_kind: ClaimKind
    dimension: Literal[
        "technical_skill",
        "domain_interest",
        "working_style",
        "execution_capacity",
        "constraint",
        "risk",
        "gap",
        "trajectory",
        "identity_hypothesis",
    ]
    source_signal_ids: list[str]
    source_fact_ids: list[str]
    confidence: TemporalConfidence
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
    scope: Literal["global", "project", "career", "learning", "custom"]
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
    branch_id: str
    text: str
    target_section: Literal[
        "summary",
        "professional_experience",
        "selected_projects",
        "competitions",
        "skills",
        "education",
    ]
    target_role_relevance: Literal["low", "medium", "high"]
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
    title: str
    description: str

    left_ref_type: EntityRefType
    left_ref_id: str
    right_ref_type: EntityRefType
    right_ref_id: str

    status: Literal["open", "resolved", "dismissed"]
    resolution_note: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

## §11.10 GapQuestion

```python
class GapQuestion(BaseModel):
    id: str
    created_at: datetime

    target_type: EntityRefType
    target_id: str

    question: str
    reason: GapTrigger
    priority: Literal["low", "medium", "high"]

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
    metadata: dict = Field(default_factory=dict)
```

---

