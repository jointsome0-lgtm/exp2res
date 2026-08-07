## §11. Pydantic Domain Models

§11 defines every persisted §9.1 ontology entity. `VerificationFinding` (§11.14) is append-only verifier-attempt history. Storage-only artifacts (join tables, telemetry, and recomputable-row production provenance) have no models here; their DDL and derivation are normative in §12.

Every top-level entity `id` follows §12 rule 11's identity contract: service-assigned only — never model-authored — opaque, immutable, unique, and never reused within its entity table; embedded `JDRequirement.id` keeps its stronger global uniqueness (§11.13).

Every persisted entity model below other than `RawLog` carries a system-assigned `created_at: datetime`, set when the entity is first persisted. `RawLog.recorded_at` retains its §5.4 meaning as the time the raw record entered Exp2Res. A creation timestamp does not substitute for the production provenance defined below.

Every recomputable entity — `ExperienceFact`, `SelfClaim`, `AssessmentSnapshot`, `ResumeBullet`, `Contradiction`, `GapQuestion`, and `ResumeBranch` — also carries `superseded_at: Optional[datetime] = None`. `None` means the row belongs to the one current generation for its replacement identity — the correction lineage for facts, the global Stage 4 generation for gaps and contradictions, the assessment view (§11.7) for claims and snapshots, and the case-folded branch name (§14.10) for branches and bullets; a timestamp makes it historical. A normal rerun or correction sets this field once instead of rewriting payload or provenance. New stages, verification, generation, and export use only current rows. `JobDescription` is retained context, not a recomputed interpretation. Owner deletion is the privacy exception: §13.13 purges current and historical recomputable rows rather than retaining superseded copies.

Production provenance for those seven recomputable entities — `produced_by_run_id` and `generation_id` — is storage-level under §12 rule 13. Every §15 LLM-contract input is drawn from §11 shapes exactly as the receiving contract declares it — a complete persisted shape, or a declared narrower projection such as the §15.6/§15.7 parsed job-description view (never `JobDescription.raw_text`) or a §13.3 rule 10 displaced-record support descriptor. A declared projection may not be widened toward the complete entity, and because every transmitted shape is a §11 shape or a projection of one, no §15 contract ever sees or sets either storage-only value.

An `AssessmentSnapshot`'s assessment payload and provenance are immutable after creation. Stage 7 alone may update `verification_status` while the snapshot is current, and `superseded_at` may make its one-way lifecycle transition; neither field may rewrite the document or its source lists. A superseded snapshot remains inspectable history after correction but cannot feed verification, resume generation, or export. Owner deletion may purge it under the stronger privacy rule. Operational verification state and finding-history semantics are defined in §11.14 and §16.11.

### Model validation policy

1. Every top-level and embedded §11 model and every outer or nested §15/§19 transport shape uses one common validation policy.
    - Each `BaseModel` declaration shown below is shorthand for that configured base rather than Pydantic's defaults.
    - Every shown `metadata: dict` field is shorthand for the bounded entity-metadata shape defined here.
2. Undeclared fields are rejected (`extra = forbid`).
3. Validation is strict: a value must already have its declared type, with exactly one boundary coercion. When values arrive as JSON — an LLM response, an import payload, or SQLite JSON/ISO-TEXT hydration — an ISO 8601 string may be parsed into a declared `datetime` field.
4. The parsed ISO 8601 string must carry an explicit UTC offset — `Z` or numeric `±hh:mm` — and the requirement is value-level: every accepted `datetime` is offset-aware however it arrives, and a naive value fails validation at transport, at hydration, and at direct construction alike.
5. Model validation never consults a workspace or platform timezone; §14.14's Time input resolution rule may resolve naive owner CLI input before model construction and validation.
6. No other cross-type coercion is permitted: strings, integers, booleans, and floats do not bridge in either direction, and truthiness never substitutes for a boolean.
7. SQLite first performs §12's normative storage-representation decoding — for example, an `INTEGER` 0/1 boolean column becomes a JSON boolean — and then validates the reconstructed shape through this same JSON-boundary mode. Representation decoding is not model coercion, and storage and transport use one rule set.
8. Assignment validation is enabled. A constructed model instance is immutable to ordinary assignment. Only the lifecycle-owned field on an entity for which §11/§13 already defines the owning transition may change:
    - `superseded_at`;
    - `SelfClaim.verification_status` and `counterevidence`;
    - `AssessmentSnapshot.verification_status`;
    - `ResumeBullet.verification_status`, `unsupported_phrases`, and `verifier_reason`;
    - `GapQuestion.answered` and `answer_log_id`.
