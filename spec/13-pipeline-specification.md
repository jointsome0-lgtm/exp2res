## §13. Pipeline Specification

A pipeline stage exists only when it has at least one CLI trigger in §14 and produces a downstream-referenceable persisted business artifact or persisted state transition. `processing_runs` telemetry alone does not qualify an operation as a stage.

§14 is the sole canonical home of command forms. Each stage below points to its §14 trigger instead of restating shell syntax.

Unless a stage explicitly says otherwise, its inputs and outputs are current rows (`superseded_at IS NULL`) under §11–§12. Historical rows are inspectable but never implicit processing inputs.

Every stage validates its typed output references under §12 rule 10 before committing business rows. Missing, wrong-type, superseded, or duplicate targets fail the producing run atomically; JSON representation is not an integrity exception.

Whenever any transition supersedes a current `AssessmentSnapshot`, `ResumeBranch`, or `ResumeBullet`, it also enumerates and attempts to remove all dependent managed assessment/resume artifacts under `out/`. Database invalidation remains committed if cleanup fails; every residual path is reported as an unsuccessful invalidation and no command may report the stale files as current output.

## §13.1 Stage 1 — Raw Capture and Evidence Recording

Triggers: capture and import commands in §14.2–§14.5; gap-answer capture in §14.7.

Inputs:

```text
manual daily logs
manual retrospective logs
gap answers
corrections
Tick-like JSONL exports
Atlas artifact refs
GitHub commits
local design documents
```

Persisted outputs:

```text
raw_logs
evidence_items
```

Rules:

1. Raw text must be non-empty.
2. `recorded_at` must always be set.
3. Occurred precision must always be explicit.
4. Imported artifacts must keep their source URI/path.
5. Each accepted source record is persisted atomically as one `RawLog` plus its linked `EvidenceItem` records before the command returns; a batch import may persist multiple such pairs.
6. A manual daily log, retrospective log, gap answer, or correction receives its linked `EvidenceItem(strength=manual_claim)` when the `RawLog` is persisted; there is no later normalization stage.
7. Import commands create linked evidence items under §14.5; §19 defines the integration payload contracts.
8. `commit_or_pr` is used for an imported VCS commit; `code_artifact` is reserved for source or build evidence not represented by a commit.
9. Capture and evidence recording do not create self-claims or interpret any input as a strong fact.
10. Automation may not update or delete a retained `RawLog` or its linked `EvidenceItem`s. A correction is an appended, self-contained `RawLog` whose validated `corrects_log_id` identifies its target; §14.4 then invokes §13.13.

Owner deletion is triggered only by §14.11 and follows §13.13. It is a raw-layer lifecycle operation, not another capture stage.

## §13.3 Stage 3 — Experience Fact Extraction

Triggers: fact extraction in §14.6; lifecycle recomputation in §14.12.

Input:

```text
raw_logs + evidence_items
```

Persisted outputs:

```text
experience_facts
fact_sources
```

Rules:

