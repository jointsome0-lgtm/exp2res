## §15. LLM Contracts

## §15.1 General LLM Requirements

All LLM calls must:

1. Use structured outputs.
2. Be validated with Pydantic.
3. Fail closed on invalid output.
4. Store the processing-run execution identity and metadata defined in §12.13 and §12.15.
5. Never create, mutate, or delete raw logs; automation's raw-layer authority is append-only and capture/import services own those appends (§5.3).
6. Preserve provenance links.
7. Preserve the generated-voice/source-voice boundary in §16.12: structured source text may be evidence input, but voice rules evaluate only Exp2Res-authored candidate language and never rewrite or reject source material.
8. Apply §11's Model validation policy, including its `extra = forbid` rule for every output shape.
9. Before any provider call, deterministically preflight the fully serialized payload against §11's boundary limits alongside §29.4's credential preflight; a failure is local and fail-closed and reports only a non-secret diagnostic.
10. Emit every generated natural-language output under the V1 language scope in §16.13.

§15.10 owns transport attempts, provider capability checks, runtime budgets, context-overflow refusal, and cancellation. Its transport retry does not widen the response-validation retry below, which remains limited to schema and reference invalidity.

Under §11's field-authorship policy, a model response that sets a service-owned persisted field instead of its declared model-authored transition result, or sets any undeclared field, is invalid structured output.

If validation fails:

```text
retry once with validation errors
if retry fails, mark processing run failed
do not insert partial invalid objects
```

This retry handles only invalid structured output, including reference-validation errors. A schema-valid negative semantic verdict is successful verifier output: it does not trigger another verifier call, invoke a writer, or begin an automatic repair loop.

Structured-output validation includes §12 rule 10. If any typed reference is missing, wrong-type, superseded, or duplicated, no candidate business output or verification finding is committed; §12.13 defines the failed `processing_runs` result, stable `failure_code`, and diagnostic metadata.

Every contract `warnings` field is `list[ContractWarning]`, where each item has exactly two non-empty string fields and no extras:

```json
{
  "type": "stable_machine_code",
  "message": "Owner-facing explanation grounded in this call's input."
}
```

The one retry above applies only to an invalid model response. Failure in deterministic service enrichment after a valid response — such as allocating a collision-free service-owned ID — must be retried locally when safe or fail the processing run atomically; it must not invoke the LLM again.