9. Each field listed in rule 8 changes only through its owning stage transition.
10. Same-named fields on another model gain no mutation right; in particular, a `VerificationFinding` remains immutable.
11. Assignment immutability is a model-instance policy: a storage referential action already defined by §12 rehydrates a newly validated state rather than mutating an existing instance.
12. Canonical serialization uses UTF-8 JSON and declared field names only.
13. For the §12.15 `input_hash` and `output_hash`, the exact byte form is pinned:
    - Object keys are sorted by code point.
    - Insignificant whitespace is omitted.
    - Every `datetime` value is normalized to UTC and rendered as `YYYY-MM-DDThh:mm:ss.ffffffZ` with exactly six zero-padded fractional digits, including all-zero digits, so equal instants recorded under different offsets serialize to identical bytes. This normalization is total because validation admits only offset-aware `datetime` values — a naive value can never reach hash serialization.
    - Strings serialize their validated code points with no case, normalization, or other transformation, and non-ASCII code points are emitted as raw UTF-8 rather than `\uXXXX` escapes.
    - Only mandatory JSON escapes are used: `\"`, `\\`, the defined two-character forms (`\b`, `\f`, `\n`, `\r`, `\t`), and lowercase `\u00xx` for any remaining control character.
    - Numbers are integers in minimal decimal form, while `true`, `false`, and `null` use their JSON literals.
14. No §11 model declares a float-typed field, and introducing one requires first pinning its canonical rendering here.
15. The hash function is SHA-256 over those pinned bytes, stored as lowercase hexadecimal.
16. The datetime normalization-and-rendering rule governs hash bytes only and does not change any stored or displayed value.
17. Two conforming implementations therefore hash identical validated inputs and outputs identically.
18. Canonical serialization governs hash-input bytes only:
    - §12 rule 3 governs stored-offset retention and UTC-instant comparison.
    - §14.14's Time input resolution rule governs workspace-timezone interpretation before model validation.
    - The Unicode policy below governs normalization and comparison outside hashing.

19. For each producing or transition operation, every persisted field has exactly one authorship class:
    - Model-authored values are exactly the fields declared by the applicable §15 output shape.
    - Importer-authored values are exactly the mappings declared by the applicable §19 contract.
    - Owner-authored values include `raw_text`, correction and answer text, and configuration.
    - Service-owned persisted fields include IDs, timestamps, lifecycle fields, production provenance, paths, entity `metadata`, and the deterministic post-response copies and derivations §15.11's ownership matrix assigns to a producing stage — for example fact `project` and `source_log_ids`, snapshot `summary` and `gap_question_ids`, and bullet `source_log_ids` and `source_self_claim_ids`.
20. A declared verifier `status`, `counterevidence`, `unsupported_phrases`, or `reason` is a model-authored transition result, not direct assignment to the same-named or mapped persisted lifecycle field; the owning service alone validates and applies that result.
21. Authorship follows the declared shape and operation, not matching key spelling.
22. A model response that sets a service-owned persisted field outside its declared transition result or sets any undeclared field is invalid structured output.
23. Entity `metadata` is a bounded, inert service/importer channel; §12.13 `processing_runs.metadata_json` is separate execution telemetry governed only by that subsection.
24. Only deterministic service code authors entity metadata.
25. Capture/import commands (§14.2–§14.5 and §14.7), including §19 importers, may supply a validated copied value; every LLM-backed producer service supplies the persisted empty value.
26. No §15 output shape contains `metadata`, and an LLM response that supplies it is invalid structured output.
27. A §19 importer may pass through a source payload's metadata object only when its source contract declares that field and the value passes this policy; the result remains inert provenance.
28. A metadata key can never carry authority, control, selection, or lifecycle state unless one specification section names both its producer and its consumer, applying to keys the same producer-closure principle reflected in §10's enum domains.
29. The V1 named keys are:
    - `question_text` and `question_reason` on a gap-answer `RawLog`, produced by §14.7 and consumed by §15.2;
    - `source_system`, `source_record_id`, and `content_hash` on an imported `RawLog`, produced by a §19 importer and consumed only by §19.4's retained-identity duplicate check;
    - `content_digest` on an imported `EvidenceItem`, produced by a §19 importer and consumed only by §19.4's integrity check at an explicitly authorized §29.4 dereference;
    - `repaired_from_snapshot_id` on an `AssessmentSnapshot` plus `adopted_rewrite_of_claim_id` on a `SelfClaim`, produced only by §13.6's deterministic repair form and consumed by no V1 operation — inert repair provenance for inspection.