1. Extract atomic claims.
2. Preserve temporal precision under §16.7.
3. Preserve ownership level under §16.4.
4. Do not infer metrics unless evidence explicitly contains metrics.
5. Do not infer production use unless explicitly present.
6. Every fact selects at least one input `EvidenceItem`; only selected items receive §12.4 rows and every selected item belongs to the correction lineage being extracted. Stage 3 persists each selected item as `direct`. V1 has no separate corroboration producer, so it never infers a `corroborating` row silently.
7. `ExperienceFact.evidence_item_ids` is exactly the duplicate-free selected-item set, and `source_log_ids` is exactly the duplicate-free set of `EvidenceItem.raw_log_id` values reached through it. Every listed raw log therefore contributes at least one selected evidence item. Multiple selected items from one raw log produce separate `fact_sources` rows.
8. `ExperienceFact.confidence` must be calibrated from every distinct linked `EvidenceItem.strength`; confidence and evidence strength remain separate axes (§9.3).
9. `ExperienceFact.claim_kind` follows the fact-extractor producer semantics in §15.2; §13 does not restate that contract.
10. The extraction unit is one correction lineage: a root `RawLog` plus every correction that reaches it through `corrects_log_id`, ordered by `recorded_at` and then ID. For a fact affected by corrections, the latest selected correction's effective `OccurredAt` under §14.4 governs; otherwise the root placement remains. The fact's required `occurred` value must preserve that source's shape and may not increase precision under §16.7. If owner deletion nulls a correction's target, that correction becomes a new lineage root.
11. Extraction computes the complete current fact generation for each selected lineage. A validated replacement and the `superseded_at` transition of the lineage's previous current facts commit atomically; it never appends a second current copy. Repeating extraction may add processing history or a superseded generation, but after success there is exactly one current fact generation for that lineage.
12. If a replacement changes current facts, every current gap, contradiction, signal, claim, snapshot, resume branch, and bullet is invalidated before it can be reused. §14.12 regenerates Stages 4–7; resume branches and managed exports require explicit regeneration from the new current snapshot.

Bad fact:

```text
The user is strong at agent systems.
```

Good facts:

```text
The user designed a verifier loop for Exp2Res.
The user wrote a system design document for Atlas.
The user repeatedly worked with provenance-heavy local-first system ideas.
```

## §13.4 Stage 4 — Gap and Contradiction Detection

Triggers: gap and contradiction generation in §14.7; lifecycle recomputation in §14.12.

Persisted outputs:

```text
gap_questions
contradictions
```

Gap triggers are the `GapTrigger` values (§10); each generated gap question records its trigger as `GapQuestion.reason` (§11.10).

Each successful run replaces the complete current gap/contradiction generation derived from all current facts. The prior current generation becomes superseded in the same transaction; inputs or output references to superseded rows are invalid.

A changed Stage 4 generation atomically supersedes every current signal, claim, snapshot, resume branch, and resume bullet before those rows can be reused. Regenerating those higher layers requires their §14 triggers or the shared §14.12 flow.

The V1 Stage 4 producer may persist only gaps and contradictions whose polymorphic targets are retained Stage 1 evidence or current Stage 3 facts. It rejects targets owned by Stage 5 or later, because the same Stage 4 replacement invalidates those upper generations.

Contradiction examples:

```text
Fact A: user claims strong project execution.
Fact B: user repeatedly reports burnout under plans.

Fact A: one experience fact says production-grade.
Fact B: another source-backed fact supports only a local prototype.
```

## §13.5 Stage 5 — Self-Signal Extraction

Triggers: self-signal generation in §14.8; lifecycle recomputation in §14.12.

Input:

```text
experience_facts
evidence_items
gap answers
contradictions
```

Persisted output:

```text
self_signals
```

Signal categories are the `SignalType` values (§10), carried by `SelfSignal.signal_type` (§11.5). §13 must not restate them.

Signal extraction consumes the complete current fact, answer, and contradiction sets and atomically replaces the complete current signal generation. It must not mix generations.

A changed signal generation atomically supersedes every current claim, snapshot, resume branch, and resume bullet before those rows can be reused.

Example signal:

```json
{
  "signal_type": "direction_signal",
  "statement": "The user repeatedly returns to local-first provenance-heavy systems.",
  "supporting_fact_ids": ["fact_storyworm_001", "fact_exp2res_004", "fact_atlas_002"],
  "confidence": "medium"
}
```

## §13.6 Stage 6 — Self-Assessment Synthesis

Triggers: self-assessment generation in §14.9; lifecycle recomputation in §14.12.

Input:

```text
self_signals
experience_facts
contradictions
gap questions
```

Persisted outputs:

```text
self_claims
assessment_snapshots
```

Assessment dimensions are the `SelfClaimDimension` values (§10), carried by `SelfClaim.dimension` (§11.6). §13 must not restate them.

`SelfClaim.claim_kind` follows the self-assessment-writer producer semantics in §15.4; §13 does not restate that contract.

