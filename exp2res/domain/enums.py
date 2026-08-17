from datetime import timedelta
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

# §16.7 rule 5's maximum-uncertainty width per non-range precision; ranges use
# their own `end - start` and `unknown` is unbounded, so neither appears. It
# sits beside the alias it is keyed by, like CONFIDENCE_RANK, because §11's
# boundary and §16.7's arithmetic both read it.
MAX_UNCERTAINTY_WIDTH: dict[str, timedelta] = {
    "exact_datetime": timedelta(0),
    "exact_day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=31),
    "quarter": timedelta(days=92),
    "year": timedelta(days=366),
}

TemporalConfidence = Literal["low", "medium", "high", "unknown"]
Confidence = Literal["low", "medium", "high", "unknown"]

# §10's normative weak-to-strong order for both confidence aliases, which the
# assignment order above does not carry. The aliases stay separate by §10's own
# rule; the rank they share lives here once rather than once per comparison.
CONFIDENCE_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}

AssessmentScope = Literal["global"]

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

# §10 declaration order is normative: §18 renders one bullet-pack section per
# member in exactly this order, and §13.10 orders bullets by it.
ResumeTargetSection = Literal[
    "summary",
    "professional_experience",
    "selected_projects",
    "competitions",
    "skills",
    "education",
]

TargetRoleRelevance = Literal["low", "medium", "high"]

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

OwnerAttribution = Literal["owner", "not_owner", "unknown"]

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

JDRequirementKind = Literal[
    "required_skill",
    "preferred_skill",
    "responsibility",
]

CLIResultStatus = Literal["ok", "blocked", "failed", "cancelled"]

ManagedOutputKind = Literal["assessment", "resume"]
