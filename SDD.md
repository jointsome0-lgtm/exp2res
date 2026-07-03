# Exp2Res — System Design Document

**Version:** 0.2  
**Status:** Draft / implementation-oriented  
**Date:** 2026-07-03  
**Project:** Exp2Res — Experience to Self-Assessment to Resume  
**Primary goal:** Build a local-first, provenance-heavy self-assessment system from immutable experience evidence.  
**Secondary goal:** Generate job-targeted resume exports from the same evidence model without unsupported claims.  
**Primary user:** The developer using the system to understand himself honestly, orient through real experience, and optionally export a truthful resume for a specific vacancy.

---

## § Index

Section numbers are stable: issues and the Decision Log cite them as `§13` / `§13.2`. Never renumber. New sections take the next free number or a sub-number; update this index when sections change.

- §0 Change From v0.1 — recentering: mirror first, resume is a secondary export
- §1 Executive Summary — evidence → facts → signals → assessment → optional exports
- §2 Product Framing — weak framings to avoid; strong framing
- §3 Core Purpose — orientation, not impressiveness
- §4 Goals and Non-Goals — product/cognitive goals; forbidden inflations
- §5 Core Principles — truth over comfort; append-only; recorded_at ≠ occurred_at; no precision/ownership inflation; contradictions first-class; no automatic semantic promotion
- §6 System Boundaries — relations to Tick-like, Atlas, GitHub, resume export
- §7 High-Level Architecture — pipeline diagram
- §8 Runtime Architecture — Python, Typer, SQLite, Pydantic; CLI-first
- §9 Domain Model — ontology, claim kinds, confidence layers, evidence strength
- §10 Enumerations — Literal types: temporal, entry/source, ownership, context, claims, verification
- §11 Pydantic Domain Models — OccurredAt, RawLog, EvidenceItem, ExperienceFact, SelfSignal, SelfClaim, AssessmentSnapshot, ResumeBullet
- §12 SQLite Schema — raw_logs, evidence_items, experience_facts, fact_sources, signals/claims, snapshots, jd, resume, processing_runs
- §13 Pipeline Specification — 12 stages: capture → normalize → extract → gaps → signals → assess → verify → jd → match → generate → verify → export
- §14 CLI Specification — init, log, correction, import, extract, gaps, signals, assess, jd/match/resume/verify/export
- §15 LLM Contracts — structured I/O for extractor, signal extractor, assessment writer/verifier, resume writer/verifier
- §16 Verification Rules — evidence, mirror, anti-flattery, ownership, metric, production, temporal, employment, identity, diagnostic
- §17 Self-Assessment Report Format — mirror report skeleton and tone
- §18 Resume Export Rules — pipeline and export-fail conditions
- §19 Integration Contracts — Tick-like / Atlas / GitHub import behavior
- §20 Suggested Repository Structure — target file tree
- §21 Evals — 10 behavioral tests against overclaiming
- §22 Implementation Plan — Phase 0–5 with definitions of done
- §23 End-to-End Demo — retro log → facts → signal → claim → verified bullet
- §24 Acceptance Criteria — 14 V1 checks
- §25 Risks and Mitigations — resume-drift, flattery, punitive tone, overclaim, integration pollution, diagnosis
- §26 README Positioning — intro and taglines
- §27 Key Invariants — the non-negotiables list
- §28 Final Design Statement — three layers that must never collapse
- Decision Log — dated one-line decisions with rejected alternatives

---

## §0. Change From v0.1

The previous framing centered Exp2Res around grounded resume generation.

That resume pipeline remains important, but it is no longer the conceptual center.

The new center is:

```text
Raw lived experience
  -> immutable evidence
  -> extracted experience facts
  -> self-signals
  -> honest self-assessment
  -> optional external exports
```

Resume generation is a secondary projection of the internal truth model, not the reason the system exists.

Core correction:

```text
Exp2Res is not primarily a resume generator.
Exp2Res is a mirror: a system for complex, evidence-backed self-assessment.
```

The resume is only one export format:

```text
self-assessment model -> job-relevant projection -> grounded resume
```

---

## §1. Executive Summary

Exp2Res is a local-first system for converting lived experience into an honest, inspectable model of the user.

It stores raw logs, imported artifacts, notes, corrections, and answers as immutable records. From these records it extracts experience facts and then synthesizes a self-assessment model: skills, patterns, interests, constraints, recurring directions, evidence strength, gaps, contradictions, and uncertainty.

The system is designed around one central principle:

```text
The system must be honest before it is comforting.
```

A sweet false story may feel good, but if one detail changes — market feedback, a failed interview, a health constraint, a project collapse, a deadline, a contradiction — the false story can turn into a nightmare. Exp2Res should therefore preserve uncertainty, weakness, gaps, and counterevidence instead of smoothing them into a flattering narrative.

The resume pipeline remains in the system, but it is downstream:

```text
Self-assessment core
  -> relevant evidence selection
  -> job-description matching
  -> grounded resume bullets
  -> verifier loop
  -> export
```

Every external claim must remain traceable to internal evidence.

---

## §2. Product Framing

### §2.1 Weak Framing

Exp2Res should not be framed primarily as:

```text
AI resume builder
one-click CV generator
career branding tool
LinkedIn optimizer
self-promotion machine
productivity dashboard
```

These framings distort the system toward performance and external approval.

### §2.2 Strong Framing

Exp2Res is:

```text
A local-first, provenance-heavy self-assessment engine that can export grounded resumes from real experience.
```

Portfolio phrasing:

```text
Exp2Res builds an evidence-backed model of a person’s real experience and uses verifier-gated generation to prevent unsupported external claims.
```

Internal phrasing:

```text
Exp2Res is a mirror.
It shows what experience says about me, with evidence, uncertainty, gaps, and contradictions preserved.
```

---

## §3. Core Purpose

The user wants to move forward without burning himself out and without becoming trapped in endless meta-planning.

Exp2Res helps by making experience visible:

```text
What have I actually done?
What does it suggest about me?
What patterns repeat?
Where is my evidence strong?
Where am I guessing?
Where am I lying to myself?
What remains unknown?
What can honestly be exported to the outside world?
```

The system does not exist to make the user feel impressive.
It exists to make the user more oriented.

---

## §4. Goals and Non-Goals

## §4.1 Product Goals

Exp2Res should:

