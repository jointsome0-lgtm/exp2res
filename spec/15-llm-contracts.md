## §15. LLM Contracts

## §15.1 General LLM Requirements

All LLM calls must:

1. Use structured outputs.
2. Be validated with Pydantic.
3. Fail closed on invalid output.
4. Store processing run metadata.
5. Never create, mutate, or delete raw logs; automation's raw-layer authority is append-only and capture/import services own those appends (§5.3).
6. Preserve provenance links.

If validation fails:

```text
retry once with validation errors
if retry fails, mark processing run failed
do not insert partial invalid objects
```

This retry handles only invalid structured output, including reference-validation errors. A schema-valid negative semantic verdict is successful verifier output: it does not trigger another verifier call, invoke a writer, or begin an automatic repair loop.

Structured-output validation includes §12 rule 10. If any typed reference is missing, wrong-type, superseded, or duplicated, no candidate business output is committed; §12.13 defines the failed `processing_runs` result and diagnostic metadata.

## §15.2 Fact Extractor Contract

Input:

```json
{
  "raw_logs": [
    {
      "id": "log_001",
      "entry_type": "manual_retro",
      "source_type": "user_memory",
      "occurred": {
        "start": "2026-06-01T00:00:00+02:00",
        "precision": "month",
        "confidence": "medium"
      },
      "raw_text": "...",
      "metadata": {}
    }
  ],
  "evidence_items": [
    {
      "id": "evidence_001",
      "created_at": "2026-07-11T10:00:00+02:00",
      "raw_log_id": "log_001",
      "summary": "Manual retrospective about StoryWorm design work.",
      "strength": "manual_claim"
    }
  ]
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
      "occurred": {
        "start": "2026-06-01T00:00:00+02:00",
        "precision": "month",
        "confidence": "medium"
      },
      "source_log_ids": ["log_001"],
      "evidence_item_ids": ["evidence_001"],
      "confidence": "medium"
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

`raw_logs` is one ordered correction lineage under §13.3. The root appears first and corrections follow by `recorded_at` then ID; the extractor produces one complete replacement fact set for the lineage rather than extracting mutually inconsistent generations independently.

Each raw log passes its `metadata` through this contract unmodified. For `gap_answer` logs it carries the §14.7 question context (`question_text`, `question_reason`); the extractor must interpret the answer text against that question — a contextual answer such as a bare quantity is meaningless without it — while still attributing extracted facts to the answer log itself.

Each fact output selects its supporting evidence explicitly through `evidence_item_ids`. Persistence verifies that those items exist and that `source_log_ids` is exactly their distinct raw-log set before writing one `direct` §12.4 row per item; all linked strengths participate in confidence calibration.

Every fact output also carries `occurred`. For corrected facts it uses the latest selected correction's effective `OccurredAt` from §14.4; for uncorrected facts it preserves the root source placement. It must satisfy §11.1 and must not increase source precision under §16.7.

For `ExperienceFact.claim_kind`, `observed_fact` means the linked sources directly state or demonstrate the narrow claim; `inferred_fact` means the claim is a conservative derivation whose source links and calibrated confidence remain explicit. Other `ClaimKind` values are invalid fact-extractor outputs.

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
    },
    {
      "claim": "Current evidence suggests a recurring interest in provenance-heavy systems, while implementation depth remains uncertain.",
      "dimension": "domain_interest",
      "claim_kind": "narrative_summary",
      "source_signal_ids": ["signal_001"],
      "source_fact_ids": ["fact_001", "fact_002"],
      "confidence": "medium",
      "uncertainty": "The evidence supports direction of interest more strongly than implementation depth."
    }
  ],
  "summary": "Current evidence suggests a recurring interest in provenance-heavy systems, while implementation depth remains uncertain.",
  "unknowns": [],
  "warnings": []
}
```

For `SelfClaim.claim_kind`, `pattern_signal` summarizes a recurring supported pattern, `hypothesis` marks a tentative interpretation, and `narrative_summary` synthesizes already supported claims without adding a new fact. Other `ClaimKind` values are invalid self-assessment-writer outputs.

The writer emits exactly one `narrative_summary` self-claim whose `claim` equals the top-level `summary`. Stage 6 assigns its ID and includes it in the snapshot's `self_claim_ids`; there is no separate unverified summary channel.

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
  "counterevidence": [
    "fact_007: the only deployment fact describes a local demo, not a production environment"
  ],
  "suggested_rewrite": "Evidence supports repeated design work around local-first provenance systems, but not production experience.",
  "reason": "No source facts support production deployment or production ownership."
}
```

`counterevidence` lists contrary-evidence statements grounded in the supplied sources (empty when none); Stage 7 persists it to `SelfClaim.counterevidence` (§11.6, §13.7).

Every `status` uses the canonical meaning in §16.11. Stage 7 validates one finding for every claim in the snapshot and derives the snapshot's own status from those claim results; the writer or verifier may not assign a more permissive snapshot label independently.

`suggested_rewrite` is owner-facing advisory output from any CLI command that invokes Stage 7 (§14.9, §14.12). It is not persisted, is not an input to §15.4, and is never applied by Stage 7. If the owner requests revised wording, the assessment writer must emit a new claim in a later Stage 6 replacement generation.

## §15.6 Resume Writer Contract

Same as v0.1, but the resume writer may additionally reference self-claims whose status is `supported` under §16.11.

Hard rule:

```text
Self-claims can guide selection and wording, but resume bullets must still link to concrete experience facts and raw logs.
```

Stage 10 passes the writer only the `supported` self-claims selected for that bullet; it does not pass `AssessmentSnapshot.title` or `.summary` as independent prose inputs. The bullet's `source_self_claim_ids` is the duplicate-free exact ID set of that writer input and is empty iff the writer received no self-claim. The writer may neither use an unlisted self-claim nor list one it did not receive.

## §15.7 Resume Verifier Contract

Input:

```json
{
  "resume_bullet": {},
  "source_facts": [],
  "source_logs": [],
  "source_self_claims": [],
  "job_description": {}
}
```

Output:

```json
{
  "status": "unsupported",
  "unsupported_phrases": ["production-grade"],
  "suggested_rewrite": "Built an LLM evaluation platform grounded in local project evidence.",
  "reason": "No supplied fact supports production use."
}
```

`status` uses §16.11. Stage 11 validates one finding for every current bullet and persists `status`, `unsupported_phrases`, and `reason` to `ResumeBullet.verification_status`, `unsupported_phrases`, and `verifier_reason` (§11.8, §13.11). `suggested_rewrite` is owner-facing advisory output: it is presented by §14.10 but is neither persisted nor applied.

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