Synthesis atomically creates a complete current claim generation and a new current snapshot from one coherent current input generation, then supersedes the prior current snapshot for the same scope and the claims owned by that snapshot. The transaction validates the reverse cardinality in §12: after the swap, every current `SelfClaim` appears in exactly one current `AssessmentSnapshot.self_claim_ids`, current snapshots share no claim rows, and no current claim is unowned. Other scopes remain current. A superseded snapshot's payload and provenance remain inspectable history after correction but cannot become a processing input.

Replacing an assessment scope also supersedes every current resume branch and bullet based on that scope's prior snapshot before they can be reused.

The assessment must include:

```text
supported strengths
weakly supported strengths
recurring interests
known gaps
risks / failure modes
contradictions
unknowns
counterevidence
next questions
```

## §13.7 Stage 7 — Assessment Verification

Triggers: assessment verification in §14.9; lifecycle recomputation in §14.12.

Persisted outputs:

```text
self_claims verification_status and counterevidence
assessment_snapshots verification_status
```

Verifier checks:

1. Every self-claim has sources.
2. Each `SelfClaim.confidence` is justified by the strength and scope of its supporting facts' linked evidence; confidence and evidence strength remain separate axes (§9.3).
3. Counterevidence is not hidden.
4. Identity claims are not over-broad.
5. Self-assessment does not become motivational fiction.
6. No clinical/diagnostic claims are generated.
7. No resume-style overclaiming leaks into mirror mode.
8. Every verified claim and snapshot is current and all of its referenced current entities resolve; superseded historical snapshots are inspect-only.

If re-verification changes a current claim or snapshot status after resume generation, every branch and bullet based on that snapshot is superseded and dependent managed-export removal is attempted under the global rule above; a verifier result may not leave a now-unsupported resume current.

## §13.8 Stage 8 — Job Description Parsing

Trigger: job-description addition in §14.10.

Persisted output:

```text
job_descriptions
```

Extract:

```text
required skills
preferred skills
responsibilities
seniority signals
domain signals
keywords
red flags
```

## §13.10 Stage 10 — Relevance-Aware Resume Generation

Trigger: resume generation in §14.10.

Inputs:

```text
job_description
assessment_snapshot
experience_facts
supported self_claims
linked raw_logs and evidence_items
```

Persisted outputs:

```text
resume_branches
resume_bullets
```

Generation selects and ranks relevant facts and supported self-claims for the supplied job description. Matching is internal to this stage, must not invent relevance, and is persisted on each bullet through `ResumeBullet.target_role_relevance` and `ResumeBullet.matched_jd_requirements`; there is no separate match artifact or stage.

The supplied assessment snapshot, facts, and self-claims must all be current. A replacement fact or assessment generation supersedes every dependent current resume branch and bullet and attempts dependent managed-export removal under the global rule above; resume generation must be run again rather than silently carrying old selections forward. Generating an existing branch name atomically supersedes that branch's prior current row and bullets, so at most one generation of the named branch is current.

Hard constraints:

1. Use only supplied facts and supported self-claims.
2. Every bullet must include `source_fact_ids`.
3. Every bullet must include `source_log_ids`.
4. Do not invent metrics.
5. Do not upgrade ownership under §16.4.
6. Do not upgrade temporal precision under §16.7.
7. Do not turn learning into employment.
8. Do not turn independent projects into company roles.
9. Do not use unsupported production/scale claims.
10. Prefer concrete engineering language over self-description.

## §13.11 Stage 11 — Resume Verification

Trigger: resume verification in §14.10.

Persisted output:

```text
resume_bullets verification_status, unsupported_phrases, and verifier_reason
```

The verifier inspects phrases, not only whole bullets.

Verification rejects a superseded branch or bullet and any bullet whose current provenance chain no longer resolves.

Example:

```text
Bullet:
"Built a production-grade LLM evaluation platform that reduced hallucinations by 40%."

Verifier findings:
"Built" -> maybe supported
"production-grade" -> unsupported
"platform" -> partially supported
"reduced hallucinations by 40%" -> unsupported
```