30. The import identity keys are non-empty structural strings; `content_hash` and `content_digest` are exactly the lowercase SHA-256 hexadecimal forms defined by §19.4.
31. The digest remains inert for authority, control, selection, and lifecycle purposes.
32. A §19 source metadata object containing a reserved import key for the target entity is invalid rather than overwritten, and the final service-mapped metadata remains subject to the limits below.
33. The same key names from any other producer remain inert.
34. Every object within entity metadata has at most 16 keys.
35. A key is non-empty lowercase ASCII snake case matching `^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$` and is at most 64 characters.
36. A value is a JSON scalar, an array of scalars, or one nested object whose values are scalars or arrays of scalars; arrays and objects cannot nest further.
37. The canonical serialized metadata is at most 4 KiB (4,096 UTF-8 bytes) per entity.

    The §14.7 copied pair fits that budget by construction: `question_text` copies a `GapQuestion.question` bounded at 1,024 UTF-8 bytes whose free-text hygiene admits no character that canonical serialization expands beyond two bytes, and `question_reason` copies a `GapTrigger` literal, so the copied metadata object stays under 2.2 KiB even at maximal escaping and a persisted gap can never be unanswerable under the metadata limit.

38. The following limits apply at every external boundary: LLM inputs and responses, import payloads, owner-supplied files, and SQLite hydration.

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

39. Exceeding a limit is a deterministic local failure:
    - an input fails preflight before any provider call;
    - a model response is invalid structured output;
    - an import or owner-supplied file fails at acquisition;
    - a stored row fails closed at hydration.
40. Stored JSON is not grandfathered around validation or limits (§12 rule 2).
41. Every string rejects NUL.
42. Structural strings — IDs, enum values, metadata keys, names, paths, and selectors — also reject every C0/C1 control character.
43. Free-text strings — including `raw_text`, claims, statements, summaries, and questions — permit tabs and newlines but reject every other control character.
44. An inert metadata string follows free-text hygiene unless a named-key contract types it as structural.
45. Accepted source text is never normalized or rewritten and retains the byte-for-byte preservation required by §16.12 and §19.
46. Generated prose is stored as its validated Unicode code points; the service applies no Unicode normalization, and canonical hash bytes remain governed by the serialization rules above.
47. Comparison identity uses Unicode NFC normalization followed by locale-independent Unicode Default Case Folding only at the named identity points: scope-target and assessment-view/project matching (§14.9, §11.7, §13.6), and branch replacement and selection (§14.10, §11.12).
48. The separately named leading/trailing whitespace trim in §14.9 still applies.
49. Project provenance remains copied exactly under §13.3 rule 13. Its comparison identity is computed once at persistence as the §12 rule 14 stored `project_key`. A non-null `project` value must remain non-blank after §14.9's canonicalization (Unicode NFC plus leading/trailing whitespace trim) — a label that canonicalizes to blank fails structural validation at every boundary.
50. Managed-output path keys are opaque service IDs under §13.14 and never derive from these comparison identities.
51. No other identifier, selector, label, duplicate comparison, or prose string receives implicit normalization or case folding; strings differing only by normalization form or case remain distinct wherever no owning rule names a fold.
52. Locale-dependent casing, including Turkish-I special casing, is forbidden; "locale-independent case fold" means Unicode Default Case Folding.
53. A non-null `path` field value must use POSIX path syntax under §29.4; a Windows drive-letter, UNC, or backslash-separated form fails the same structural validation as unsupported.

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

