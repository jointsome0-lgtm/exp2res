## §11. Pydantic Domain Models

§11 defines every persisted §9.1 ontology entity. `VerificationFinding` (§11.14) is append-only verifier-attempt history; denormalized verification fields on its targets remain the operational state. Storage-only artifacts (join tables, telemetry, and recomputable-row production provenance) have no models here; their DDL and derivation are normative in §12.

Every top-level entity `id` is a service-assigned, opaque, non-empty value that is immutable for the lifetime of the workspace, unique within its entity table, and never reused in that table, including after supersession or owner deletion (§12 rule 11). No producer contract may let a model author an entity ID; for an LLM-backed producer, the service assigns it only after a valid model response as deterministic enrichment under §15.1. This per-table contract does not weaken the stronger global uniqueness of embedded `JDRequirement.id` (§11.13).

Every persisted entity model below other than `RawLog` carries a system-assigned `created_at: datetime`, set when the entity is first persisted. `RawLog.recorded_at` retains its §5.4 meaning as the time the raw record entered Exp2Res. A creation timestamp does not substitute for the production provenance defined below.

Every recomputable entity — `ExperienceFact`, `SelfSignal`, `SelfClaim`, `AssessmentSnapshot`, `ResumeBullet`, `Contradiction`, `GapQuestion`, and `ResumeBranch` — also carries `superseded_at: Optional[datetime] = None`. `None` means the row belongs to the one current generation for its replacement identity — the correction lineage for facts, the global Stage 4 generation for gaps and contradictions, the global Stage 5 generation for signals, the assessment view (§11.7) for claims and snapshots, and the case-folded branch name (§14.10) for branches and bullets; a timestamp makes it historical. A normal rerun or correction sets this field once instead of rewriting payload or provenance. New stages, verification, generation, and export use only current rows. `JobDescription` is retained context, not a recomputed interpretation. Owner deletion is the privacy exception: §13.13 purges current and historical recomputable rows rather than retaining superseded copies.

Production provenance for those eight recomputable entities is storage-level under §12 rule 13: `produced_by_run_id` and `generation_id` have no §11 model counterpart and are hydrated only by inspection surfaces. Every §15 LLM-contract input is drawn from §11 shapes exactly as the receiving contract declares it — a complete persisted shape, or a declared narrower projection such as the §15.6/§15.7 parsed job-description view (never `JobDescription.raw_text`) or a §13.3 rule 10 displaced-record support descriptor. A declared projection may not be widened toward the complete entity, and because every transmitted shape is a §11 shape or a projection of one, no §15 contract ever sees or sets either storage-only value.

An `AssessmentSnapshot`'s assessment payload and provenance are immutable after creation. Stage 7 alone may update `verification_status` while the snapshot is current, and `superseded_at` may make its one-way lifecycle transition; neither field may rewrite the document or its source lists. A superseded snapshot remains inspectable history after correction but cannot feed verification, resume generation, or export. Owner deletion may purge it under the stronger privacy rule. `SelfClaim`, `AssessmentSnapshot`, and `ResumeBullet` are the only entities whose denormalized `VerificationStatus` is operational state; `VerificationFinding.status` is inspect-only history, and §16.11 defines the meanings and consumer gates.

### Model validation policy

Every top-level and embedded §11 model and every outer or nested §15/§19 transport shape uses one common validation policy. Each `BaseModel` declaration shown below is shorthand for that configured base rather than Pydantic's defaults, and every shown `metadata: dict` field is shorthand for the bounded entity-metadata shape defined here. Undeclared fields are rejected (`extra = forbid`). Validation is strict: a value must already have its declared type, with exactly one boundary coercion. When values arrive as JSON — an LLM response, an import payload, or SQLite JSON/ISO-TEXT hydration — an ISO 8601 string may be parsed into a declared `datetime` field. That string must carry an explicit UTC offset — `Z` or numeric `±hh:mm` — and the requirement is value-level: every accepted `datetime` is offset-aware however it arrives, and a naive value fails validation at transport, at hydration, and at direct construction alike. Model validation never consults a workspace or platform timezone; §14.14's Time input resolution rule may resolve naive owner CLI input before model construction and validation. No other cross-type coercion is permitted: strings, integers, booleans, and floats do not bridge in either direction, and truthiness never substitutes for a boolean. SQLite first performs §12's normative storage-representation decoding — for example, an `INTEGER` 0/1 boolean column becomes a JSON boolean — and then validates the reconstructed shape through this same JSON-boundary mode; representation decoding is not model coercion, and storage and transport use one rule set.

Assignment validation is enabled. A constructed model instance is immutable to ordinary assignment. Only the lifecycle-owned field on an entity for which §11/§13 already defines the owning transition may change: `superseded_at`; `SelfClaim.verification_status` and `counterevidence`; `AssessmentSnapshot.verification_status`; `ResumeBullet.verification_status`, `unsupported_phrases`, and `verifier_reason`; and `GapQuestion.answered` and `answer_log_id`. Those changes occur only through their owning stage transition. Same-named fields on another model gain no mutation right; in particular, a `VerificationFinding` remains immutable. This is a model-instance assignment policy: a storage referential action already defined by §12 rehydrates a newly validated state rather than mutating an existing instance.

