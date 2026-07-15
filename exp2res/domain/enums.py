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

CLIResultStatus = Literal["ok", "blocked", "failed", "cancelled"]
