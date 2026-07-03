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

