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

AssessmentScope = Literal["global", "project"]

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

CounterevidenceRefType = Literal[
    "raw_log",
    "evidence_item",
    "experience_fact",
    "self_signal",
]

VerificationTargetRefType = Literal[
    "self_claim",
    "resume_bullet",
]

EntryType = Literal[
    "manual_daily",
    "manual_retro",
    "gap_answer",
    "correction",
    "ephemeris_event",
    "atlas_snapshot",
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
    "knowledge_state_snapshot",
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

GapPriority = Literal["low", "medium", "high"]

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

CLIResultStatus = Literal["ok", "blocked", "failed", "cancelled"]