## §13.12 Stage 12 — Export

Triggers: assessment export in §14.9 and resume export in §14.10.

Persisted assessment outputs:

```text
out/assessment/self_assessment.md
out/assessment/self_claims.json
out/assessment/evidence_map.json
out/assessment/gap_questions.md
out/assessment/contradictions.md
```

Persisted resume outputs:

```text
out/<branch>/resume.md
out/<branch>/evidence_map.json
out/<branch>/verification_report.md
out/<branch>/gap_questions.md
```

Export accepts only a current snapshot or branch whose complete current provenance chain resolves under §12 rule 10 and §16.1. Unexpected missing or inconsistent links fail closed. Owner deletion does not leave a partial database graph for export to skip: §13.13 purges all derived generations, attempts verified managed-output removal, and reports residual paths as incomplete before rebuilding. Export remains unavailable until recomputation and any requested resume regeneration succeed.

## §13.13 Derived Lifecycle and Recompute

Trigger: lifecycle recomputation in §14.12; correction and owner-deletion operations in §14.4 and §14.11 invoke the same service flow.

This subsection orchestrates existing stages and is not a pipeline stage. Each invoked stage creates its own `processing_runs` row under its stable §13 identifier.

Rules:

1. Selected-lineage recomputation under §14.12 replaces Stage 3 facts for that correction lineage, then regenerates the complete current Stage 4–7 graph from all current facts. Full recomputation under §14.12 replaces facts for every lineage before the same global Stage 4–7 rebuild.
2. A recompute validates every stage's complete candidate output, including §12 rule 10, before the business-state swap. A successful swap leaves at most one current generation per lineage/scope and marks the replaced generation `superseded_at`; payloads are never updated in place.
3. A standalone rerun whose inputs have not changed may retain the prior current generation or replace it, but it must never expose duplicate current facts, signals, claims, snapshots, gaps, or contradictions. If validation fails before a source change, the prior current generation remains current and no partial candidate output is inserted.
4. Correction capture and invalidation are one atomic database visibility boundary before rebuilding starts: the transaction inserts the new raw/evidence records, supersedes current facts for that correction lineage, and supersedes every current gap, contradiction, signal, claim, snapshot, resume branch, and resume bullet. Managed exports are enumerated and removal is attempted as part of the same operation; residual paths are reported as an unsuccessful invalidation rather than silently retained. A crash or recompute failure can therefore leave the correction plus no replacement current graph, but can never leave the pre-correction graph current against the changed source set. The correction remains stored and §14.12 is the retry surface.
5. Owner deletion is a privacy-first global reset. The service first enumerates and attempts to remove every managed `out/` artifact, then atomically purges every current and historical fact, fact source, gap, contradiction, signal, claim, snapshot, resume branch, and resume bullet while hard-deleting the selected `RawLog` and cascading its evidence items. It then attempts a full recompute from every surviving lineage. Job descriptions and `processing_runs` telemetry remain. Surviving `gap_answer` raw logs stay interpretable through their §14.7 self-containment; the rebuild never re-links them to regenerated questions.
6. Database deletion commits even if managed-output removal or rebuilding fails. The command verifies the managed paths before reporting success: any residual path makes the result `deletion_incomplete`, is reported explicitly for manual removal, and is never treated as a retained evidence source. Rebuild failure reports a separate unsuccessful result with no derived database model. Neither failure restores the raw row or purged derived rows; the user may remove reported files and retry recomputation through §14.12. No FK, filesystem error, or failed processing run may restore or block database deletion.
7. The reset is deliberately global in V1. Selective graph deletion and warn-and-skip are rejected because JSON and implicit dependencies cannot prove that all private derived text was found, and a partial truth model could be mistaken for a complete one.
8. Deletion covers only Exp2Res-managed database records and `out/`. Supplied source files and copies of prior exports outside the managed workspace remain user-controlled; §14.11 reports their known paths but does not delete them.

---
