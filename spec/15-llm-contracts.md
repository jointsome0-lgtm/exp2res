## §15. LLM Contracts

## §15.1 General LLM Requirements

All LLM calls must:

1. Use structured outputs.
2. Be validated with Pydantic.
3. Fail closed on invalid output.
4. Store processing run metadata.
5. Never create, mutate, or delete raw logs; automation's raw-layer authority is append-only and capture/import services own those appends (§5.3).
6. Preserve provenance links.
7. Preserve the generated-voice/source-voice boundary in §16.12: structured source text may be evidence input, but voice rules evaluate only Exp2Res-authored candidate language and never rewrite or reject source material.
8. Forbid undeclared output fields; an extra key is invalid structured output rather than ignored data.

If validation fails:

```text
retry once with validation errors
if retry fails, mark processing run failed
do not insert partial invalid objects
```

This retry handles only invalid structured output, including reference-validation errors. A schema-valid negative semantic verdict is successful verifier output: it does not trigger another verifier call, invoke a writer, or begin an automatic repair loop.

Structured-output validation includes §12 rule 10. If any typed reference is missing, wrong-type, superseded, or duplicated, no candidate business output is committed; §12.13 defines the failed `processing_runs` result and diagnostic metadata.

Every contract `warnings` field is `list[ContractWarning]`, where each item has exactly two non-empty string fields and no extras:

```json
{
  "type": "stable_machine_code",
  "message": "Owner-facing explanation grounded in this call's input."
}
```

The one retry above applies only to an invalid model response. Failure in deterministic service enrichment after a valid response — such as allocating a collision-free service-owned ID — must be retried locally when safe or fail the processing run atomically; it must not invoke the LLM again.

Example notation: an entity's model-emitted shape appears once, at its producing contract — §15.2 (fact), §15.3 (signal), §15.4 (claim), §15.8 (gap, contradiction), §15.9 (`ParsedJD`) — and the complete persisted §11 shape is that shape plus exactly the service-set fields the producer's prose names (§15.2: Stage 3's `id`/`created_at`/`superseded_at`; §15.3 and §15.8: the stage-supplied ID, lifecycle, and answer-state fields; §15.9: Stage 8's `JobDescription.id`/`created_at` and `JDRequirement.id`). Persisted-row examples appear where a contract consumes them: §15.2's input (`RawLog`, `EvidenceItem`), §15.4's input (`SelfSignal`, `GapQuestion`), §15.6's input (a verified `SelfClaim`). Other examples elide a repeated body to a `"<id: complete §NN.N Model — canonical example in §NN.N>"` string pointing at the named example. A behavior-bearing object — one whose concrete content the same example's output depends on — is never elided: §15.8 shows its fact and raw log in full. §11 remains the normative field source (§12 rule 1); placeholders are example notation only, and the service always passes the complete typed objects the surrounding prose requires.

## §15.2 Fact Extractor Contract

Input:

