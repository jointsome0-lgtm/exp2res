## §23. End-to-End Demo

## §23.1 Input

```markdown
# Exp2Res retrospective

Period: June-July 2026
Precision: approximate range
Confidence: medium
Context: independent_project

I redesigned Exp2Res from a resume-first tool into a self-assessment-first system.
The core idea became: honest model of self from owner-controlled evidence protected from system rewrites, with resume as a secondary export.
I emphasized truth over comfort, provenance, verifier gates, and no automatic semantic promotion from activity to skill.
```

The retrospective capture operation persists this text as a `RawLog` with `occurred = {start: 2026-06-01T00:00:00+02:00, end: 2026-08-01T00:00:00+02:00, precision: approximate_range, confidence: medium}` — bounds are offset-aware `datetime` values at every precision (§11.1), and `precision`, not the representational midnight time-of-day, carries the temporal meaning — and creates its linked `EvidenceItem(strength=manual_claim)` before fact extraction starts (§13.1). The demo contains no narrower temporal evidence, so both facts inherit that placement unchanged.

## §23.2 Extracted Facts

```json
[
  {
    "id": "fact_001",
    "created_at": "2026-07-11T10:00:00+02:00",
    "superseded_at": null,
    "claim": "Redesigned Exp2Res from a resume-first tool into a self-assessment-first system.",
    "claim_kind": "observed_fact",
    "project": "Exp2Res",
    "role": null,
    "company": null,
    "context": "independent_project",
    "ownership_level": "designed",
    "action": "redesigned",
    "object": "Exp2Res product framing",
    "outcome": "Self-assessment became the primary model and resume generation a secondary export.",
    "skills": ["system design", "product architecture"],
    "technologies": [],
    "themes": ["self-assessment", "provenance", "grounded generation"],
    "occurred": {"start": "2026-06-01T00:00:00+02:00", "end": "2026-08-01T00:00:00+02:00", "precision": "approximate_range", "confidence": "medium"},
    "source_log_ids": ["log_001"],
    "evidence_item_ids": ["evidence_001"],
    "confidence": "medium",
    "metadata": {}
  },
  {
    "id": "fact_002",
    "created_at": "2026-07-11T10:00:00+02:00",
    "superseded_at": null,
    "claim": "Defined resume generation as a secondary export grounded in the internal evidence model.",
    "claim_kind": "observed_fact",
    "project": "Exp2Res",
    "role": null,
    "company": null,
    "context": "independent_project",
    "ownership_level": "designed",
    "action": "defined",
    "object": "resume generation boundary",
    "outcome": "Resume output depends on the internal evidence and assessment layers.",
    "skills": ["system design", "verification"],
    "technologies": [],
    "themes": ["resume export", "evidence mapping"],
    "occurred": {"start": "2026-06-01T00:00:00+02:00", "end": "2026-08-01T00:00:00+02:00", "precision": "approximate_range", "confidence": "medium"},
    "source_log_ids": ["log_001"],
    "evidence_item_ids": ["evidence_001"],
    "confidence": "medium",
    "metadata": {}
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
  "claim": "Current evidence suggests an interest in owner-controlled evidence, provenance, and verifier gates within the Exp2Res redesign.",
  "dimension": "domain_interest",
  "claim_kind": "hypothesis",
  "source_fact_ids": ["fact_001", "fact_002"],
  "confidence": "medium",
  "uncertainty": "Support is one owner retrospective about Exp2Res project framing; implementation depth and outcome remain unestablished."
}
```

## §23.5 Resume Bullet Candidate

```text
Designed Exp2Res, a local-first self-assessment system that converts owner-controlled experience evidence into verified self-claims and job-targeted resume exports.
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
