## §10. Enumerations

§10 is the canonical home for every named `Literal` value list used by persisted §11 models and pipeline sections. Each enum-like list must have a stable alias name and be written as a plain Python `Literal[...]` assignment so a future post-MVP docs/schema/lint generator can mechanically extract it.

Other sections must reference the alias name and the field that carries it; they must not restate the value list as a second source of truth. Prose mirrors must not become second normative homes. A change to a §10 value list requires updating all direct references and examples in the same commit, without creating another normative list.

No generator, linter, generated documentation, separate machine-readable registry, or runtime schema tooling is required for MVP. If introduced after MVP, it must derive from §10 rather than creating a second enum source.

`TemporalConfidence` types only confidence in an `OccurredAt` placement; `Confidence` types general confidence in derived facts, signals, and claims. The aliases intentionally remain separate even while their member sets are identical. For temporal-provenance comparison, `TemporalConfidence` has the normative weak-to-strong order `unknown < low < medium < high`; for calibration comparison under §9.4, `Confidence` has the same normative weak-to-strong order `unknown < low < medium < high`. These two explicit orders are normative for their aliases; the assignment order below is not itself a ranking.

`OwnershipLevel` is a normative total order. Members in its assignment are listed from weakest to strongest; `unknown` is the weakest value.

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
Confidence = Literal["low", "medium", "high", "unknown"]

EntryType = Literal[
    "manual_daily",
    "manual_retro",
    "gap_answer",
    "correction",
    "tick_like_event",
    "atlas_artifact_ref",
    "github_commit",
    "design_doc",
]

SourceType = Literal[
    "manual_entry",
    "user_memory",
    "imported_artifact",
    "imported_event",
]

EvidenceStrength = Literal[
    "manual_claim",
    "imported_activity_event",
    "artifact_reference",
    "commit_or_pr",
    "design_doc",
]

OwnershipLevel = Literal[
    "unknown",
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

SignalType = Literal[
    "skill_signal",
    "interest_signal",
    "direction_signal",
    "execution_pattern",
    "avoidance_pattern",
    "constraint_signal",
    "capacity_signal",
    "contradiction_signal",
]

SelfClaimDimension = Literal[
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

AssessmentScope = Literal[
    "global",
    "project",
    "career",
    "learning",
]

ResumeTargetSection = Literal[
    "summary",
    "professional_experience",
    "selected_projects",
    "competitions",
    "skills",
    "education",
]

TargetRoleRelevance = Literal[
    "low",
    "medium",
    "high",
]

GapPriority = Literal[
    "low",
    "medium",
    "high",
]

DetectionRefType = Literal[
    "raw_log",
    "evidence_item",
    "experience_fact",
]

GapTrigger = Literal[
    "missing_metric",
    "missing_scale",
    "missing_ownership",
    "missing_context",
    "ambiguous_time",
    "ambiguous_claim",
    "weak_evidence",
    "unsupported_skill_claim",
    "unclear_artifact_status",
]

JDRequirementKind = Literal[
    "required_skill",
    "preferred_skill",
    "responsibility",
]
```

`VerificationStatus` members are canonical here; their operational meanings, aggregation, and consumer allowlists live only in §16.11.

---
