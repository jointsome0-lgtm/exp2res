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