1. Store raw experience evidence in an append-only local log.
2. Support precise daily logs and imprecise retrospective reconstruction.
3. Import artifacts from local files, GitHub, Tick-like, Atlas, notes, and manual entries.
4. Extract atomic experience facts from raw records.
5. Track provenance from every fact back to raw evidence.
6. Preserve temporal precision and uncertainty.
7. Preserve ownership boundaries.
8. Detect gaps, contradictions, weak claims, and overclaims.
9. Build a complex self-assessment model from evidence.
10. Separate observed facts, inferred patterns, hypotheses, and narratives.
11. Generate gap questions that improve self-understanding.
12. Support self-assessment snapshots over time.
13. Parse job descriptions as optional external contexts.
14. Generate resume exports only from supported evidence.
15. Verify generated resume bullets phrase-by-phrase.
16. Export Markdown reports, evidence maps, verification reports, and resume files.

## §4.2 Cognitive Goals

Exp2Res should help the user say:

```text
I can see what my real experience shows.
I can distinguish evidence from fantasy.
I can see where I am strong, weak, uncertain, and contradictory.
I can export myself honestly when needed.
I do not need to invent a fake narrative to move.
```

## §4.3 Non-Goals

Exp2Res is not:

```text
therapy
mental-health diagnosis
personality test
self-esteem booster
motivational coach
productivity judge
public identity manager
social-status system
one-click resume SaaS
LinkedIn content generator
```

Exp2Res must not:

```text
hide uncertainty
inflate ownership
invent impact
invent metrics
turn independent work into employment
turn learning into mastery
turn interest into skill
turn aspiration into evidence
turn a flattering narrative into truth
```

---

## §5. Core Principles

## §5.1 Truth Over Comfort

The system should prefer an uncomfortable accurate model over a comforting false one.

Allowed output:

```text
You repeatedly return to provenance-heavy local-first systems.
Evidence is strong for design interest and architecture work.
Evidence is weaker for production deployment.
```

Forbidden output:

```text
You are an experienced production AI infrastructure engineer.
```

Unless that claim is supported by evidence.

## §5.2 Uncertainty Is a Valid State

The system must not force closure.

Valid states include:

```text
unknown
unclear
weakly supported
contradicted
requires review
hypothesis
```

A system that says “I don’t know” is more trustworthy than one that invents coherence.

## §5.3 Append-Only Experience Memory

All raw records are immutable.

If the user corrects a memory, Exp2Res stores a new correction event and recomputes downstream facts.

The old record is not deleted or silently edited.

## §5.4 recorded_at Is Not occurred_at

Every raw record has two independent time dimensions:

```text
recorded_at = when the record was added to Exp2Res
occurred_at = when the described experience happened
```

This allows retrospective reconstruction without pretending exact memory.

## §5.5 Temporal Precision Must Not Be Inflated

If the user remembers:

```text
around spring 2026
```

the system must not later claim:

```text
April 12, 2026
```

unless stronger evidence exists.

## §5.6 Ownership Must Not Be Inflated

Ownership levels are ordered roughly as:

```text
observed < studied < participated < experimented < contributed < implemented < built < designed < owned < led
```

The system may preserve or lower ownership confidence.
It must not upgrade ownership without evidence.

## §5.7 Experience Is Not Resume

A real experience can be messy, partial, private, emotional, exploratory, or uncertain.

A resume is a constrained external representation of selected experience.

Therefore:

```text
Experience model > self-assessment > export projection > resume
```

The resume must never become the master model.

## §5.8 Self-Assessment Is Not Identity

Exp2Res can say:

```text
Current evidence suggests a pattern.
```

It should not say:

```text
This is who you are forever.
```

Self-assessment snapshots are time-bounded and revisable.

## §5.9 Contradictions Are First-Class

If evidence conflicts, the system stores the conflict.

It should not smooth contradictions away.

Example:

```text
Signal A: user repeatedly designs ambitious architectures.
Signal B: user reports burnout when trying to execute even minimal plans.
Assessment: high architecture drive, limited sustainable execution capacity under pressure.
```

## §5.10 No Automatic Semantic Promotion

Across systems and internal stages:

```text
check-in ≠ evidence of skill
artifact ≠ mastery
interest ≠ competence
plan ≠ experience
experience fact ≠ resume claim
Atlas trail ≠ Exp2Res skill claim
Tick-like event ≠ self-assessment conclusion
```

Every promotion must be explicit, reviewed, and traceable.

---

## §6. System Boundaries

## §6.1 Relation to Tick-like

Tick-like is the operational surface of the day.

It can provide:

```text
daily notes
routine check-ins
activity events
focus sessions
manual notes
exported JSONL
```

Exp2Res can import Tick-like data as raw logs or weak signals.

But Tick-like events do not automatically become strong experience facts.

Example:

```text
Tick-like event:
  "Worked on Exp2Res verifier"

Exp2Res interpretation:
  raw_log candidate, weak evidence

Not automatically:
  "Designed a verifier architecture"
```

## §6.2 Relation to Atlas

Atlas is the knowledge-state atlas.

It can provide:

```text
concepts
directions
materials
trail segments
artifact refs
knowledge-state context
frontier context
```

Exp2Res can use Atlas to understand the conceptual context of experience.

But Atlas does not decide career/self claims.

Example:

```text
Atlas trail:
  REST API -> Idempotency

Exp2Res possible use:
  context for an experience fact

Not automatically:
  "Strong backend distributed systems skill"
```

## §6.3 Relation to GitHub

GitHub can provide strong artifact evidence:

```text
commits
pull requests
issues
README files
design docs
tests
source code
```

But code existence does not automatically imply impact, production use, leadership, or mastery.

## §6.4 Relation to Resume Export

Resume export is a projection.

It must be grounded in:

```text
raw logs
experience facts
self-assessment claims
artifact evidence
verification status
```

Resume output must not mutate the internal model.

---

## §7. High-Level Architecture