`OccurredAt.precision` is the sole discriminator for temporal shape; there is no separate `kind`. For `exact_datetime`, `exact_day`, `week`, `month`, `quarter`, and `year`, `start` is required and `end` must be `None`. For `date_range` and `approximate_range`, `start` is required and `end` is optional: a non-null `end` must be strictly after `start` — a zero-width period is not a range and must be expressed as `exact_datetime` or `exact_day`, so a closed range's width under §16.7 is always positive — while a `None` `end` is the open-ended shape defined below. For `unknown`, both bounds must be `None`. `OccurredAt.confidence` expresses confidence only in temporal placement; it is independent of general claim `Confidence`. Bounds follow the validation policy's offset-aware requirement at every precision: a coarse-precision bound is stored as an offset-aware instant whose time-of-day is representational, and `precision` alone carries the temporal meaning, so a midnight bound under `exact_day` or a range precision states nothing narrower than the labeled precision (§16.7, §21.7).

An **open-ended** placement — `end` is `None` at `date_range` or `approximate_range` — records activity that began at `start` and has no recorded end. It is the only shape for ongoing activity, and it is not `unknown`: `unknown` records no placement at all, while an open-ended value keeps its known start and its range flavor, so an exactly anchored ongoing period stays `date_range` and a fuzzily remembered one stays `approximate_range`. Openness is carried by the absent bound alone. V1 declares no `ongoing` marker field, no open-specific `TemporalPrecision` member, and no derived reading of a closed bound as "as of capture", so one truth has exactly one representation and `precision` keeps its role as the sole discriminator of which bounds a shape may carry. An open-ended value never asserts an end at capture time and never silently acquires one: it states no recorded end as of the `recorded_at` of the record carrying it. §16.7 defines the resulting unbounded comparison width and attested containment window, §17 owns its rendering, and the owner closes the period by capturing a correction that restates it with an `end` (§14.4).

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

V1 reserves, but does not implement, a future human-only `private` marker as either a field or named metadata key; when introduced, it must bind at the §15 input-assembly boundary and exclude the marked `RawLog` from every LLM-stage input, including an otherwise user-initiated run. The marker is reserved rather than pending: §29.2 owns the standing V1 answer for material the owner will not disclose, and this reservation adds no confidentiality control of its own.

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

`source_log_ids` and `evidence_item_ids` are hydrated views under §12 rule 8, agreeing exactly with §12.4's `fact_sources → evidence_items` relation.

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
    snapshot_id: str
    claim: str
    claim_kind: ClaimKind
    dimension: SelfClaimDimension
    source_fact_ids: list[str]
    counter_fact_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    verification_status: VerificationStatus
    counterevidence: list[CounterevidenceItem] = Field(default_factory=list)
    uncertainty: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

`snapshot_id` is the claim's owning `AssessmentSnapshot` (§11.7): a required service-owned reference Stage 6 assigns when it creates the claim generation, immutable for the row's lifetime. Ownership is one-to-many by construction — a claim row is created for exactly one snapshot and is never shared or re-parented, so current snapshots cannot share claim rows and no claim can be unowned. Deterministic claim ordering needs no separate position field: every ordering consumer — §13.12 export documents, §14.14 inspection results, §17 rendering — orders a snapshot's claims by `SelfClaim.id` ascending in UTF-8 byte order, and the service-assigned entity ID is that stable ordering field.

`counter_fact_ids` is service-derived at the Stage 6 boundary, never model-authored: the duplicate-free union of the claim's cited §15.4 patterns' `counter_fact_ids`, empty for a claim citing no patterns — under §15.4 only a `pattern_signal` claim cites any — and immutable for the row's lifetime. It is always a subset of `source_fact_ids` — §15.4's equality rule makes a pattern-citing claim's closure exactly its cited patterns' fact union — and it durably marks which closure members entered as contrary evidence, so §9.4's pattern-generalization caps stay re-checkable after the patterns are discarded and no §15.5, §17, or §13.12 consumer mistakes a contrary member for support.