```json
{
  "raw_logs": [
    {
      "id": "log_001",
      "recorded_at": "2026-07-11T09:55:00+02:00",
      "entry_type": "manual_retro",
      "source_type": "user_memory",
      "occurred": {
        "start": "2026-06-01T00:00:00+02:00",
        "end": null,
        "precision": "month",
        "confidence": "medium"
      },
      "raw_text": "...",
      "project": "StoryWorm",
      "external_ref": null,
      "corrects_log_id": null,
      "metadata": {}
    }
  ],
  "evidence_items": [
    {
      "id": "evidence_001",
      "created_at": "2026-07-11T10:00:00+02:00",
      "raw_log_id": "log_001",
      "title": null,
      "summary": "Manual retrospective about StoryWorm design work.",
      "uri": null,
      "path": null,
      "strength": "manual_claim",
      "metadata": {}
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
      "role": null,
      "company": null,
      "context": "independent_project",
      "ownership_level": "designed",
      "action": "designed",
      "object": "provenance links between generated outputs and source records",
      "outcome": null,
      "skills": ["provenance", "LLM workflows"],
      "technologies": [],
      "themes": ["grounding", "traceability"],
      "occurred": {
        "start": "2026-06-01T00:00:00+02:00",
        "end": null,
        "precision": "month",
        "confidence": "medium"
      },
      "source_log_ids": ["log_001"],
      "evidence_item_ids": ["evidence_001"],
      "confidence": "medium",
      "metadata": {}
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

Every fact output carries every writer-settable §11.4 field shown above; Stage 3 supplies only `id`, `created_at`, and `superseded_at`. Optional/default fields are explicit in the contract so a model change cannot silently fall outside the structured boundary.

Every fact output also carries `occurred`. For corrected facts the governing source placement is the latest selected correction's effective `OccurredAt` from §14.4; for uncorrected facts it is the root log's placement. The extractor copies that `OccurredAt` by default. It may emit a contained narrower placement only when the selected raw/evidence context explicitly states the narrower time; this is the additional linked support required by §16.7, not a model inference. It may never widen beyond the governing source window, set `occurred.precision` / persisted `temporal_precision` stronger than the strongest explicit in-context temporal support, or set `occurred.confidence` / persisted `temporal_confidence` above the governing source confidence under §10's order. When support conflicts or containment cannot be established, preserve the governing placement and lower temporal confidence if necessary rather than change its window or choose a stronger one.

For `ExperienceFact.claim_kind`, `observed_fact` means the linked sources directly state or demonstrate the narrow claim; `inferred_fact` means the claim is a conservative derivation whose source links and calibrated confidence remain explicit. Other `ClaimKind` values are invalid fact-extractor outputs.

## §15.3 Self-Signal Extractor Contract

Input:

```json
{
  "facts": [],
  "evidence_items": [],
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

`evidence_items` is exactly the duplicate-free set reached through the supplied current facts and is context for evidence-strength calibration; signal provenance remains the fact IDs in §11.5. Prior signals are never inputs because Stage 5 produces a complete replacement generation. Raw gap answers are not inputs either: §13.5 requires them to pass through Stage 3 first, so only re-extracted current facts and their linked evidence can influence this contract.

## §15.4 Self-Assessment Writer Contract

Input:

```json
{
  "scope": "project",
  "scope_target": "Exp2Res",
  "signals": [
    {
      "id": "signal_001",
      "created_at": "2026-07-11T10:02:00+02:00",
      "superseded_at": null,
      "signal_type": "direction_signal",
      "statement": "The user repeatedly returns to provenance-heavy local-first systems.",
      "supporting_fact_ids": ["fact_001", "fact_002"],
      "counter_fact_ids": [],
      "confidence": "medium",
      "metadata": {}
    }
  ],
  "facts": [
    "<fact_001, fact_002: complete §11.4 ExperienceFact objects — canonical example in §15.2>"
  ],
  "gaps": [
    {
      "id": "gap_001",
      "created_at": "2026-07-11T10:03:00+02:00",
      "superseded_at": null,
      "target_type": "experience_fact",
      "target_id": "fact_001",
      "question": "What implementation evidence demonstrates the current depth?",
      "reason": "weak_evidence",
      "priority": "medium",
      "answered": false,
      "answer_log_id": null
    }
  ],
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
  "unknowns": [
    {
      "gap_question_id": "gap_001"
    }
  ],
  "warnings": []
}
```

For `SelfClaim.claim_kind`, `pattern_signal` summarizes a recurring supported pattern, `hypothesis` marks a tentative interpretation, and `narrative_summary` synthesizes already supported claims without adding a new fact. Other `ClaimKind` values are invalid self-assessment-writer outputs.

The writer emits exactly one `narrative_summary` self-claim whose `claim` equals the top-level `summary`. Stage 6 assigns its ID and includes it in the snapshot's `self_claim_ids`; there is no separate unverified summary channel.

`scope` is a canonical `AssessmentScope` and `scope_target` is service-supplied structural context from §14.9. The writer must return neither field and cannot rewrite the target. `gaps` is the complete current unanswered set; answered current rows are not passed. Each `unknowns` entry has exactly one `gap_question_id` and no prose field. The IDs must be the duplicate-free exact set of all supplied `gaps`; an empty `unknowns` array is valid only when that input is empty. Stage 6 stores the set unchanged as `AssessmentSnapshot.gap_question_ids`. Known-gap assertions belong in status-bearing `SelfClaim(dimension="gap")` output. An unknown reference can render only the referenced question/uncertainty under §17; it is not an independent claim or a §16.11 bypass.

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

Input:

```json
{
  "branch": {
    "name": "agent-engineer",
    "job_description_id": "jd_001",
    "assessment_snapshot_id": "snapshot_001",
    "assessment_scope": "project",
    "assessment_scope_target": "Exp2Res"
  },
  "job_description": {
    "id": "jd_001",
    "title": "Agent Engineer",
    "company": "Example Co",
    "parsed": "<complete §11.13 ParsedJD containing requirement jdreq_001 — canonical example in §15.9>"
  },
  "selected_facts": [
    {
      "fact": "<fact_001: complete §11.4 ExperienceFact — canonical example in §15.2>",
      "evidence": [
        {
          "evidence_item": "<evidence_001: complete §11.3 EvidenceItem — canonical example in §15.2>",
          "raw_log": "<log_001: complete §11.2 RawLog — canonical example in §15.2>"
        }
      ]
    }
  ],
  "supported_self_claims": [
    {
      "id": "claim_001",
      "created_at": "2026-07-11T10:05:00+02:00",
      "superseded_at": null,
      "claim": "Current evidence supports recurring work on provenance-heavy systems.",
      "claim_kind": "pattern_signal",
      "dimension": "domain_interest",
      "source_signal_ids": ["signal_001"],
      "source_fact_ids": ["fact_001"],
      "confidence": "medium",
      "verification_status": "supported",
      "counterevidence": [],
      "uncertainty": null,
      "metadata": {}
    }
  ]
}
```

Output:

```json
{
  "bullet": {
    "text": "Designed provenance links for an evidence-grounded LLM workflow.",
    "target_section": "selected_projects",
    "target_role_relevance": "high",
    "matched_jd_requirements": ["jdreq_001"],
    "source_fact_ids": ["fact_001"],
    "source_log_ids": ["log_001"],
    "source_self_claim_ids": ["claim_001"]
  },
  "warnings": []
}
```

Hard rule:

```text
Self-claims can guide selection and wording, but resume bullets must still link to concrete experience facts and raw logs.
```

Stage 10 invokes this contract in an isolated model context once per planned bullet and passes only the `supported` self-claims selected for that bullet. No invocation can see another bullet's facts or claims. It does not pass `AssessmentSnapshot.title` or `.summary` as independent prose inputs. The bullet's `source_self_claim_ids` is the duplicate-free exact ID set of that writer input and is empty iff the writer received no self-claim. The writer may neither use an unlisted self-claim nor list one it did not receive.

`source_fact_ids` is non-empty, duplicate-free, and names only supplied selected facts; `source_log_ids` is the exact duplicate-free raw-log set reachable through those facts; and `source_self_claim_ids` is the exact supported-self-claim input set. Every `matched_jd_requirements` value is duplicate-free and resolves to a `JDRequirement.id` in the supplied `ParsedJD`. Stage 10 rejects any out-of-context provenance, unsupported claim, free-form requirement label, missing requirement ID, or wrong-job ID under §12 rule 10.

The writer sets only the seven output fields shown. Stage 10 supplies `id`, `created_at`, `superseded_at`, `branch_id`, and initial `verification_status = "unverified"`; Stage 11 alone supplies verifier fields. The writer receives snapshot ID/scope/target only as structural branch context, never as another prose source.

## §15.7 Resume Verifier Contract

Input:

```json
{
  "resume_bullet": {},
  "source_facts": [],
  "source_logs": [],
  "source_self_claims": [],
  "job_description": {
    "id": "jd_001",
    "parsed": "<complete §11.13 ParsedJD for the branch job description — canonical example in §15.9>"
  }
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

Stage 11 loads this exact typed job description through `resume_bullet.branch_id → ResumeBranch.job_description_id`. The verifier resolves every `resume_bullet.matched_jd_requirements` entry against its `parsed`; an absent branch association or a missing, duplicate, or wrong-job requirement ID is invalid structured input and fails closed before a semantic verdict.

## §15.8 Gap and Contradiction Detector Contract

Input:

```json
{
  "facts": [
    {
      "id": "fact_001",
      "created_at": "2026-07-11T10:00:00+02:00",
      "superseded_at": null,
      "claim": "Designed a local prototype.",
      "claim_kind": "observed_fact",
      "project": "Exp2Res",
      "role": null,
      "company": null,
      "context": "independent_project",
      "ownership_level": "designed",
      "action": "designed",
      "object": "local prototype",
      "outcome": null,
      "skills": ["system design"],
      "technologies": [],
      "themes": ["prototyping"],
      "occurred": {
        "start": "2026-06-01T00:00:00+02:00",
        "end": null,
        "precision": "month",
        "confidence": "medium"
      },
      "source_log_ids": ["log_001"],
      "evidence_item_ids": ["evidence_001"],
      "confidence": "medium",
      "metadata": {}
    }
  ],
  "evidence_context": [
    {
      "evidence_item": "<evidence_001: complete §11.3 EvidenceItem for log_001 — canonical example in §15.2>",
      "raw_log": {
        "id": "log_001",
        "recorded_at": "2026-07-11T09:55:00+02:00",
        "entry_type": "manual_retro",
        "source_type": "user_memory",
        "occurred": {
          "start": "2026-06-01T00:00:00+02:00",
          "end": null,
          "precision": "month",
          "confidence": "medium"
        },
        "raw_text": "I tested this only locally, but I also called it production-grade; I do not know whether it works at production scale.",
        "project": "Exp2Res",
        "external_ref": null,
        "corrects_log_id": null,
        "metadata": {}
      }
    }
  ]
}
```

Output:

```json
{
  "gap_questions": [
    {
      "target_type": "experience_fact",
      "target_id": "fact_001",
      "question": "Was the prototype ever used outside a local environment?",
      "reason": "unclear_artifact_status",
      "priority": "medium"
    }
  ],
  "contradictions": [
    {
      "title": "Prototype scope conflicts with a production claim",
      "description": "The current fact supports a local prototype while another supplied statement claims production use.",
      "left_ref_type": "experience_fact",
      "left_ref_id": "fact_001",
      "right_ref_type": "raw_log",
      "right_ref_id": "log_001"
    }
  ],
  "warnings": []
}
```

The input arrays contain complete §11.4 `ExperienceFact`, §11.3 `EvidenceItem`, and §11.2 `RawLog` objects. `facts` is the complete current fact set; `evidence_context` covers the effective lineage evidence defined in §13.4 — every governing raw log and its linked evidence items under §13.3 rule 10 and §14.4, including effective records that produced no fact. Records displaced by a selected correction are not inputs: a correction is a supersession of raw interpretation, not a conflicting current position for the detector to rediscover.

The output is the complete candidate generation for the complete input, not an incremental patch and not a verifier verdict. `target_type`, `left_ref_type`, and `right_ref_type` are restricted to `raw_log`, `evidence_item`, or `experience_fact`; every target must occur in the input and pass §12 rule 10. Gap `reason` and `priority` use `GapTrigger` and `GapPriority` (§10). The service supplies IDs, timestamps, supersession fields, empty `Contradiction.metadata`, and initial gap answer state; no detector output field or metadata channel can carry verification status, resolution, dismissal, or a resolution note.

Schema, enum, reference, or completeness-shape invalidity follows the single §15.1 retry and atomic failure path. Before persistence, every detector-authored `question`, `title`, `description`, and warning message must also pass the generated-voice rules in §16.12; a voice violation fails the Stage 4 candidate atomically without an LLM retry, status, verdict, or repair call. A schema-valid and voice-valid semantic set completes the LLM call even when it reports a conflict or no conflict; it never triggers writer repair, mutates prior detections, or becomes an owner-verdict channel.

## §15.9 Job Description Parser Contract

Input:

```json
{
  "job_description": {
    "id": "jd_001",
    "raw_text": "We require evidence-grounded LLM workflow design. Production operations experience is preferred."
  }
}
```

Output:

```json
{
  "title": null,
  "company": null,
  "parsed": {
    "requirements": [
      {
        "kind": "required_skill",
        "text": "Evidence-grounded LLM workflow design",
        "keywords": ["LLM", "evidence-grounded"]
      },
      {
        "kind": "preferred_skill",
        "text": "Production operations experience",
        "keywords": ["production operations"]
      }
    ],
    "seniority_signals": [],
    "domain_signals": ["LLM systems"],
    "keywords": ["LLM", "evidence-grounded", "production operations"],
    "red_flags": []
  },
  "warnings": []
}
```

The parser output contains every model-authored field of `JobDescription` and `ParsedJD`; as with entity IDs in sibling contracts, it omits service-set `JobDescription.id`, `created_at`, and `JDRequirement.id`. Stage 8 assigns a globally unique opaque ID to every validated requirement, constructs the final §11.13 `ParsedJD`, and validates that typed model plus ID uniqueness before atomically persisting the `JobDescription`. No untyped parsed payload may be stored.

`kind` uses `JDRequirementKind` (§10). Requirement text must preserve the source's required/preferred modality and must not convert a keyword, signal, or red flag into a matchable requirement. Parsed requirement/signal/keyword/red-flag text is LLM output and therefore generated voice under §16.12 — structurally validated, bound by this contract's fidelity rules, never a quotation channel — while only the input `JobDescription.raw_text` is source voice. Under §16.12's owner-referential rule, faithfully preserved demand wording such as "expert" or "production" characterizes the vacancy, not the owner: it cannot make a faithful parse unpersistable, and no §16 rule may force rewriting the demand's meaning. Any Exp2Res-authored assertion that the owner satisfies a requirement remains fully bound wherever it appears. Invalid model-authored structure or enum values receive only the schema retry in §15.1. Failure to allocate valid service-owned IDs is handled locally or fails atomically without another parser call. A schema-valid parse is not a verdict and does not invoke another writer or mutate the source job-description text.

---