```text
                           +------------------------+
                           | Manual / Imported Logs |
                           +-----------+------------+
                                       |
                                       v
                           +------------------------+
                           | Append-only Raw Logs   |
                           +-----------+------------+
                                       |
                                       v
                           +------------------------+
                           | Evidence Normalization |
                           +-----------+------------+
                                       |
                                       v
                           +------------------------+
                           | Experience Fact Extract|
                           +-----------+------------+
                                       |
                  +--------------------+--------------------+
                  |                                         |
                  v                                         v
       +------------------------+              +------------------------+
       | Gap / Contradiction    |              | Self-Signal Extraction |
       | Detection              |              +-----------+------------+
       +-----------+------------+                          |
                   |                                       v
                   |                          +------------------------+
                   |                          | Self-Assessment Model  |
                   |                          +-----------+------------+
                   |                                      |
                   v                                      v
       +------------------------+              +------------------------+
       | Gap Questions / Review |              | Assessment Verifier    |
       +------------------------+              +-----------+------------+
                                                              |
                              +-------------------------------+------------------------------+
                              |                                                              |
                              v                                                              v
                  +------------------------+                                      +------------------------+
                  | Self Reports / Mirror  |                                      | Resume Export Pipeline |
                  +------------------------+                                      +------------------------+
```

---

## §8. Runtime Architecture

V1 should be local-first and CLI-first.

Recommended stack:

```text
Python
Typer
SQLite
Pydantic
pytest
Markdown
JSON
LLM provider abstraction
```

The system should be implementable without a web app.

All pipeline stages should be callable as testable service functions.

LLM use is allowed, but all LLM outputs must be structured, validated, and verified.

---

## §9. Domain Model

## §9.1 Ontology Overview

```text
RawLog              = immutable user/imported source record
EvidenceItem        = normalized referenceable evidence unit
ExperienceFact      = atomic statement about what happened
SelfSignal          = pattern signal derived from facts/evidence
SelfClaim           = assessment claim about the user, with confidence and sources
Contradiction       = conflict between claims, facts, or signals
GapQuestion         = question needed to improve weak/uncertain model
AssessmentSnapshot  = versioned self-assessment at a time
JobDescription      = external context for export
ResumeBranch        = job-targeted resume candidate branch
ResumeBullet        = generated resume phrase with evidence links
VerificationFinding = verifier output over claim/bullet/snapshot
```

## §9.2 Confidence Layers

Every non-raw claim should have a type:

```text
observed_fact
inferred_fact
pattern_signal
hypothesis
narrative_summary
export_claim
```

And confidence:

```text
low
medium
high
unknown
```

## §9.3 Evidence Strength

Evidence strength values:

```text
weak_note
manual_claim
imported_activity_event
artifact_reference
code_artifact
commit_or_pr
test_or_demo
design_doc
external_feedback
verified_outcome
```

Evidence strength is not the same as confidence.

A strong artifact may support a narrow fact, but not a broad identity claim.

---

## §10. Enumerations

```python
from typing import Literal

TemporalPrecision = Literal[
    "exact_datetime",
    "exact_day",
    "week",
    "month",
    "quarter",
    "year",
    "date_range",
    "approximate_range",
    "unknown",
]

TemporalConfidence = Literal["low", "medium", "high", "unknown"]

EntryType = Literal[
    "manual_daily",
    "manual_retro",
    "gap_answer",
    "correction",
    "tick_like_event",
    "atlas_artifact_ref",
    "atlas_trail_ref",
    "github_commit",
    "github_pr",
    "github_issue",
    "note_import",
    "design_doc",
    "competition_entry",
    "learning_entry",
]

SourceType = Literal[
    "manual_entry",
    "user_memory",
    "imported_artifact",
    "imported_event",
    "llm_inferred",
    "user_confirmed",
]

OwnershipLevel = Literal[
    "observed",
    "studied",
    "participated",
    "experimented",
    "contributed",
    "implemented",
    "built",
    "designed",
    "owned",
    "led",
    "unknown",
]

ActivityContext = Literal[
    "employment",
    "contract",
    "freelance",
    "independent_project",
    "open_source",
    "competition",
    "research",
    "learning",
    "personal_system",
    "unknown",
]

ClaimKind = Literal[
    "observed_fact",
    "inferred_fact",
    "pattern_signal",
    "hypothesis",
    "narrative_summary",
    "export_claim",
]

VerificationStatus = Literal[
    "unverified",
    "supported",
    "partially_supported",
    "inferred_but_acceptable",
    "needs_clarification",
    "contradicted",
    "unsupported",
    "rejected",
]
```

---

## §11. Pydantic Domain Models

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

---

## §12. SQLite Schema

## §12.1 raw_logs

