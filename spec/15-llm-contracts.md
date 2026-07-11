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
      "start": "2026-06-01T00:00:00+02:00",
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

Hard instructions: apply §16.2 (mirror, no motivational rewriting), §16.3 (anti-flattery), §16.9 (identity), §16.10 (diagnostic); preserve uncertainty and mention weak evidence where relevant.

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