Canonical serialization uses UTF-8 JSON and declared field names only. For the §12.15 `input_hash` and `output_hash`, the exact byte form is pinned: object keys are sorted by code point and insignificant whitespace is omitted; every `datetime` value is normalized to UTC and rendered as `YYYY-MM-DDThh:mm:ss.ffffffZ` with exactly six zero-padded fractional digits, including all-zero digits, so equal instants recorded under different offsets serialize to identical bytes, and this normalization is total because validation admits only offset-aware `datetime` values — a naive value can never reach hash serialization; strings serialize their validated code points with no case, normalization, or other transformation, non-ASCII code points are emitted as raw UTF-8 rather than `\uXXXX` escapes, and only mandatory JSON escapes are used — `\"`, `\\`, the defined two-character forms (`\b`, `\f`, `\n`, `\r`, `\t`), and lowercase `\u00xx` for any remaining control character; numbers are integers in minimal decimal form, while `true`, `false`, and `null` use their JSON literals. No §11 model declares a float-typed field, and introducing one requires first pinning its canonical rendering here. The hash function is SHA-256 over those bytes, stored as lowercase hexadecimal. The datetime rule governs hash bytes only and does not change any stored or displayed value. Two conforming implementations therefore hash identical validated inputs and outputs identically. This paragraph governs hash-input bytes only; §12 rule 3 governs stored-offset retention and UTC-instant comparison, §14.14's Time input resolution rule governs workspace-timezone interpretation before model validation, and the Unicode policy below governs normalization and comparison outside hashing.

For each producing or transition operation, every persisted field has exactly one authorship class. Model-authored values are exactly the fields declared by the applicable §15 output shape. Importer-authored values are exactly the mappings declared by the applicable §19 contract. Owner-authored values include `raw_text`, correction and answer text, and configuration. Service-owned persisted fields include IDs, timestamps, lifecycle fields, production provenance, paths, and entity `metadata`. A declared verifier `status`, `counterevidence`, `unsupported_phrases`, or `reason` is a model-authored transition result, not direct assignment to the same-named or mapped persisted lifecycle field; the owning service alone validates and applies that result. Authorship follows the declared shape and operation, not matching key spelling. A model response that sets a service-owned persisted field outside its declared transition result or sets any undeclared field is invalid structured output.

Entity `metadata` is a bounded, inert service/importer channel; §12.13 `processing_runs.metadata_json` is separate execution telemetry governed only by that subsection. Only deterministic service code authors entity metadata. Capture/import commands (§14.2–§14.5 and §14.7), including §19 importers, may supply a validated copied value; every LLM-backed producer service supplies the persisted empty value. No §15 output shape contains `metadata`, and an LLM response that supplies it is invalid structured output. A §19 importer may pass through a source payload's metadata object only when its source contract declares that field and the value passes this policy; the result remains inert provenance.

A metadata key can never carry authority, control, selection, or lifecycle state unless one specification section names both its producer and its consumer, applying to keys the same producer-closure principle reflected in §10's enum domains. The V1 named keys are `question_text` and `question_reason` on a gap-answer `RawLog`, produced by §14.7 and consumed by §15.2; `source_system`, `source_record_id`, and `content_hash` on an imported `RawLog`, produced by a §19.4 importer and consumed only by §19.4's retained-identity duplicate/conflict check; and `content_digest` on an imported `EvidenceItem`, produced by a §19.4 importer and consumed only by §19.4's integrity check at an explicitly authorized §29.4 dereference. The import identity keys are non-empty structural strings; `content_hash` and `content_digest` are exactly the lowercase SHA-256 hexadecimal forms defined by §19.4. The digest remains inert for authority, control, selection, and lifecycle purposes. A §19.4 source metadata object containing a reserved import key for the target entity is invalid rather than overwritten, and the final service-mapped metadata remains subject to the limits below. The same key names from any other producer remain inert. Every object within entity metadata has at most 16 keys. A key is non-empty lowercase ASCII snake case matching `^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$` and is at most 64 characters. A value is a JSON scalar, an array of scalars, or one nested object whose values are scalars or arrays of scalars; arrays and objects cannot nest further. The canonical serialized metadata is at most 4 KiB (4,096 UTF-8 bytes) per entity. The §14.7 copied pair fits that budget by construction: `question_text` copies a `GapQuestion.question` bounded at 1,024 UTF-8 bytes whose free-text hygiene admits no character that canonical serialization expands beyond two bytes, and `question_reason` copies a `GapTrigger` literal, so the copied metadata object stays under 2.2 KiB even at maximal escaping and a persisted gap can never be unanswerable under the metadata limit.

The following limits apply at every external boundary: LLM inputs and responses, import payloads, owner-supplied files, and SQLite hydration.