Example notation: an entity's model-emitted shape appears once, at its producing contract — §15.2 (fact), §15.3 (signal), §15.4 (claim), §15.8 (gap, contradiction), §15.9 (`ParsedJD`) — and the complete persisted §11 shape is that shape plus exactly the service-owned fields §15.11's ownership matrix assigns to the producing stage — IDs, lifecycle and answer-state fields, `metadata`, and the deterministic post-response copies and derivations named there. Persisted-row examples appear where a contract consumes them: §15.2's input (`RawLog`, `EvidenceItem`), §15.4's input (`SelfSignal`, `GapQuestion`), §15.6's input (a verified `SelfClaim`). Other examples elide a repeated body to a `"<id: complete §NN.N Model — canonical example in §NN.N>"` string pointing at the named example. The literal `{}` candidates in §15.5 and §15.7 are schema-envelope notation for the complete typed candidate, never empty transport objects; those examples demonstrate response shape rather than a reproducible verdict from hidden candidate content. A behavior-bearing object whose concrete content the same example's output depends on is never elided: §15.8 shows its fact and raw log in full. §11 remains the normative field source (§12 rule 1); placeholders are example notation only, and the service always passes the complete typed objects the surrounding prose requires except where a contract declares a narrower projection: §13.3 rule 10 requires a displaced-record support descriptor in place of a complete `EvidenceItem` and forbids the displaced `RawLog` object, and the §15.6/§15.7 job-description input is the parsed view — `id` plus complete `ParsedJD`, with `title` and `company` in §15.6 — never `raw_text` or `created_at`. The storage-only §12 rule 13 production columns are absent from §11 shapes and never appear in these examples or calls.

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
  ],
  "displaced_support_items": []
}
```

Output:

```json
{
  "facts": [
    {
      "claim": "Designed provenance links between generated outputs and source records.",
      "claim_kind": "observed_fact",
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
      "occurred": null,
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

`raw_logs` contains exactly the ordered effective records of one correction lineage under §13.3 rule 10; filtering displaced members does not reorder the lineage. The root or owner-deletion orphan appears first when it is effective, and the remaining effective records retain §13.3's ascending `recorded_at` / ID byte order. `evidence_items` contains exactly the complete `EvidenceItem` objects linked to those effective records and is ID-ordered ascending by byte order. The extractor produces one complete replacement fact set for the lineage rather than extracting mutually inconsistent generations independently.

Each raw log passes its `metadata` through this contract unmodified. For `gap_answer` logs it carries the §14.7 question context (`question_text`, `question_reason`); the extractor must interpret the answer text against that question — a contextual answer such as a bare quantity is meaningless without it — while still attributing extracted facts to the answer log itself.

`displaced_support_items` contains exactly one §13.3 rule 10 displaced-record support descriptor for each non-`manual_claim` `EvidenceItem` linked to a displaced record of the lineage, ID-ordered ascending by byte order. §13.3 rule 10 is the sole definition of that call-time projection and its displaced-`RawLog` exclusion; this array is the Stage 3 instance. A displaced `manual_claim` item never appears in either evidence array. The example array is empty because its lineage has no correction.

An extractor may select a displaced-support descriptor's `id` only as support within that item's §9.4 evidential scope; the descriptor's `raw_log_id` then joins the derived `source_log_ids`. The descriptor is never a content source: every emitted fact and every content-bearing field must trace to content supplied by the effective `raw_logs` and `evidence_items`. Thus a correction may restate a corrected imported fact and select both the correction's item and the displaced root's non-manual descriptor, but no displaced raw text, summary, copied question context, or manual claim can be re-emitted. A descriptor is also never a fact's only selection: every fact selects at least one effective-record evidence item whose content states it (§13.3 rule 6), and a fact whose selections are all descriptors is invalid structured output.

Each fact output selects its supporting evidence explicitly through `evidence_item_ids`. Persistence accepts only item IDs supplied in `evidence_items` or `displaced_support_items`, verifies §13.3 rule 10 and §12.4 selectability, and derives `source_log_ids` as exactly their distinct raw-log set before writing one `direct` §12.4 row per item (§13.3 rule 7). A selected descriptor's owner `RawLog` is intentionally absent from `raw_logs`, but its `raw_log_id` still appears in the derived set. Every linked item participates in §9.4 confidence calibration within its evidential scope.

The extractor's emitted `confidence` must satisfy §9.4's deterministic ceiling and its materially-conflicting-context cap for the selected evidence, and may be conservatively lower.

Every fact output carries every model-authored §11.4 field shown above; Stage 3 supplies `id`, `created_at`, `superseded_at`, `metadata`, the copied `project` provenance, the derived `source_log_ids`, and the default `occurred` placement (§15.11). Optional/default model-authored fields are explicit in the contract so a model change cannot silently fall outside the structured boundary.

The extractor does not emit `project`: after the response validates, Stage 3 copies it exactly from each fact's governing record — per §13.3 rule 10, the latest by (`recorded_at`, then ID) of the effective records it selects — under rules 10 and 13, `None` when that record carries none. §13.6 project views select subjects through this copied field, so no model-authored, renamed, or re-cased value can reach it.

`occurred` is the one retained narrowing channel: the output field is nullable, `null` means the governing record's `OccurredAt` under §13.3 rule 10, and Stage 3 copies that placement after validation — the governing record is per fact, the latest of the effective records it selects, and when the root is the fact's only selected effective record, the root governs. A non-null value stays model-authored because it encodes a semantic judgment the service cannot derive (§15.11): a contained narrower placement the selected effective-record context explicitly states — the additional linked support required by §16.7, not a model inference — or a conservative reduction of `occurred.confidence` below the governing record's. A non-null placement may never widen beyond its governing record's source window, set `occurred.precision` / persisted `temporal_precision` stronger than the strongest explicit in-context temporal support, or set `occurred.confidence` / persisted `temporal_confidence` above its governing record's temporal confidence under §10's order. When support conflicts or containment cannot be established, the extractor preserves the governing window — returning `null`, or a non-null placement that only lowers temporal confidence — rather than change the window or choose a stronger one.

For `ExperienceFact.claim_kind`, `observed_fact` means the linked sources directly state or demonstrate the narrow claim; `inferred_fact` means the claim is a conservative derivation whose source links and `confidence` assigned under §9.4 remain explicit. Other `ClaimKind` values are invalid fact-extractor outputs.

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

`evidence_items` is exactly the duplicate-free set reached through the supplied current facts, serialized under §13.3 rule 10's universal displaced-record projection, and is context for §9.4 confidence calibration; signal provenance remains the fact IDs in §11.5. Candidate `SelfSignal.confidence` obeys §9.4's propagation caps. Prior signals are never inputs because Stage 5 produces a complete replacement generation. Raw gap answers are not inputs either: §13.5 requires them to pass through Stage 3 first, so only re-extracted current facts and their linked evidence can influence this contract.

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
  "context_facts": [],
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
  "warnings": []
}
```

For `SelfClaim.claim_kind`, `pattern_signal` summarizes a recurring supported pattern, `hypothesis` marks a tentative interpretation, and `narrative_summary` synthesizes already supported claims without adding a new fact. Other `ClaimKind` values are invalid self-assessment-writer outputs.

Candidate `SelfClaim.confidence` obeys §9.4's source-maximum cap at the Stage 6 boundary; Stage 7 judges whether the listed sources actually cover the claim's breadth.

The writer emits exactly one `narrative_summary` self-claim. Stage 6 assigns its ID, includes it in the snapshot's `self_claim_ids`, and service-copies its claim text into `AssessmentSnapshot.summary` (§13.6, §15.11); the writer returns no separate summary field and there is no separately unverified summary channel.

`scope` is a canonical `AssessmentScope` and `scope_target` is service-supplied structural context from §14.9. The writer must return neither field and cannot rewrite the target. `facts` is the scope's subject set selected under §13.6 and `context_facts` is exactly the duplicate-free out-of-subject fact set referenced by the supplied signals; both carry complete §11.4 objects, and for `global` the context set is empty. Claims are authored about the subject; a context fact grounds cross-target support or counterevidence and may be cited only where actually used. Every `source_fact_ids` / `source_signal_ids` value must name a supplied object; out-of-context provenance is invalid structured output. `gaps` is the complete current unanswered set; answered current rows are not passed. The writer returns no unknowns echo: Stage 6 service-populates `AssessmentSnapshot.gap_question_ids` with exactly that supplied set (§13.6, §15.11). Known-gap assertions belong in status-bearing `SelfClaim(dimension="gap")` output. An unknown reference can render only the referenced question/uncertainty under §17; it is not an independent claim or a §16.11 bypass.

Hard instructions: apply §16.2 (mirror, no motivational rewriting), §16.3 (anti-flattery), §16.9 (identity), §16.10 (diagnostic); preserve uncertainty and mention weak evidence where relevant.

## §15.5 Assessment Verifier Contract

Input:

```json
{
  "self_claim": {},
  "scope": "project",
  "scope_target": "Exp2Res",
  "source_signals": [],
  "scope_signals": [
    "<signal_001: complete §11.5 SelfSignal — canonical example in §15.4>"
  ],
  "scope_facts": [
    "<fact_007 and every other supplied view fact: complete §11.4 ExperienceFact objects — canonical example in §15.2>"
  ],
  "source_facts": [
    "<fact_007: complete §11.4 ExperienceFact — canonical example in §15.2>"
  ],
  "source_evidence_items": [
    "<evidence_007: complete §11.3 EvidenceItem or §13.3 rule 10 displaced-record support descriptor for fact_007>"
  ],
  "source_logs": [
    "<log_007: complete non-displaced §11.2 RawLog reached through evidence_007 — canonical example in §15.2>"
  ]
}
```

Output:

```json
{
  "status": "partially_supported",
  "unsupported_phrases": ["strong production experience"],
  "counterevidence": [
    {
      "statement": "The only deployment fact describes a local demo, not a production environment.",
      "source_ref_type": "experience_fact",
      "source_ref_id": "fact_007"
    }
  ],
  "suggested_rewrite": "Evidence supports repeated design work around local-first provenance systems, but not production experience.",
  "reason": "No source facts support production deployment or production ownership."
}
```

`scope` and `scope_target` are the snapshot's §11.7 values, supplied as structural context so the verifier can judge scope fit under §13.7 check 11; the verifier returns neither field. `source_signals` is exactly the claim's duplicate-free `source_signal_ids` set. `scope_signals` and `scope_facts` are the complete deterministic §13.6 selection for the snapshot's view, re-derived from current rows: every signal, and the union of the view's §15.4 `facts` and `context_facts`, including the cited members. They exist so check 3 can see a contrary signal or fact the writer's account omits; the closure alone deepens into evidence context, so uncited view facts arrive as fact rows without extra raw text. An omitted contrary bundle member grounds a non-passing status and may persist as a typed counterevidence reference to that `scope_facts` or `scope_signals` member, keeping a navigable contrary source in the exported mirror. `source_facts` is the duplicate-free provenance closure of the claim: its `source_fact_ids` plus every listed source signal's `supporting_fact_ids` and `counter_fact_ids`. `source_evidence_items` is exactly the duplicate-free `EvidenceItem` set reached through those facts' §12.4 rows, serialized under §13.3 rule 10: an item linked to a non-displaced record arrives as its complete object, while an item linked to a displaced record arrives as the displaced-record support descriptor. `source_logs` is exactly the duplicate-free retained `RawLog` object set referenced by the non-displaced members; a displaced log is supplied only through the descriptor's `raw_log_id` reference, never as an object. Every input array is ID-ordered (ascending byte order), so conforming implementations assemble one identical displacement-aware bundle. This remains complete context for the §9.4 strength/scope judgment required by §13.7 rule 2: descriptors carry `strength` and `raw_log_id`, so a signal-only claim still supplies its underlying evidence, scoped strength and the same-log independence rule remain visible, and the verifier never judges calibration from hidden state. The bundle is exact after projection — §13.7 forbids any other narrowing and §29.3 forbids widening it.

`counterevidence` is a list of typed `CounterevidenceItem` entries (§11.6), empty when none: each carries a contrary-evidence `statement` and a (`source_ref_type`, `source_ref_id`) grounding reference that must resolve to a member of this call's supplied bundle — a fact in `source_facts` or `scope_facts`, an item in `source_evidence_items` (including a displaced-record support descriptor member), a log in `source_logs`, or a signal in `scope_signals`. A descriptor member remains a legal `evidence_item` grounding reference because it is a supplied bundle member. A reference outside that bundle, a wrong-type or missing target, or a duplicate (`source_ref_type`, `source_ref_id`) pair is invalid structured output under §15.1 and §12 rule 10. Stage 7 persists the validated list to `SelfClaim.counterevidence` and inside the complete §11.14 `VerificationFinding` for that claim (§11.6, §13.7).

Every `status` uses the canonical meaning in §16.11. Stage 7 validates one finding for every claim in the snapshot and derives the snapshot's own status from those claim results; the writer or verifier may not assign a more permissive snapshot label independently.

`suggested_rewrite` is owner-facing advisory output presented through §14.9 and follows §11.14's inspect-only lifecycle; revised wording requires a Stage 6 replacement generation (§13.7).

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
    "source_fact_ids": ["fact_001"]
  },
  "warnings": []
}
```

Hard rule:

```text
Self-claims can guide selection and wording, but resume bullets must still link to concrete experience facts and raw logs.
```

Stage 10 invokes this contract in an isolated model context once per planned bullet and passes only the `supported` self-claims selected for that bullet. No invocation can see another bullet's facts or claims. It does not pass `AssessmentSnapshot.title` or `.summary` as independent prose inputs. Stage 10 service-sets the persisted bullet's `source_self_claim_ids` to the duplicate-free exact ID set of that invocation's self-claim input, empty iff the writer received none (§15.11); the writer does not return the field, so it can neither use an unlisted claim nor list one it did not receive.

Each `selected_facts[].evidence` array contains one ID-ordered entry for every §12.4 row of that fact. Under §13.3 rule 10, an item linked to a non-displaced record is a complete `EvidenceItem` paired with its complete `RawLog`; an item linked to a displaced record is the displaced-record support descriptor paired with `raw_log = null`. No displaced `RawLog` object is supplied. The descriptor can support the fact only within its §9.4 scope and cannot source bullet content; content-bearing record evidence remains the effective-record context used at Stage 3.

`source_fact_ids` is non-empty, duplicate-free, and names only supplied selected facts; it stays model-authored because it records which supplied facts actually ground the bullet's text — an independent semantic selection (§15.11). After validation Stage 10 derives `source_log_ids` as the exact duplicate-free raw-log ID set reachable through those facts, including each descriptor's `raw_log_id` even though its `raw_log` member is `null`. Every `matched_jd_requirements` value is duplicate-free and resolves to a `JDRequirement.id` in the supplied `ParsedJD`. Stage 10 rejects any out-of-context provenance, unsupported claim, free-form requirement label, missing requirement ID, or wrong-job ID under §12 rule 10.

The writer sets only the five output fields shown. Stage 10 supplies `id`, `created_at`, `superseded_at`, `branch_id`, the derived `source_log_ids`, the exact-input `source_self_claim_ids`, and initial `verification_status = "unverified"` (§15.11); Stage 11 alone supplies verifier fields. The writer receives snapshot ID/scope/target only as structural branch context, never as another prose source.

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

`status` uses §16.11. Stage 11 validates one finding for every current bullet and persists `status`, `unsupported_phrases`, and `reason` both to the denormalized `ResumeBullet.verification_status`, `unsupported_phrases`, and `verifier_reason` fields and inside the complete §11.14 `VerificationFinding` (§11.8, §13.11). `suggested_rewrite` is owner-facing advisory output presented through §14.10 and follows §11.14's inspect-only lifecycle; revised wording requires a Stage 10 replacement generation (§13.11).

Stage 11 applies §13.3 rule 10 when assembling this provenance context. `source_facts` is exactly the duplicate-free fact set named by `resume_bullet.source_fact_ids`. `source_logs` contains exactly the duplicate-free retained `RawLog` objects reached through those facts' §12.4 rows whose owning records are not displaced; it never contains a displaced `RawLog` object. Both arrays are ID-ordered ascending by byte order. This contract serializes no `EvidenceItem` object. A fact may retain displaced-support identities in its `evidence_item_ids` and `source_log_ids`, but those remain opaque provenance references here: neither the displaced item nor its `RawLog` is hydrated. `resume_bullet.source_log_ids` remains the exact raw-log identity set reached through all source facts, including displaced identities whose objects are intentionally absent, so provenance stays visible without displaced prose.

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

The input arrays contain complete §11.4 `ExperienceFact`, §11.3 `EvidenceItem`, and §11.2 `RawLog` objects. `facts` is the complete current fact set. `evidence_context` is exactly the effective lineage evidence defined by §13.3 rule 10: every complete effective `RawLog` and every complete `EvidenceItem` linked to one, including effective records that produced no fact. A displaced record and its linked items are neither detector input nor detector targets, and Stage 3 `displaced_support_items` descriptors are not supplied to Stage 4. A current fact that cites displaced-record support remains an ordinary `facts` input row; its nested `evidence_item_ids` and `source_log_ids` do not make the unsupplied item or record an input object or a legal detector target.

The output is the complete candidate generation for the complete input, not an incremental patch and not a verifier verdict. `target_type`, `left_ref_type`, and `right_ref_type` are typed `DetectionRefType` (§10); every referenced target must resolve to a supplied input object and pass §12 rule 10. Gap `reason` and `priority` use `GapTrigger` and `GapPriority` (§10). The service supplies IDs, timestamps, supersession fields, empty `Contradiction.metadata`, and initial gap answer state; no detector output field or metadata channel can carry verification status, resolution, dismissal, or a resolution note.

Schema, enum, reference, or completeness-shape invalidity follows the single §15.1 retry and atomic failure path. Before persistence, every detector-authored `question`, `title`, `description`, and warning message must also pass the generated-voice rules in §16.12; a voice violation fails the Stage 4 candidate atomically without an LLM retry, status, verdict, or repair call. A schema-valid and voice-valid semantic set completes the LLM call even when it reports a conflict or no conflict; it never triggers writer repair, mutates prior detections, or becomes an owner-verdict channel.

## §15.9 Job Description Parser Contract

Input:

```json
{
  "job_description": {
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

The parser input is the owner-supplied vacancy payload, not a persisted entity: §13.8 persists no `JobDescription` until the parse validates, so at call time no job-description entity or entity ID exists and none transits (§29.3). The parser output contains every model-authored field of `JobDescription` and `ParsedJD`; as with entity IDs in sibling contracts, it omits service-set `JobDescription.id`, `created_at`, and `JDRequirement.id`. After a valid model response, Stage 8 assigns those service-owned values under §11's post-response rule — the entity's own ID and a globally unique opaque ID for every validated requirement — constructs the final §11.13 `ParsedJD`, and validates that typed model plus ID uniqueness before atomically persisting the `JobDescription`. No untyped parsed payload may be stored.

`kind` uses `JDRequirementKind` (§10). Requirement text must preserve the source's required/preferred modality and must not convert a keyword, signal, or red flag into a matchable requirement. Parsed requirement/signal/keyword/red-flag text is LLM output and therefore generated voice under §16.12 — structurally validated, bound by this contract's fidelity rules, never a quotation channel — while only the input `JobDescription.raw_text` is source voice. Under §16.12's owner-referential rule, faithfully preserved demand wording such as "expert" or "production" characterizes the vacancy, not the owner: it cannot make a faithful parse unpersistable, and no §16 rule may force rewriting the demand's meaning. Any Exp2Res-authored assertion that the owner satisfies a requirement remains fully bound wherever it appears. Invalid model-authored structure or enum values receive only the schema retry in §15.1. Failure to allocate valid service-owned IDs is handled locally or fails atomically without another parser call. A schema-valid parse is not a verdict and does not invoke another writer or mutate the source job-description text.

## §15.10 Transport, Budget, and Cancellation Contract

1. **Failure-class separation.** Transport failure, response-validation failure, and a valid negative semantic result are normatively distinct for retry handling. A **transport failure** means that no complete response reached local structured-output validation because request transport failed, the provider rejected the request, or the adapter/model could not support it. Its retryable instances are connection or TLS failure, a response timeout, HTTP 408, HTTP 429, HTTP 5xx, provider-reported overload, and an ambiguous lost response in which the request may have been delivered but no response was received; rule 2 names the non-retryable instances. A retryable failure increments `llm_calls.transport_retries` only before another attempt. A **response-validation failure** is the schema or reference-invalid response governed exclusively by §15.1's one retry; the first such failure increments `llm_calls.schema_retries` before that retry, while a terminal invalid response does not count a retry that never occurs. A **valid negative semantic result** is successful output: it is never retried, repaired, or escalated into another model call, although its completed verifier result may still close a consumer gate and produce §14.14's `blocked` CLI result.
2. **Bounded foreground transport retry.** One planned invocation is one §12.15 `llm_calls` row. For each request round — the initial structured-output request and, only after an invalid complete response, §15.1's validation-retry request — the retryable transport subset is exactly connection/TLS failure, response timeout, HTTP 408/429/5xx, provider-reported overload, and ambiguous lost response. HTTP 4xx other than 408 and 429, authentication or authorization failure, a provider-rejected request shape, and capability mismatch are non-retryable and fail the invocation immediately. Each request round has the configured total-transport-attempt cap, including that round's initial attempt, and bounded backoff with jitter; one per-invocation wall-clock deadline covers every round, attempt, and backoff. These are `[llm]` configuration values under rule 9, with conservative service defaults; the structure is normative and the numeric values are not. Every attempt remains synchronous inside the same foreground §14 action authorized by §29.2. Exhausting a round's cap or the invocation deadline fails the call and run with the applicable stable transport `failure_code`; nothing is queued, deferred, or resumed in the background. Tuning a service-default retry count requires observed failure distributions rather than an unmeasured SDK default.
3. **Idempotency and ambiguous lost responses.** A provider response is not a business-write channel. Business rows commit only after a received response validates locally, so duplicate provider-side completion cannot create duplicate business rows. The adapter supplies one opaque logical correlation value per (`run_id`, `call_index`) and records it in `provider_request_id`. Where the selected provider supports request idempotency, the adapter derives a distinct idempotency key for each request round from (`run_id`, `call_index`, validation round) and reuses that key only for byte-identical transport retries within that round. The §15.1 validation-retry request includes validation errors, so it uses a new idempotency key while remaining on the same call row. These values are transport metadata, never prompt fields, credentials, or content-derived values. Only a provider contract that explicitly guarantees idempotent billing for a repeated key can guarantee that such a transport retry is not billed as a second logical request. An ambiguous lost response remains a retryable transport failure on the same call row. If its retries exhaust, the run fails. Any response received after that failure is discarded and is never adopted out of band, because such adoption would be background completion forbidden by §29.2.
4. **Provider capability requirements.** Capability validation occurs when an adapter/model is selected or configured and again before the run's first transport, and fails closed. A selectable adapter/model must declare structured-output support for the §15.2–§15.9 contracts; the model identifier written to `processing_runs.model` (§12.13); maximum context and output sizes that local preflight can check; adapter timeout and cancellation support; and the deterministic credential and token classifiers required by §29.4. An adapter/model that cannot declare every requirement is not selectable.
5. **Deterministic local preflight.** Extending §15.1 rule 9 and §11's boundary limits, before every provider attempt the service computes the fully serialized input's UTF-8 byte size and deterministic token estimate, the planned maximum output-token count, and the total planned call count for the invoking stage. §11's per-list and per-payload limits bind each invocation's typed payload; they do not bound the number of correction lineages or current branch bullets in a run, so the separate per-run call ceiling binds planned invocation count. When the provider declares pricing, preflight also computes conservative per-invocation and per-run cost maxima from the planned token bounds and every potentially billable physical attempt through both request rounds' attempt caps, including the possible §15.1 validation retry and treating an ambiguous lost request as billed. It may collapse byte-identical transport retries to one charge only when the provider declares an idempotent-billing guarantee for the reused key. Preflight fails before transport with a stable code when §11's hard input byte/shape limits, a configured input/output token budget, the per-run call ceiling, an applicable per-invocation or per-run cost ceiling, or the declared model context bound would be exceeded. For a combined context window, the input estimate plus planned maximum output must fit. A §11 boundary violation retains its validation failure class; exceeding the declared model bound is `context_overflow`; any other exceeded configured hard budget is `budget_exceeded`. The privacy-safe diagnostic names the stage, exceeded limit, and measured value, never payload content.
6. **Context-overflow and complete-input policy.** No §15 input may be truncated as transport accommodation. For the complete-set contracts — Stage 4, Stage 6, each Stage 7 provenance closure, and each Stage 10 or Stage 11 per-bullet invocation — an input that exceeds the model's declared context bound or configured input budget fails closed before transport. V1 defines no partition/merge machinery. Truncating, sampling, eliding evidence, summarizing, or otherwise narrowing a declared input to fit is forbidden; the complete contract input is serialized under its declared projection or no call occurs. Narrowing would violate the completeness and exactness requirements in §13.4, §13.6, §13.7, and §15.5. The diagnostic names the stage and bounding limit. Recovery is owner-level: delete, correct, or reduce source material; raise a configured budget without exceeding the model bound; or select a declared larger-context model.
7. **Multi-call stage semantics.** Stages 3 (one call per correction lineage), 7 (one call per current claim), 10 (one call per planned bullet), and 11 (one call per current bullet) persist no partial business result: all planned invocations for the run must validate before its business replacement or verifier update commits. `processing_runs` and `llm_calls` remain durable telemetry and are not business output. §13.7, §13.10, and §13.11 already define the whole-stage atomic transactions. For a Stage 3 run spanning multiple lineages, this rule is the outer commit boundary over §13.3's per-lineage replacements; §12 rule 13 still allocates one generation ID per lineage. V1 defines no cross-command response cache: validated responses from a failed run are not reused by another run, which invokes the complete stage again with fresh calls and accepts the repeated provider cost. `llm_calls.run_id` binds every call to its §12.13 run. On success, Stage 3 and Stage 10 produced rows bind to that run through §12 rule 13's `produced_by_run_id` and `generation_id`, while Stage 7 and Stage 11 findings bind through §11.14 `VerificationFinding.produced_by_run_id`; a failed run retains telemetry but owns no business or finding row. No response-content persistence artifact is added. Stage 10 cross-bullet context isolation remains exactly as defined by §13.10 and §15.6.
8. **Cancellation and crash safety.** A no-response attempt timeout or per-invocation total-deadline exhaustion remains a transport failure under rules 1–2 and exits through §14.14's provider/transport class. Cancellation safety covers an owner interrupt, the adapter abort initiated by that interrupt, and a process crash. Before transport, a handled owner interrupt sends nothing and finishes the run and any created call row with `failure_code = "cancelled"`. During transport, the adapter aborts or abandons the request, never adopts a response even if the provider completes it, and finishes the call and run with that code. After local validation but before business commit, the owning transaction rolls back and no business row or verifier update commits. In every phase, the database exposes either the prior current generation or the already-committed §13.13 source-change invalidated state, never a partial new generation. §8.1's OS-lock release, WAL recovery, and transaction rollback govern a hard crash. Durable unfinished LLM telemetry left by a dead writer is never complete or reusable; after a later compatible writer acquires the §8.1 workspace lock and before its business operation, it marks the abandoned run and each nonterminal call `status = "failed"`, `failure_code = "cancelled"`, and supplies their missing finish times, leaving already terminal call rows unchanged. A handled owner interrupt exits through §14.14's cancelled class. A hard crash cannot emit an exit envelope from the dead process, but its recovery preserves the same database-state guarantee.
9. **Budgets and privacy-safe progress/cost reporting.** Budget values live in `.exp2res/config.toml` under `[llm]`: per-request-round transport-attempt cap, lower and upper backoff bounds, per-invocation total deadline, input and output token budgets, per-run call ceiling, and applicable per-invocation and per-run cost ceilings. The service supplies conservative defaults. These numeric values are configuration, so tuning them is not an SDD change; the existence of each budget, deterministic estimation where applicable, and fail-before-call enforcement are normative. Progress and cost output uses stderr under §14.14 and may include only stage, call index/count, token counts, and reported cost. It never includes prompt or response content or credentials, applying §12.15's telemetry content prohibition to the console as well.
10. **Stable failure codes.** `processing_runs.failure_code` and `llm_calls.failure_code` use stable machine codes and are intentionally not §10 enums (§12.13). The minimum transport/runtime vocabulary is `transport_timeout`, `transport_rate_limited`, `transport_provider_error`, `transport_lost_response`, `transport_auth_failed`, `capability_mismatch`, `budget_exceeded`, `context_overflow`, and `cancelled`; §15.1 validation and reference failures retain their separately governed stable codes. Response or HTTP timeouts and per-invocation deadline exhaustion use `transport_timeout`; HTTP 429 uses `transport_rate_limited`; ambiguous delivery uses `transport_lost_response`; authentication or authorization failure uses `transport_auth_failed`; connection/TLS failure, provider overload, HTTP 5xx, provider-rejected request shape, and other non-retryable HTTP 4xx use `transport_provider_error`. A recovered retry may finish successfully with a nonzero retry counter and no terminal failure code. From the first runner implementation, each failed attempt increments the applicable §12.15 retry counter before another attempt, and each terminal failure records its stable class on the same logical call row; transport/schema validity is never left as an unclassified SDK event.

## §15.11 Field-Ownership Matrix

Every §15.2–§15.9 transport field has exactly one authorship class under §11's Model validation policy. A field is model-authored only where removing it would erase an independent semantic choice; every value the service already fixed before the call or can derive deterministically from the validated response and its declared inputs is service-owned — supplied as input context, or computed after validation as deterministic enrichment under §15.1. A verifier-authored transition result is model output that only its owning stage may validate and apply to the persisted lifecycle fields. Owner- and importer-authored source content transits as input only and is never a model output channel. This matrix is the normative ownership home for §15 transport fields; the producing contracts and their §13 stage rules keep each field's semantics, and §11 keeps the persisted-field authorship policy.

| Contract | Model-authored output | Service-owned: input context and post-response enrichment |
|---|---|---|
| §15.2 fact extractor | per fact: `claim`, `claim_kind`, `role`, `company`, `context`, `ownership_level`, `action`, `object`, `outcome`, `skills`, `technologies`, `themes`, `evidence_item_ids`, `confidence`, nullable `occurred` — `null` is the valid default that selects the copied governing placement, non-null asserts §15.2's narrowing or confidence-reduction judgment; `warnings` | all input arrays; per fact: `id`, `created_at`, `superseded_at`, `metadata`, copied `project`, derived `source_log_ids`, the governing `occurred` placement copied when the response value is `null` |
| §15.3 signal extractor | per signal: `signal_type`, `statement`, `supporting_fact_ids`, `counter_fact_ids`, `confidence`; `warnings` | all input arrays; per signal: `id`, `created_at`, `superseded_at`, `metadata` |
| §15.4 assessment writer | per claim: `claim`, `claim_kind`, `dimension`, `source_signal_ids`, `source_fact_ids`, `confidence`, `uncertainty`; `warnings` | `scope`, `scope_target`, `gaps`, `contradictions`, and every other input; per claim: `id`, `created_at`, `superseded_at`, initial `verification_status`, `metadata`; the complete snapshot: `id`, `created_at`, `superseded_at`, `scope`, `scope_target`, `title` (derived per §13.6), initial `verification_status`, `metadata`, `summary` (copied from the `narrative_summary` claim), `gap_question_ids`, `contradiction_ids`, `self_claim_ids` |
| §15.5 assessment verifier | transition result: `status`, `unsupported_phrases`, `counterevidence`, `suggested_rewrite`, `reason` | the complete input bundle; Stage 7 alone validates and applies the result and writes the §11.14 finding |
| §15.6 resume writer | per bullet: `text`, `target_section`, `target_role_relevance`, `matched_jd_requirements`, `source_fact_ids`; `warnings` | all inputs; per bullet: `id`, `created_at`, `superseded_at`, `branch_id`, derived `source_log_ids`, exact-input `source_self_claim_ids`, initial `verification_status` with the §11.8 verifier-field defaults until Stage 11 |
| §15.7 resume verifier | transition result: `status`, `unsupported_phrases`, `suggested_rewrite`, `reason` | the complete input bundle; Stage 11 alone validates and applies the result and writes the §11.14 finding |
| §15.8 detector | per gap question: `target_type`, `target_id`, `question`, `reason`, `priority`; per contradiction: `title`, `description`, both typed refs; `warnings` | all input arrays; IDs, timestamps, supersession fields, answer state, empty `Contradiction.metadata` |
| §15.9 JD parser | `title`, `company`, complete `parsed`; `warnings` | input `raw_text` (owner-authored source, input-only); `JobDescription.id`, `created_at`, every `JDRequirement.id` |

Documented retained apparent echoes — each stays model-authored because deleting it would erase a semantic judgment: §15.2's non-null `occurred` asserts explicitly supported contained narrowing, or a conservative temporal-confidence reduction, against the copied default; §15.6's `source_fact_ids` selects which supplied facts actually ground the bullet's text; §15.5's and §15.7's `unsupported_phrases` and counterevidence references quote or cite supplied material as a verdict, not as bookkeeping. No other output field may duplicate a value the service supplied or can derive, and a response that returns a removed echo field — `project`, `source_log_ids`, `summary`, an unknowns list, or `source_self_claim_ids` — is invalid structured output under §11's `extra = forbid` and field-authorship policy.

---