`CounterevidenceItem` is an embedded typed annotation, not an ontology entity. `statement` is the verifier-authored contrary-evidence prose and remains generated voice under §16.12; (`source_ref_type`, `source_ref_id`) is its polymorphic grounding reference. Stage 7 persists the validated §15.5 list: each reference must resolve under §12 rule 10 to the table its type selects and must be a member of that claim's supplied §15.5 bundle — closure or `scope_facts` — so the verifier cannot ground contrary evidence outside what it received, while an omitted contrary view member stays navigably citable. Entries are duplicate-free by (`source_ref_type`, `source_ref_id`); one grounding source carries one consolidated statement.

## §11.7 AssessmentSnapshot

```python
class AssessmentSnapshot(BaseModel):
    id: str
    created_at: datetime
    superseded_at: Optional[datetime] = None
    scope: AssessmentScope
    title: str
    summary: str
    gap_question_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    metadata: dict = Field(default_factory=dict)
```

`contradiction_ids` is populated under §13.6's complete, unfiltered contradiction-set rule.

A snapshot's member claims are exactly the `self_claims` rows whose `snapshot_id` names it (§11.6); the snapshot persists no claim list, and member selection orders by claim ID under §11.6's ordering rule.

`scope` is the assessment view and the snapshot replacement identity under §13.6. V1 declares exactly one view, `global` (§10), so exactly one assessment snapshot is current at a time. `title` is service-derived under §13.6's deterministic rule.

`gap_question_ids` is service-populated under §13.6's exact unanswered-set rule. The field carries references only: the stored unknown content remains the referenced current `GapQuestion.question`, `reason`, `priority`, and target; known-gap assertions belong to §13.6's status-bearing claim output. At the Stage 6 transaction boundary, missing, duplicate, superseded, answered, or omitted gap references fail under §12 rule 10 and the Stage 6 transaction checks.

A later `gaps answer` on a referenced question is normal owner activity, not snapshot corruption: `gap_question_ids` remains the honest record of what was unknown at synthesis. §14.7 owns the answer transaction's no-supersession and stale-export semantics, §17 renders the answered-since-synthesis state, and the next Stage 6 generation excludes the answered row. Read-time consumers re-validate reference integrity — resolvable, duplicate-free, current rows — but never fail a current snapshot because a referenced gap was answered after synthesis.

The exactly-one `narrative_summary` member claim and the service-copied `summary` follow §13.6.

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

The initial `verification_status` values, their owning verifier transitions, and all consumer permissions are defined in §13.10, §13.11, and §16.11.

`source_self_claim_ids` follows the citation contract in §13.10/§15.6.

`matched_jd_requirements` production and validation follow §13.10 and §12 rule 10.

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

A current `Contradiction`'s retention, replacement, omission, and absence of any in-place verdict are owned by §13.4's retain-or-replace rule. Prior rows become superseded inspect-only history; owner deletion may purge them under §13.13.

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

`assessment_snapshot_id` follows the canonical snapshot-anchor rule in §18.

`job_description_id` follows §13.10's exact `--jd` association rule.

`name` storage and its NFC case-folded replacement/selection identity are defined in §14.10.

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

`produced_by_run_id` is an explicit model field here, unlike production provenance on the seven recomputable entities: the persisted finding shape never crosses the LLM boundary, and the owning verifier run is part of the finding's semantics. The verifier contract returns only its declared payload; the service assigns the finding ID, creation time, owning run, and typed target.

The denormalized fields on `SelfClaim`, `ResumeBullet`, and `AssessmentSnapshot` remain the sole operational state consumed by §16.11 gates. Findings are inspect-only except for §13.6's narrow deterministic repair consumer: they are never an LLM writer input, never a §15.4 or §15.6 input, never any prompt input, and never §17 or §18 export content. `suggested_rewrite` is persisted only in this history and remains advisory, with that one narrow deterministic consumer: §13.6's explicit repair form may adopt a claim's latest persisted rewrite as the text of a new `unverified` replacement claim, which then earns status only through ordinary Stage 7 re-verification. No other operation applies it, and it is still never a prompt input or export content.

---