```text
raw_text: at most 1 MiB (1,048,576 UTF-8 bytes) for one source document or payload read into the field
GapQuestion.question: at most 1,024 UTF-8 bytes
every other string field: at most 16 KiB (16,384 UTF-8 bytes)
each list field: at most 1,000 items
each payload: at most 10,000 total objects
JSON nesting: at most 32 levels
each warnings list and each findings list: at most 100 entries
typed ID lists: duplicate-free under their existing rules
each string-list member: non-empty
```

Exceeding a limit is a deterministic local failure: an input fails preflight before any provider call; a model response is invalid structured output; an import or owner-supplied file fails at acquisition; and a stored row fails closed at hydration. Stored JSON is not grandfathered around validation or limits (§12 rule 2).

Every string rejects NUL. Structural strings — IDs, enum values, metadata keys, names, paths, and selectors — also reject every C0/C1 control character. Free-text strings — including `raw_text`, claims, statements, summaries, and questions — permit tabs and newlines but reject every other control character. An inert metadata string follows free-text hygiene unless a named-key contract types it as structural. Accepted source text is never normalized or rewritten and retains the byte-for-byte preservation required by §16.12 and §19. Generated prose is stored as its validated Unicode code points; the service applies no Unicode normalization, and canonical hash bytes remain governed by the serialization rule above.

Comparison identity uses Unicode NFC normalization followed by locale-independent Unicode Default Case Folding only at the named identity points: scope-target and assessment-view/project matching (§14.9, §11.7, §13.6), view-slug derivation (§13.12), and branch replacement and selection (§14.10, §11.12). The separately named leading/trailing whitespace trim in §14.9 and percent encoding in §13.12 still apply. Project provenance remains copied exactly under §13.3 rule 13 and is transformed only for §13.6 comparison. No other identifier, selector, label, duplicate comparison, or prose string receives implicit normalization or case folding; strings differing only by normalization form or case remain distinct wherever no owning rule names a fold. Locale-dependent casing, including Turkish-I special casing, is forbidden; "locale-independent case fold" means Unicode Default Case Folding.

A non-null `path` field value must use POSIX path syntax under §29.4; a Windows drive-letter, UNC, or backslash-separated form fails the same structural validation as unsupported.

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

`OccurredAt.precision` is the sole discriminator for temporal shape; there is no separate `kind`. For `exact_datetime`, `exact_day`, `week`, `month`, `quarter`, and `year`, `start` is required and `end` must be `None`. For `date_range` and `approximate_range`, both bounds are required and `end` must be strictly after `start` — a zero-width period is not a range and must be expressed as `exact_datetime` or `exact_day`, so range widths under §16.7 are always positive. For `unknown`, both bounds must be `None`. `OccurredAt.confidence` expresses confidence only in temporal placement; it is independent of general claim `Confidence`. Bounds follow the validation policy's offset-aware requirement at every precision: a coarse-precision bound is stored as an offset-aware instant whose time-of-day is representational, and `precision` alone carries the temporal meaning, so a midnight bound under `exact_day` or a range precision states nothing narrower than the labeled precision (§16.7, §21.7).

For provenance containment, a non-range `start` is the anchor of the normative uncertainty interval defined in §16.7; it is not silently re-aligned by an extractor.

Calendar anchors use ISO 8601 semantics: a week starts on Monday, and quarters are Q1 January–March, Q2 April–June, Q3 July–September, and Q4 October–December. When a CLI or importer normalizes a named period such as `June 2026`, `2026-W23`, or `Q2 2026` into `OccurredAt`, `start` is that period's first instant in the operation's resolving timezone (§14.14 for owner CLI input) and `precision` is respectively `month`, `week`, or `quarter`. Normalization of a named period aligns that derived anchor; an accepted non-aligned anchor remains legal and is never silently re-aligned. The widths in §16.7 are maximum-uncertainty widths for comparison, not calendar-period widths.

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

`corrects_log_id` is a capture-time requirement, not a standing invariant: §14.4 must set it when a correction is captured (`entry_type == "correction"`), it must then resolve to an existing `RawLog`, and it must not create a correction cycle. §13.3 rule 10 defines the field's whole-record displacement, effective-record, governing-record, and orphan re-rooting consequences. Correction text must be self-contained. A correction row with `corrects_log_id = None` is nevertheless a valid model state that hydration, §12 rule 10 validation, and the §13.13 rebuild accept: it arises only when owner deletion removes the target (§12 rule 6 `ON DELETE SET NULL`), and such an orphaned correction is the root of its own correction lineage (§13.3 rule 10). No flow other than owner deletion may null or rewrite the field.

V1 reserves, but does not implement, a future human-only `private` marker as either a field or named metadata key; when introduced, it must bind at the §15 input-assembly boundary and exclude the marked `RawLog` from every LLM-stage input, including an otherwise user-initiated run.

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

`name` keeps the owner's spelling; replacement identity and `--branch` selection use its NFC case-folded form (§14.10), and no two current branches fold equal.

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
