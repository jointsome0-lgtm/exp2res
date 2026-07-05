## §10. Enumerations

§10 is the canonical home for every named `Literal` value list used by persisted §11 models and pipeline sections. Each enum-like list must have a stable alias name and be written as a plain Python `Literal[...]` assignment so a future post-MVP docs/schema/lint generator can mechanically extract it.

Other sections must reference the alias name and the field that carries it; they must not restate the value list as a second source of truth. Prose mirrors must not become second normative homes. A change to a §10 value list requires updating all direct references and examples in the same commit, without creating another normative list.

No generator, linter, generated documentation, separate machine-readable registry, or runtime schema tooling is required for MVP. If introduced after MVP, it must derive from §10 rather than creating a second enum source.

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

OccurredAtKind = Literal[
    "exact_datetime",
    "exact_day",
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
    "custom",
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

ContradictionStatus = Literal[
    "open",
    "resolved",
    "dismissed",
]

GapPriority = Literal[
    "low",
    "medium",
    "high",
]

EntityRefType = Literal[
    "raw_log",
    "evidence_item",
    "experience_fact",
    "self_signal",
    "self_claim",
    "assessment_snapshot",
    "resume_bullet",
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
```

---