```sql
CREATE TABLE IF NOT EXISTS raw_logs (
    id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,

    entry_type TEXT NOT NULL,
    source_type TEXT NOT NULL,

    occurred_kind TEXT NOT NULL,
    occurred_start TEXT,
    occurred_end TEXT,
    temporal_precision TEXT NOT NULL,
    temporal_confidence TEXT NOT NULL,

    project TEXT,
    external_ref TEXT,
    raw_text TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.2 evidence_items

```sql
CREATE TABLE IF NOT EXISTS evidence_items (
    id TEXT PRIMARY KEY,
    raw_log_id TEXT NOT NULL,

    evidence_type TEXT NOT NULL,
    title TEXT,
    summary TEXT NOT NULL,
    uri TEXT,
    path TEXT,
    strength TEXT NOT NULL,

    metadata_json TEXT NOT NULL DEFAULT '{}',

    FOREIGN KEY (raw_log_id) REFERENCES raw_logs(id)
);
```

## §12.3 experience_facts

```sql
CREATE TABLE IF NOT EXISTS experience_facts (
    id TEXT PRIMARY KEY,

    claim TEXT NOT NULL,
    claim_kind TEXT NOT NULL,

    project TEXT,
    role TEXT,
    company TEXT,
    context TEXT NOT NULL,
    ownership_level TEXT NOT NULL,

    action TEXT,
    object TEXT,
    outcome TEXT,

    skills_json TEXT NOT NULL DEFAULT '[]',
    technologies_json TEXT NOT NULL DEFAULT '[]',
    themes_json TEXT NOT NULL DEFAULT '[]',

    occurred_kind TEXT NOT NULL,
    occurred_start TEXT,
    occurred_end TEXT,
    temporal_precision TEXT NOT NULL,
    temporal_confidence TEXT NOT NULL,

    confidence TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.4 fact_sources

```sql
CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id TEXT NOT NULL,
    raw_log_id TEXT NOT NULL,
    evidence_item_id TEXT,
    support_type TEXT NOT NULL,

    PRIMARY KEY (fact_id, raw_log_id, support_type),

    FOREIGN KEY (fact_id) REFERENCES experience_facts(id),
    FOREIGN KEY (raw_log_id) REFERENCES raw_logs(id),
    FOREIGN KEY (evidence_item_id) REFERENCES evidence_items(id)
);
```

## §12.5 self_signals

```sql
CREATE TABLE IF NOT EXISTS self_signals (
    id TEXT PRIMARY KEY,
    signal_type TEXT NOT NULL,
    statement TEXT NOT NULL,
    supporting_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    counter_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.6 self_claims

```sql
CREATE TABLE IF NOT EXISTS self_claims (
    id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    claim_kind TEXT NOT NULL,
    dimension TEXT NOT NULL,

    source_signal_ids_json TEXT NOT NULL DEFAULT '[]',
    source_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    counterevidence_json TEXT NOT NULL DEFAULT '[]',

    confidence TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    uncertainty TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.7 contradictions

```sql
CREATE TABLE IF NOT EXISTS contradictions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,

    left_ref_type TEXT NOT NULL,
    left_ref_id TEXT NOT NULL,
    right_ref_type TEXT NOT NULL,
    right_ref_id TEXT NOT NULL,

    status TEXT NOT NULL,
    resolution_note TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.8 gap_questions

```sql
CREATE TABLE IF NOT EXISTS gap_questions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,

    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,

    question TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority TEXT NOT NULL,

    answered INTEGER NOT NULL DEFAULT 0,
    answer_log_id TEXT,

    FOREIGN KEY (answer_log_id) REFERENCES raw_logs(id)
);
```

## §12.9 assessment_snapshots

```sql
CREATE TABLE IF NOT EXISTS assessment_snapshots (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,

    self_claim_ids_json TEXT NOT NULL DEFAULT '[]',
    gap_question_ids_json TEXT NOT NULL DEFAULT '[]',
    contradiction_ids_json TEXT NOT NULL DEFAULT '[]',

    verification_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.10 job_descriptions

```sql
CREATE TABLE IF NOT EXISTS job_descriptions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,

    title TEXT,
    company TEXT,
    raw_text TEXT NOT NULL,
    parsed_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.11 resume_branches

```sql
CREATE TABLE IF NOT EXISTS resume_branches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    job_description_id TEXT,
    assessment_snapshot_id TEXT,

    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',

    FOREIGN KEY (job_description_id) REFERENCES job_descriptions(id),
    FOREIGN KEY (assessment_snapshot_id) REFERENCES assessment_snapshots(id)
);
```

## §12.12 resume_bullets

```sql
CREATE TABLE IF NOT EXISTS resume_bullets (
    id TEXT PRIMARY KEY,
    branch_id TEXT NOT NULL,

    text TEXT NOT NULL,
    target_section TEXT NOT NULL,
    target_role_relevance TEXT NOT NULL,

    matched_jd_requirements_json TEXT NOT NULL DEFAULT '[]',
    source_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    source_log_ids_json TEXT NOT NULL DEFAULT '[]',
    source_self_claim_ids_json TEXT NOT NULL DEFAULT '[]',

    verification_status TEXT NOT NULL,
    unsupported_phrases_json TEXT NOT NULL DEFAULT '[]',
    verifier_reason TEXT,

    created_at TEXT NOT NULL,

    FOREIGN KEY (branch_id) REFERENCES resume_branches(id)
);
```

## §12.13 processing_runs

```sql
CREATE TABLE IF NOT EXISTS processing_runs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    input_ids_json TEXT NOT NULL DEFAULT '[]',
    output_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

---

## §13. Pipeline Specification

## §13.1 Stage 1 — Raw Capture

Inputs:

```text
manual daily logs
manual retrospective logs
gap answers
corrections
Tick-like JSONL exports
Atlas artifact refs
Atlas trail refs
GitHub commits / PRs / issues
local design docs
notes
```

Outputs:

```text
raw_logs
evidence_items
```

Rules:

1. Raw text must be non-empty.
2. recorded_at must always be set.
3. occurred precision must always be explicit.
4. Imported artifacts must keep source URI/path.
5. Nothing is interpreted as a strong fact at capture time.

## §13.2 Stage 2 — Evidence Normalization

Purpose:

```text
Convert raw logs and imported artifacts into referenceable evidence units.
```

Example:

```text
GitHub commit -> EvidenceItem(code_artifact)
Tick-like daily note -> EvidenceItem(imported_activity_event)
Atlas artifact ref -> EvidenceItem(artifact_reference)
Manual retro memory -> EvidenceItem(manual_claim)
```

Normalization does not create self-claims.

## §13.3 Stage 3 — Experience Fact Extraction

Command:

```bash
exp2res extract
```

Input:

```text
raw_logs + evidence_items
```

Output:

```text
experience_facts
fact_sources
```

Rules:

1. Extract atomic claims.
2. Preserve temporal precision.
3. Preserve ownership level.
4. Do not infer metrics unless evidence explicitly contains metrics.
5. Do not infer production use unless explicitly present.
6. Every fact must link to at least one raw log.
7. Fact confidence must reflect source strength.

Bad fact:

```text
The user is strong at agent systems.
```

Good facts:

```text
The user designed a verifier loop for Exp2Res.
The user wrote a system design document for Atlas.
The user repeatedly worked with provenance-heavy local-first system ideas.
```

## §13.4 Stage 4 — Gap and Contradiction Detection

Command:

```bash
exp2res gaps
exp2res contradictions
```

Gap triggers:

```text
missing_metric
missing_scale
missing_ownership
missing_context
ambiguous_time
ambiguous_claim
weak_evidence
unsupported_skill_claim
unclear_artifact_status
```

Contradiction examples:

```text
Fact A: user claims strong project execution.
Fact B: user repeatedly reports burnout under plans.

Fact A: resume bullet says production-grade.
Fact B: evidence only supports local prototype.
```

## §13.5 Stage 5 — Self-Signal Extraction

Command:

```bash
exp2res signals generate
```

Input:

```text
experience_facts
evidence_items
gap answers
contradictions
```

Output:

```text
self_signals
```

Signal categories:

```text
skill_signal
interest_signal
direction_signal
execution_pattern
avoidance_pattern
constraint_signal
capacity_signal
contradiction_signal
```

Example signal:

```json
{
  "signal_type": "direction_signal",
  "statement": "The user repeatedly returns to local-first provenance-heavy systems.",
  "supporting_fact_ids": ["fact_storyworm_001", "fact_exp2res_004", "fact_atlas_002"],
  "confidence": "medium"
}
```

## §13.6 Stage 6 — Self-Assessment Synthesis

Command:

```bash
exp2res assess generate
```

Input:

```text
self_signals
experience_facts
contradictions
gap questions
```

Output:

```text
self_claims
assessment_snapshots
```

Assessment dimensions:

```text
technical_skill
domain_interest
working_style
execution_capacity
constraint
risk
gap
trajectory
identity_hypothesis
```

The assessment must include:

```text
supported strengths
weakly supported strengths
recurring interests
known gaps
risks / failure modes
contradictions
unknowns
counterevidence
next questions
```

## §13.7 Stage 7 — Assessment Verification

Command:

```bash
exp2res assess verify --snapshot <id>
```

Verifier checks:

1. Every self-claim has sources.
2. Confidence matches evidence strength.
3. Counterevidence is not hidden.
4. Identity claims are not over-broad.
5. Self-assessment does not become motivational fiction.
6. No clinical/diagnostic claims are generated.
7. No resume-style overclaiming leaks into mirror mode.

## §13.8 Stage 8 — Job Description Parsing

Command:

```bash
exp2res jd add jobs/agent_engineer.md
```

Extract:

```text
required skills
preferred skills
responsibilities
seniority signals
domain signals
keywords
red flags
```

## §13.9 Stage 9 — Relevance Matching

Command:

```bash
exp2res match --jd <jd_id>
```

Purpose:

```text
Select relevant facts and self-claims for a specific external context.
```

The matcher must not invent relevance.
It can rank evidence by fit.

## §13.10 Stage 10 — Resume Generation

Command:

```bash
exp2res resume generate --jd <jd_id> --branch <name>
```

Hard constraints:

1. Use only supplied facts and supported self-claims.
2. Every bullet must include source_fact_ids.
3. Every bullet must include source_log_ids.
4. Do not invent metrics.
5. Do not upgrade ownership.
6. Do not upgrade temporal precision.
7. Do not turn learning into employment.
8. Do not turn independent projects into company roles.
9. Do not use unsupported production/scale claims.
10. Prefer concrete engineering language over self-description.

## §13.11 Stage 11 — Resume Verification

Command:

```bash
exp2res verify --branch <name>
```

Verifier inspects phrases, not only whole bullets.

Example:

```text
Bullet:
"Built a production-grade LLM evaluation platform that reduced hallucinations by 40%."

Verifier findings:
"Built" -> maybe supported
"production-grade" -> unsupported
"platform" -> partially supported
"reduced hallucinations by 40%" -> unsupported
```

## §13.12 Stage 12 — Export

Command:

```bash
exp2res export assessment --snapshot <id>
exp2res export resume --branch <name>
```

Assessment outputs:

```text
out/assessment/self_assessment.md
out/assessment/self_claims.json
out/assessment/evidence_map.json
out/assessment/gap_questions.md
out/assessment/contradictions.md
```

Resume outputs:

```text
out/<branch>/resume.md
out/<branch>/evidence_map.json
out/<branch>/verification_report.md
out/<branch>/gap_questions.md
```

Export must fail if required evidence links are missing.

---

## §14. CLI Specification

## §14.1 Initialize Project

```bash
exp2res init
```

Creates:

```text
.exp2res/
  exp2res.sqlite
  config.toml
logs/
evidence/
out/
```

## §14.2 Add Daily Log

```bash
exp2res log today
exp2res log today --project Exp2Res
exp2res log today --file notes/today.md
```

## §14.3 Add Retrospective Log

```bash
exp2res log retro
```

Interactive prompts:

```text
What period are we reconstructing?
How precise is this?
How confident are you?
Project/activity?
Describe what you remember.
```

## §14.4 Add Correction

```bash
exp2res correction add --log-id log_001
```

Stores a new raw log with `entry_type = correction`.

## §14.5 Import Evidence

```bash
exp2res import tick-like path/to/export.jsonl
exp2res import atlas path/to/atlas-export.json
exp2res import github --repo owner/name
exp2res import file docs/design.md --project Exp2Res
```

## §14.6 Extract Facts

```bash
exp2res extract
exp2res extract --log-id log_001
exp2res facts list
exp2res facts show fact_001
```

## §14.7 Generate Gaps and Contradictions

```bash
exp2res gaps
exp2res gaps answer gap_001
exp2res contradictions list
exp2res contradictions show contradiction_001
```

## §14.8 Generate Self-Signals

```bash
exp2res signals generate
exp2res signals list
```

## §14.9 Generate Self-Assessment

```bash
exp2res assess generate
exp2res assess generate --scope project --project Exp2Res
exp2res assess show snapshot_001
exp2res assess verify snapshot_001
exp2res export assessment --snapshot snapshot_001
```

## §14.10 Resume Export Flow

```bash
exp2res jd add jobs/agent_engineer.md
exp2res match --jd jd_001
exp2res resume generate --jd jd_001 --branch agent-engineer
exp2res verify --branch agent-engineer
exp2res export resume --branch agent-engineer
```

---

## §15. LLM Contracts

## §15.1 General LLM Requirements

All LLM calls must:

1. Use structured outputs.
2. Be validated with Pydantic.
3. Fail closed on invalid output.
4. Store processing run metadata.
5. Never directly mutate raw logs.
6. Preserve provenance links.

If validation fails:

```text
retry once with validation errors
if retry fails, mark processing run failed
do not insert partial invalid objects
```

## §15.2 Fact Extractor Contract

Input:

```json
{
  "raw_log": {
    "id": "log_001",
    "entry_type": "manual_retro",
    "source_type": "user_memory",
    "occurred": {
      "precision": "month",
      "confidence": "medium"
    },
    "raw_text": "..."
  },
  "evidence_items": []
}
```

Output:

```json
{
  "facts": [
    {
      "claim": "Designed provenance links between generated outputs and source records.",
      "claim_kind": "observed_fact",
      "project": "StoryWorm",
      "context": "independent_project",
      "ownership_level": "designed",
      "skills": ["provenance", "LLM workflows"],
      "themes": ["grounding", "traceability"],
      "source_log_ids": ["log_001"],
      "confidence": "medium",
      "verification_status": "unverified"
    }
  ],
  "warnings": [
    {
      "type": "missing_artifact",
      "message": "The raw log describes design work but does not link to a code/design artifact."
    }
  ]
}
```

Extractor must be conservative.

## §15.3 Self-Signal Extractor Contract

Input:

```json
{
  "facts": [],
  "existing_signals": [],
  "contradictions": []
}
```

Output:

```json
{
  "signals": [
    {
      "signal_type": "direction_signal",
      "statement": "The user repeatedly returns to provenance-heavy local-first systems.",
      "supporting_fact_ids": ["fact_001", "fact_002"],
      "counter_fact_ids": [],
      "confidence": "medium"
    }
  ],
  "warnings": []
}
```

Rules:

```text
Do not turn a single fact into a broad pattern.
Do not infer identity from one artifact.
Do not hide counterevidence.
```

## §15.4 Self-Assessment Writer Contract

Input:

```json
{
  "signals": [],
  "facts": [],
  "gaps": [],
  "contradictions": []
}
```

Output:

```json
{
  "self_claims": [
    {
      "claim": "The user shows a recurring attraction to systems that preserve provenance and prevent unsupported claims.",
      "dimension": "domain_interest",
      "claim_kind": "pattern_signal",
      "source_signal_ids": ["signal_001"],
      "source_fact_ids": ["fact_001", "fact_002"],
      "confidence": "medium",
      "uncertainty": "Evidence comes mostly from personal projects and design documents, not production work."
    }
  ],
  "summary": "...",
  "unknowns": [],
  "warnings": []
}
```

Hard instructions:

```text
Do not motivate.
Do not flatter.
Do not diagnose.
Do not create a fixed identity.
Preserve uncertainty.
Mention weak evidence where relevant.
```

## §15.5 Assessment Verifier Contract

Input:

```json
{
  "self_claim": {},
  "source_signals": [],
  "source_facts": [],
  "source_logs": []
}
```

Output:

```json
{
  "status": "partially_supported",
  "unsupported_phrases": ["strong production experience"],
  "suggested_rewrite": "Evidence supports repeated design work around local-first provenance systems, but not production experience.",
  "reason": "No source facts support production deployment or production ownership."
}
```

## §15.6 Resume Writer Contract

Same as v0.1, but resume writer may additionally reference supported self_claims.

Hard rule:

```text
Self-claims can guide selection and wording, but resume bullets must still link to concrete experience facts and raw logs.
```

## §15.7 Resume Verifier Contract

The resume verifier must check:

```text
source facts
source logs
self claims
job relevance
ownership level
time precision
unsupported phrases
section placement
```

---

## §16. Verification Rules

## §16.1 Evidence Rule

Every exported self-claim or resume bullet must link to evidence.

## §16.2 Mirror Rule

Self-assessment claims must be allowed to be uncomfortable.

The system must not rewrite them into motivational language.

## §16.3 Anti-Flattery Rule

Forbidden without evidence:

```text
exceptional
world-class
highly skilled
expert
production-grade
proven leader
visionary
```

## §16.4 Ownership Rule

A claim cannot use stronger ownership language than source evidence supports.

## §16.5 Metric Rule

Numeric metrics must appear in source logs, imported artifacts, or gap answers.

## §16.6 Production Rule

Do not claim production/customer/scale/revenue/reliability unless evidence explicitly supports it.

## §16.7 Temporal Rule

Do not upgrade time precision.

## §16.8 Employment Rule

Independent projects, competitions, and learning must not be rendered as employment.

## §16.9 Identity Rule

Do not turn temporary patterns into permanent identity claims.

Allowed:

```text
Current evidence suggests...
A recurring pattern appears...
In recent projects...
```

Forbidden:

```text
You are fundamentally...
You will always...
Your true identity is...
```

## §16.10 Diagnostic Rule

The system must not generate medical, psychiatric, or clinical labels.

Allowed:

```text
The user reports burnout under ambitious plans.
```

Forbidden:

```text
The user has depression / ADHD / anxiety disorder.
```

---

## §17. Self-Assessment Report Format

Default output:

```markdown
# Self-Assessment Snapshot

Generated: YYYY-MM-DD
Scope: global / project / career / learning

## 1. Summary

## 2. Strongly Supported Facts

## 3. Recurring Signals

## 4. Current Strengths

## 5. Weakly Supported Strengths

## 6. Gaps

## 7. Contradictions

## 8. Risks / Failure Modes

## 9. Unknowns

## 10. Questions Worth Answering

## 11. Evidence Map
```

The tone should be:

```text
clear
specific
non-flattering
non-punitive
evidence-aware
```

---

## §18. Resume Export Rules

Resume export remains useful, but secondary.

Pipeline:

```text
assessment snapshot
  + job description
  + selected facts
  -> matched facts
  -> resume bullets
  -> verifier
  -> export
```

Minimum bullet contract:

```json
{
  "text": "...",
  "source_fact_ids": ["fact_001"],
  "source_log_ids": ["log_001"],
  "verification_status": "supported"
}
```

Export must fail if:

```text
bullet has no source_fact_ids
bullet has no source_log_ids
bullet status is unsupported/rejected
bullet contains unsupported ownership, metric, production, or employment framing
```

---

## §19. Integration Contracts

## §19.1 Tick-like Event Contract

```json
{
  "source": "tick-like",
  "event_id": "tick_001",
  "occurred_at": "2026-07-03T10:00:00+02:00",
  "event_type": "daily_note",
  "project": "Exp2Res",
  "text": "Worked on verifier loop design.",
  "metadata": {}
}
```

Import behavior:

```text
create raw_log(entry_type=tick_like_event)
create evidence_item(strength=imported_activity_event)
do not create strong fact without extraction/review
```

## §19.2 Atlas Artifact Contract

```json
{
  "source": "atlas",
  "artifact_id": "artifact:exp2res-verifier-design",
  "concepts": ["provenance", "verifier-loop", "grounded-generation"],
  "summary": "Design note about verifying generated claims.",
  "path": "docs/verifier.md"
}
```

Import behavior:

```text
create raw_log(entry_type=atlas_artifact_ref)
create evidence_item(strength=artifact_reference)
extract facts only if artifact content/source supports them
```

## §19.3 GitHub Commit Contract

```json
{
  "source": "github",
  "repo": "owner/repo",
  "commit_sha": "abc123",
  "message": "Add verifier loop schema",
  "files": ["exp2res/pipeline/verify_bullets.py"],
  "url": "..."
}
```

Import behavior:

```text
create raw_log(entry_type=github_commit)
create evidence_item(strength=commit_or_pr)
extract narrow implementation facts
```

---

## §20. Suggested Repository Structure

```text
exp2res/
  pyproject.toml
  README.md

  docs/
    SDD.md
    SELF_ASSESSMENT_MODEL.md
    RESUME_EXPORT_MODEL.md
    VERIFICATION_RULES.md
    INTEGRATION_CONTRACTS.md
    adr/
      0001-self-assessment-first.md
      0002-append-only-evidence.md
      0003-resume-as-export.md
      0004-no-automatic-semantic-promotion.md

  exp2res/
    __init__.py
    cli.py

    domain/
      models.py
      temporal.py
      ownership.py
      evidence.py
      self_assessment.py
      validation.py

    storage/
      sqlite.py
      migrations.py
      repositories.py

    pipeline/
      capture_raw.py
      normalize_evidence.py
      extract_facts.py
      generate_gaps.py
      detect_contradictions.py
      generate_signals.py
      generate_assessment.py
      verify_assessment.py
      parse_jd.py
      match_jd.py
      generate_resume.py
      verify_resume.py
      export.py

    integrations/
      tick_like.py
      atlas.py
      github.py
      local_files.py

    llm/
      client.py
      prompts.py
      schemas.py

    services/
      raw_log_service.py
      evidence_service.py
      fact_service.py
      assessment_service.py
      resume_service.py

    exports/
      self_assessment_markdown.py
      resume_markdown.py
      evidence_map.py
      verification_report.py
      gap_questions.py
      contradictions.py

  tests/
    test_append_only_logs.py
    test_temporal_precision.py
    test_ownership_levels.py
    test_fact_sources_required.py
    test_self_claim_sources_required.py
    test_no_flattery.py
    test_no_diagnostic_claims.py
    test_no_resume_without_evidence.py
    test_no_employment_framing.py
    test_tick_like_import_is_weak_evidence.py
    test_atlas_trail_not_skill_claim.py
    test_contradictions_preserved.py

  examples/
    logs/
      exp2res_daily.md
      storyworm_retro.md
      bitgn_competition.md

    imports/
      tick_like_export.jsonl
      atlas_artifacts.json

    jobs/
      agent_engineer.md

    outputs/
      self_assessment.md
      resume.md
      evidence_map.json
      verification_report.md
```

---

## §21. Evals

## §21.1 No Unsupported Self-Claim

Test:

```text
Given one weak raw log
When assessment writer says "strong expertise"
Then verifier rejects or rewrites the claim
```

## §21.2 No Automatic Skill From Tick-like

Test:

```text
Given Tick-like event "worked on verifier"
When facts are extracted
Then system may create weak activity fact
But must not create "verifier loop expert"
```

## §21.3 Atlas Trail Does Not Equal Mastery

Test:

```text
Given Atlas trail touches Kafka
When Exp2Res imports it
Then it may create context evidence
But must not claim Kafka mastery
```

## §21.4 No Hidden Contradiction

Test:

```text
Given evidence supports both high ambition and burnout under plans
When assessment is generated
Then contradiction/risk is preserved
```

## §21.5 No Invented Metrics

Test:

```text
Given no metric in evidence
When resume writer creates "reduced latency by 40%"
Then verifier rejects it
```

## §21.6 No Ownership Upgrade

Test:

```text
Given source says participated
When output says led/designed/owned
Then verifier rejects it
```

## §21.7 Temporal Precision Preservation

Test:

```text
Given source precision = month
When output contains exact day
Then verifier rejects it
```

## §21.8 No Diagnostic Labels

Test:

```text
Given user reports burnout
When assessment is generated
Then system may mention reported burnout
But must not assign clinical diagnoses
```

## §21.9 Resume Requires Evidence

Test:

```text
Given bullet has no source_fact_ids or source_log_ids
Then export fails
```

## §21.10 Assessment Requires Evidence

Test:

```text
Given self_claim has no source facts/signals
Then assessment verification fails
```

---

## §22. Implementation Plan

## Phase 0 — Skeleton

Build:

```text
Typer CLI
SQLite connection
Pydantic models
raw_logs table
evidence_items table
basic config
```

Commands:

```bash
exp2res init
exp2res log today
exp2res log retro
exp2res logs list
```

Definition of done:

```text
Can create local database.
Can add daily and retrospective logs.
Can inspect raw logs.
```

## Phase 1 — Evidence and Fact Extraction

Build:

```text
evidence normalization
fact extractor schema
experience_facts table
fact_sources table
```

Commands:

```bash
exp2res extract
exp2res facts list
exp2res facts show <id>
```

Definition of done:

```text
Raw logs become atomic facts with source_log_ids.
No fact can exist without source.
```

## Phase 2 — Gaps and Contradictions

Build:

```text
gap question generator
contradiction detector
gap answer flow
correction flow
```

Commands:

```bash
exp2res gaps
exp2res gaps answer <id>
exp2res contradictions list
exp2res correction add
```

Definition of done:

```text
Weak facts generate useful questions.
Contradictions are stored, not hidden.
Corrections append new records.
```

## Phase 3 — Self-Signals and Assessment

Build:

```text
self_signals table
self_claims table
assessment_snapshots table
assessment writer
assessment verifier
Markdown assessment export
```

Commands:

```bash
exp2res signals generate
exp2res assess generate
exp2res assess verify <snapshot_id>
exp2res export assessment --snapshot <id>
```

Definition of done:

```text
System produces an evidence-backed self-assessment with strengths, gaps, contradictions, unknowns, and evidence map.
```

## Phase 4 — Resume Export

Build:

```text
job description parser
JD matcher
resume branch table
resume writer
resume verifier
resume export
```

Commands:

```bash
exp2res jd add <file>
exp2res match --jd <id>
exp2res resume generate --jd <id> --branch <name>
exp2res verify --branch <name>
exp2res export resume --branch <name>
```

Definition of done:

```text
System generates a job-targeted Markdown resume with evidence map and verification report.
Unsupported bullets are blocked.
```

## Phase 5 — Integrations

Build:

```text
Tick-like JSONL import
Atlas artifact/trail import
GitHub commit/PR import
local file import
```

Definition of done:

```text
External evidence can enter as raw logs/evidence items without automatic overclaiming.
```

---

## §23. End-to-End Demo

## §23.1 Input

```markdown
# Exp2Res retrospective

Period: June-July 2026
Precision: month
Confidence: medium
Context: independent_project

I redesigned Exp2Res from a resume-first tool into a self-assessment-first system.
The core idea became: honest model of self from immutable evidence, with resume as a secondary export.
I emphasized truth over comfort, provenance, verifier gates, and no automatic semantic promotion from activity to skill.
```

## §23.2 Extracted Facts

```json
[
  {
    "id": "fact_001",
    "claim": "Redesigned Exp2Res from a resume-first tool into a self-assessment-first system.",
    "context": "independent_project",
    "ownership_level": "designed",
    "skills": ["system design", "product architecture"],
    "themes": ["self-assessment", "provenance", "grounded generation"],
    "source_log_ids": ["log_001"],
    "confidence": "medium"
  },
  {
    "id": "fact_002",
    "claim": "Defined resume generation as a secondary export grounded in the internal evidence model.",
    "context": "independent_project",
    "ownership_level": "designed",
    "skills": ["system design", "verification"],
    "themes": ["resume export", "evidence mapping"],
    "source_log_ids": ["log_001"],
    "confidence": "medium"
  }
]
```

## §23.3 Self-Signals

```json
[
  {
    "signal_type": "direction_signal",
    "statement": "The user is drawn to systems that preserve truth through provenance and verification.",
    "supporting_fact_ids": ["fact_001", "fact_002"],
    "confidence": "medium"
  }
]
```

## §23.4 Self-Assessment Claim

```json
{
  "claim": "Current evidence suggests a recurring interest in local-first systems that make hidden experience, knowledge, or claims inspectable and verifiable.",
  "dimension": "domain_interest",
  "claim_kind": "pattern_signal",
  "source_fact_ids": ["fact_001", "fact_002"],
  "confidence": "medium",
  "uncertainty": "Evidence is strongest in design documents and project framing; implementation depth must be assessed separately."
}
```

## §23.5 Resume Bullet Candidate

```text
Designed Exp2Res, a local-first self-assessment system that converts immutable experience evidence into verified self-claims and job-targeted resume exports.
```

Verifier result:

```json
{
  "status": "supported",
  "unsupported_phrases": [],
  "reason": "The bullet is supported by the design facts and does not claim production use, metrics, employment, or unsupported scale."
}
```

---

## §24. Acceptance Criteria

V1 is acceptable when:

1. User can add daily and retrospective raw logs.
2. User can import at least one external evidence source.
3. Raw logs are append-only.
4. Corrections are stored as new events.
5. Experience facts require source logs.
6. Self-claims require source facts/signals.
7. Assessment snapshots preserve uncertainty and contradictions.
8. Assessment verifier blocks flattery, unsupported identity claims, and diagnostic claims.
9. Resume bullets require source facts and source logs.
10. Resume verifier blocks unsupported ownership, metrics, production claims, and employment framing.
11. Markdown self-assessment export works.
12. Markdown resume export works.
13. Evidence maps are generated for assessment and resume outputs.
14. Tests cover no automatic semantic promotion across Tick-like, Atlas, and Exp2Res.

---

## §25. Risks and Mitigations

## §25.1 Risk: Exp2Res Becomes a Resume Tool Again

Mitigation:

```text
assessment pipeline comes before resume pipeline
README states resume is secondary export
resume branch references assessment snapshot
self-assessment tests are required before resume tests
```

## §25.2 Risk: The System Becomes Flattering Fiction

Mitigation:

```text
anti-flattery verifier
counterevidence fields
confidence levels
unknowns section
contradictions table
```

## §25.3 Risk: The System Becomes Punitive

Mitigation:

```text
non-punitive report language
no moral scoring
no productivity grades
no global worth claims
```

## §25.4 Risk: Agents Overclaim

Mitigation:

```text
structured outputs
Pydantic validation
verifier loop
source requirements
unsupported phrase detection
```

## §25.5 Risk: External Integrations Pollute Truth Model

Mitigation:

```text
imported data enters only as raw evidence
no automatic semantic promotion
per-source evidence strength
review gates for high-impact claims
```

## §25.6 Risk: Self-Assessment Becomes Diagnosis

Mitigation:

```text
ban diagnostic labels
report observed patterns only
include non-clinical language tests
```

---

## §26. README Positioning

Recommended README intro:

```markdown
# Exp2Res — Experience to Self-Assessment to Resume

Exp2Res is a local-first, provenance-heavy self-assessment system.

It turns immutable experience evidence into an honest model of skills, patterns, gaps, contradictions, and uncertainty. Resume generation is a secondary export: job-targeted bullets are generated only from supported evidence and verified before export.
```

Recommended tagline:

```text
A mirror first. A resume exporter second.
```

Portfolio tagline:

```text
Evidence-backed self-assessment and verifier-gated resume generation from immutable experience logs.
```

---

## §27. Key Invariants

```text
Raw logs are append-only.
Corrections are new evidence, not silent edits.
Every fact has source logs.
Every self-claim has source facts or signals.
Every resume bullet has source facts and source logs.
Uncertainty is preserved.
Contradictions are preserved.
Ownership is not inflated.
Temporal precision is not inflated.
Metrics are not invented.
Production claims are not invented.
Self-assessment does not diagnose.
Resume is an export, not the master model.
```

---

## §28. Final Design Statement

Exp2Res should preserve three layers that must never collapse into one:

```text
Experience Evidence:
  What actually happened or was recorded.

Self-Assessment:
  What that evidence honestly suggests about the user, including uncertainty and contradiction.

Resume Export:
  A job-relevant external projection that must remain grounded in the evidence model.
```

The system succeeds when the user can look at it and say:

```text
I see what I actually did.
I see what it suggests.
I see what is strong, weak, unknown, and contradictory.
I can export myself honestly without inventing a story.
I am not trapped in a sweet lie.
```

Core sentence:

> **Exp2Res is a local-first mirror of real experience: honest before comforting, evidence before narrative, assessment before export.**

---

## Decision Log

Format: `YYYY-MM-DD — decision in one phrase; rejected alternative and why.`

- 2026-07-03 — Keep the SDD as a single file navigated via the § Index (map-as-interface);
  a physical split into a map file plus per-section files is deferred until after the
  structural dedup pass, and if done, must be a purely mechanical commit (concatenated
  section files must reproduce the original). Revisit triggers: code lands and sections
  graduate into living docs/tests; full-pass reviews start hitting context limits; parallel
  per-section editing becomes the norm; the file exceeds ~30–40K tokens despite dedup.
  Rejected alternative: splitting now — point reads already load only the needed section
  (§ Index + grep), a split does not fix the actual pain (cross-section duplication and
  drift), and splitting before dedup would migrate content about to be merged or deleted.
