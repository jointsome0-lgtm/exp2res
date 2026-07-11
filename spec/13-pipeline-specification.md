## §13. Pipeline Specification

A pipeline stage exists only when it has at least one CLI trigger in §14 and produces a downstream-referenceable persisted business artifact or persisted state transition. `processing_runs` telemetry alone does not qualify an operation as a stage.

§14 is the sole canonical home of command forms. Each stage below points to its §14 trigger instead of restating shell syntax.

Unless a stage explicitly says otherwise, its inputs and outputs are current rows (`superseded_at IS NULL`) under §11–§12. Historical rows are inspectable but never implicit processing inputs.

Every stage validates its typed output references under §12 rule 10 before committing business rows. Missing, wrong-type, superseded, or duplicate targets fail the producing run atomically; JSON representation is not an integrity exception.

Whenever a status-bearing row is offered to resume generation or either export, the consumer applies the canonical `VerificationStatus` allowlists in §16.11. No consumer may replace those allowlists with a denylist or treat an unnamed status as passing.

Whenever any transition supersedes a current `AssessmentSnapshot`, `ResumeBranch`, or `ResumeBullet`, it also enumerates and attempts to remove all dependent managed assessment/resume artifacts under `out/`. Database invalidation remains committed if cleanup fails; every residual path is reported as an unsuccessful invalidation and no command may report the stale files as current output. `gaps answer` triggers the same enumeration, removal attempt, and residual-path reporting for the managed exports its answered state makes stale, without superseding any row (§14.7).

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
2. Preserve temporal provenance under §15.2 and §16.7. A fact copies the governing source log's `OccurredAt` by default. A narrower contained placement is legal only when selected evidence present in the extraction context explicitly supports it; extraction may never extend the source window, raise temporal precision above that explicit support, or raise `TemporalConfidence` above the governing source placement.
3. Preserve ownership level under §16.4.
4. Do not infer metrics unless evidence explicitly contains metrics.
5. Do not infer production use unless explicitly present.
6. Every fact selects at least one input `EvidenceItem`; only selected items receive §12.4 rows and every selected item belongs to the correction lineage being extracted. Stage 3 persists each selected item as `direct`. V1 has no separate corroboration producer, so it never infers a `corroborating` row silently.
7. `ExperienceFact.evidence_item_ids` is exactly the duplicate-free selected-item set, and `source_log_ids` is exactly the duplicate-free set of `EvidenceItem.raw_log_id` values reached through it. Every listed raw log therefore contributes at least one selected evidence item. Multiple selected items from one raw log produce separate `fact_sources` rows.
8. `ExperienceFact.confidence` must be calibrated from every distinct linked `EvidenceItem.strength`; confidence and evidence strength remain separate axes (§9.3).
9. `ExperienceFact.claim_kind` follows the fact-extractor producer semantics in §15.2; §13 does not restate that contract.
10. The extraction unit is one correction lineage: a root `RawLog` plus every correction that reaches it through `corrects_log_id`, ordered by `recorded_at` and then ID. For a fact affected by corrections, the latest selected correction's effective `OccurredAt` under §14.4 is the governing source placement; otherwise the root placement governs. The fact inherits that placement unless the evidence-backed narrowing permitted by rule 2 applies. If owner deletion nulls a correction's target, that correction becomes a new lineage root.
11. Extraction computes the complete current fact generation for each selected lineage. A validated replacement and the `superseded_at` transition of the lineage's previous current facts commit atomically; it never appends a second current copy. Repeating extraction may add processing history or a superseded generation, but after success there is exactly one current fact generation for that lineage.
12. If a replacement changes current facts, every current gap, contradiction, signal, claim, snapshot, resume branch, and bullet is invalidated before it can be reused. §14.12 regenerates Stages 4–7; resume branches and managed exports require explicit regeneration from the new current snapshot.

Bad fact:

```text
The user is strong at agent systems.
```

Good facts:

```text
The user designed a verifier gate for Exp2Res.
The user wrote a system design document for Atlas.
The user repeatedly worked with provenance-heavy local-first system ideas.
```

## §13.4 Stage 4 — Gap and Contradiction Detection

Triggers: gap and contradiction generation in §14.7; lifecycle recomputation in §14.12.

Stage 4 is LLM-backed because detecting semantic conflicts and missing support cannot be reduced to the structural rules below. Its complete structured boundary is §15.8.

Inputs:

```text
all current experience_facts
all retained raw_logs and evidence_items
```

Persisted outputs:

```text
gap_questions
contradictions
```

Gap triggers are the `GapTrigger` values (§10); each generated gap question records its trigger as `GapQuestion.reason` (§11.10).

Each successful run replaces the complete current gap/contradiction generation derived from all current facts. The prior current generation becomes superseded in the same transaction; inputs or output references to superseded rows are invalid.

Stage 4 alone owns the complete current contradiction set. If its current inputs still conflict, the replacement generation must retain a contradiction for that conflict; if evidence-driven inputs no longer conflict, the replacement may omit it. V1 has no direct resolve/dismiss transition on a derived contradiction row.

A changed Stage 4 generation atomically supersedes every current signal, claim, snapshot, resume branch, and resume bullet before those rows can be reused. Regenerating those higher layers requires their §14 triggers or the shared §14.12 flow.

The V1 Stage 4 producer may persist only gaps and contradictions whose polymorphic targets are retained Stage 1 evidence present in this complete input or current Stage 3 facts. Retained evidence that produced no fact remains visible and may receive a gap target. Stage 4 rejects targets owned by Stage 5 or later, because the same replacement invalidates those upper generations.

The validated §15.8 result is the complete replacement candidate set, never a patch over prior detections. The service assigns entity IDs and lifecycle fields and initializes each new gap with `answered = false` and `answer_log_id = None`. Detector output has no verification status, resolution, dismissal, or resolution-note field. A schema-valid semantic detection set is not a verdict: §15.1 retries only schema or reference invalidity and never retries merely because a conflict or gap was included or omitted.

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
their linked evidence_items
contradictions
```

Persisted output:

```text
self_signals
```

Signal categories are the `SignalType` values (§10), carried by `SelfSignal.signal_type` (§11.5). §13 must not restate them.

Signal extraction consumes the complete current fact and contradiction sets plus exactly the evidence items linked from those facts, and atomically replaces the complete current signal generation. It must not mix generations. It receives neither prior `existing_signals` nor raw gap-answer text: a self-contained `gap_answer` `RawLog` and its `EvidenceItem` first reach Stage 3 through §15.2, and only any re-extracted current facts and their linked evidence reach Stage 5. A gap answer that produces no current fact cannot influence a signal directly.

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
self-assessment scope and scope target from §14.9
self_signals
experience_facts
contradictions
gap questions
```

The Stage 6 gap input is the complete current unanswered (`answered = false`) set. Answered current rows remain valid §14.7 state until regeneration but are not unknowns and are not writer inputs.

Persisted outputs:

```text
self_claims
assessment_snapshots
```

Assessment dimensions are the `SelfClaimDimension` values (§10), carried by `SelfClaim.dimension` (§11.6). §13 must not restate them.

`SelfClaim.claim_kind` follows the self-assessment-writer producer semantics in §15.4; §13 does not restate that contract.

Synthesis atomically creates a complete current claim generation and a new current snapshot from one coherent current input generation, then supersedes the prior current snapshot for the same `AssessmentScope` and the claims owned by that snapshot. `scope_target` is persisted assessment context, not part of replacement identity; a new project-scoped snapshot therefore replaces the prior project-scoped snapshot even when the project target differs. The transaction validates the reverse cardinality in §12: after the swap, every current `SelfClaim` appears in exactly one current `AssessmentSnapshot.self_claim_ids`, current snapshots share no claim rows, and no current claim is unowned. Other scopes remain current. A superseded snapshot's payload and provenance remain inspectable history after correction but cannot become a processing input.

Every new claim and snapshot starts with `verification_status = "unverified"`. Stage 6 may not pre-authorize its own output; Stage 7 alone assigns semantic claim verdicts and derives the current snapshot status under §16.11.

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

For a project-scoped run, Stage 6 copies the exact non-blank §14.9 `--project` value into `AssessmentSnapshot.scope_target`; the writer receives it as branch-free structural context but cannot rewrite it. Current non-project command forms persist `scope_target = None`.

Known-gap assertions are emitted as ordinary `SelfClaim(dimension="gap")` rows and receive Stage 7 status. The §15.4 `unknowns` output contains references only and must enumerate every current unanswered Stage 4 `GapQuestion` exactly once; Stage 6 does not scope-filter that set and stores it unchanged in `AssessmentSnapshot.gap_question_ids`. Unknowns are uncertainty/question presentation, not claim-grade assertions: they do not receive an independent status, do not improve the §16.11 snapshot aggregate, and cannot guide Stage 10. Declarative prose about what is or is not true must be a snapshot-owned `SelfClaim` and pass the existing status gate.

Every current Stage 4 contradiction appears exactly once in `AssessmentSnapshot.contradiction_ids`; Stage 6 does not scope-filter conflicts. The writer cannot suppress one as resolved or dismissed; duplicate or stale IDs fail under §12 rule 10.

The writer emits exactly one `SelfClaim(claim_kind="narrative_summary")` whose `claim` equals `AssessmentSnapshot.summary`, and the snapshot includes that claim ID. The summary receives an ordinary Stage 7 verdict and participates in §16.11 aggregation; Stage 6 cannot place separately unverified prose in the snapshot summary.

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
9. The snapshot preserves the complete current Stage 4 contradiction set; verification cannot hide one by scope filtering, relabeling, or omission.
10. Exactly one member claim has `claim_kind = "narrative_summary"`, and its claim text equals `AssessmentSnapshot.summary`.

Stage 7 obtains a validated §15.5 verdict for every claim in the current snapshot. It commits all claim statuses and counterevidence together with the snapshot status computed by §16.11. If any finding remains invalid or missing after §15.1, no claim or snapshot verification update commits; an initially generated snapshot therefore remains `unverified`, while a failed re-verification against unchanged inputs retains the prior complete verifier state. The snapshot status is never an independent optimistic label.

One Stage 7 invocation performs one semantic verifier pass per current claim and then terminates after aggregation. A valid non-passing verdict completes verification but closes every consumer gate that disallows its status. Stage 7 returns the complete §15.5 findings to the invoking CLI command and persists only its declared verification fields; it never invokes Stage 6, applies `suggested_rewrite`, edits or drops claim prose, or creates a gap question. Revised claim wording can appear only in a later explicit Stage 6 replacement generation.

If verification or re-verification changes a current claim's status or counterevidence or the current snapshot status, all V1 managed assessment outputs under the fixed `out/assessment/` path are removed or reported as a residual-path failure. Every branch and bullet based on that snapshot is also superseded and dependent managed-resume removal is attempted under the global rule above; a verifier result may not leave a resume current against a changed verifier state.

## §13.8 Stage 8 — Job Description Parsing

Trigger: job-description addition in §14.10.

Stage 8 is LLM-backed and uses the structured parser contract in §15.9. The service persists no `JobDescription` until both the parser candidate and the final service-assigned requirement IDs validate.

Persisted output:

```text
job_descriptions
```

The resulting `JobDescription.parsed` is a `ParsedJD` (§11.13). It represents:

```text
required skills, preferred skills, and responsibilities as typed JDRequirement rows
seniority signals, domain signals, keywords, and red flags as typed string lists
```

Each requirement receives a globally unique, immutable service-assigned ID. IDs are neither list positions nor free-form labels. Invalid model-authored parser structure follows §15.1; after a valid response, the service must allocate collision-free IDs locally, and an allocation or final-model failure aborts the Stage 8 transaction without another LLM call.

## §13.10 Stage 10 — Relevance-Aware Resume Generation

Trigger: resume generation in §14.10.

Inputs:

```text
job_description with typed ParsedJD requirements
assessment_snapshot
experience_facts
supported self_claims owned by the selected assessment snapshot
linked raw_logs and evidence_items
```

Persisted outputs:

```text
resume_branches
resume_bullets
```

Generation selects and ranks relevant facts and `supported` self-claims for the supplied job description. Matching is internal to this stage, must not invent relevance, and is persisted on each bullet through `ResumeBullet.target_role_relevance` and `ResumeBullet.matched_jd_requirements`; there is no separate match artifact or stage.

Every `matched_jd_requirements` entry is a duplicate-free stable ID from the exact `ParsedJD.requirements` supplied to this Stage 10 run. The writer cannot emit a free-form requirement label or an ID from another job description. §12 rule 10 and the Stage 10 transaction reject every missing, duplicate, or wrong-job reference before a branch or bullet becomes current.

Without changing §11.12's optional field declaration, the Stage 10 producer must copy the exact §14.10 `--jd` ID into its candidate `ResumeBranch.job_description_id`. Stage 11 and Stage 12 recover the typed `ParsedJD` through that persisted association; a Stage 10 candidate with a missing or different job-description ID fails atomically.

The exact assessment snapshot selected under §14.10 is mandatory, must be current, and must be eligible to anchor Stage 10 under §16.11. The new `ResumeBranch.assessment_snapshot_id` equals that selected ID. There is no implicit latest snapshot and no unanchored generation. The snapshot supplies structural anchor, scope, membership, and status context; its title and summary prose are not independent writer inputs. If the matching narrative summary guides selection or wording, Stage 10 passes its `supported` member claim and the bullet lists that claim ID. Only supported member claims may guide generation, and §12 validates every bullet's source-claim membership before commit.

For each bullet, `source_self_claim_ids` is the duplicate-free exact set of self-claims that guided its selection or wording and is empty iff no self-claim did. The writer may not consume an unlisted claim or list a claim it did not use.

Stage 10 calls the §15.6 writer once per planned bullet in an isolated model context. Each invocation contains only facts selected for that bullet, their linked evidence items and retained raw logs, the `supported` snapshot-member self-claims selected for that bullet, explicit branch context, and the typed selected job description. Stage 10 validates every result and commits the complete branch/bullet batch atomically; one invocation can never read another bullet's facts or claims.

The supplied facts and eligible self-claims must all be current. A replacement fact or assessment generation supersedes every dependent current resume branch and bullet and attempts dependent managed-export removal under the global rule above; resume generation must be run again rather than silently carrying old selections forward. Generating an existing branch name atomically supersedes that branch's prior current row and bullets, so at most one generation of the named branch is current.

Every new bullet starts with `verification_status = "unverified"`; Stage 10 cannot grant its own output permission to export.

Hard constraints:

1. Use only supplied facts and self-claims whose status is exactly `supported` under §16.11.
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

Stage 11 owns the semantic transition from each current bullet's initial `unverified` status to one §16.11 verdict. It validates one §15.7 finding for every current bullet and commits the complete branch finding set atomically. If any finding remains invalid or missing after §15.1, no bullet verification update commits; a new branch remains `unverified`, while a failed re-verification against unchanged inputs retains the prior complete verifier state. A branch remains ineligible for resume export unless every bullet satisfies the resume-export allowlist.

One Stage 11 invocation performs one semantic verifier pass per current bullet, returns the complete findings to the invoking CLI command, persists only `verification_status`, `unsupported_phrases`, and `verifier_reason`, and terminates. It never invokes Stage 10, applies the advisory `suggested_rewrite`, rewrites or drops a bullet, or creates a gap question. Revised bullet wording requires an explicit Stage 10 generation, which supersedes the prior current branch generation.

If verification or re-verification changes any current bullet verification field, every managed resume export for that branch is removed or reported as a residual-path failure before the new finding set is reported current. A verifier result may not leave an older exported file current-looking against changed bullet verdicts.

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

Export accepts only a current snapshot or branch whose complete current provenance chain resolves under §12 rule 10 and §16.1 and whose status-bearing inputs pass the applicable §16.11 allowlist. Assessment export validates the snapshot aggregate, every referenced claim, and the exact-one matching `narrative_summary` invariant; resume export validates the branch's exact snapshot anchor, every selected self-claim, and every bullet. Unexpected missing, inconsistent, or status-ineligible inputs fail closed. Owner deletion does not leave a partial database graph for export to skip: §13.13 purges all derived generations, attempts verified managed-output removal, and reports residual paths as incomplete before rebuilding. Export remains unavailable until recomputation and any requested resume regeneration succeed.

Assessment export also validates the project scope target and the typed unknown-gap references — resolvable, duplicate-free, current rows — before §17 rendering; a gap answered after synthesis renders as answered-since-synthesis context under §17 and never fails export. Resume verification and export recover the Stage 10 job description through `ResumeBranch.job_description_id` and resolve every bullet's `matched_jd_requirements` against that `ParsedJD`. Neither consumer may substitute free-form unknown prose or requirement labels when a typed reference or branch association is absent.

## §13.13 Derived Lifecycle and Recompute

Trigger: lifecycle recomputation in §14.12; correction and owner-deletion operations in §14.4 and §14.11 invoke the same service flow.

This subsection orchestrates existing stages and is not a pipeline stage. Each invoked stage creates its own `processing_runs` row under its stable §13 identifier.

Rules:

1. Selected-lineage recomputation under §14.12 replaces Stage 3 facts for that correction lineage, then regenerates the complete current Stage 4–7 graph from all current facts. Full recomputation under §14.12 replaces facts for every lineage before the same global Stage 4–7 rebuild.
2. A recompute validates every stage's complete candidate output, including §12 rule 10, before the business-state swap. A successful swap leaves at most one current generation per lineage/scope and marks the replaced generation `superseded_at`; payloads are never updated in place.
3. Where an active stage explicitly requires replacement, that stage rule controls — including complete replacements in Stages 4–6 and replacement of an existing named branch in Stage 10. For other standalone reruns whose inputs have not changed, the stage may retain the prior current generation or replace it. No rerun may expose duplicate current facts, signals, claims, snapshots, gaps, or contradictions. If validation fails before a source change, the prior current generation remains current and no partial candidate output is inserted.
4. Correction capture and invalidation are one atomic database visibility boundary before rebuilding starts: the transaction inserts the new raw/evidence records, supersedes current facts for that correction lineage, and supersedes every current gap, contradiction, signal, claim, snapshot, resume branch, and resume bullet. Managed exports are enumerated and removal is attempted as part of the same operation; residual paths are reported as an unsuccessful invalidation rather than silently retained. A crash or recompute failure can therefore leave the correction plus no replacement current graph, but can never leave the pre-correction graph current against the changed source set. The correction remains stored and §14.12 is the retry surface.
5. Owner deletion is a privacy-first global reset. The service first enumerates and attempts to remove every managed `out/` artifact, then atomically purges every current and historical fact, fact source, gap, contradiction, signal, claim, snapshot, resume branch, and resume bullet while hard-deleting the selected `RawLog` and cascading its evidence items. It then attempts a full recompute from every surviving lineage. Job descriptions and `processing_runs` telemetry remain. Surviving `gap_answer` raw logs stay interpretable through their §14.7 self-containment; the rebuild never re-links them to regenerated questions.
6. Database deletion commits even if managed-output removal or rebuilding fails. The command verifies the managed paths before reporting success: any residual path makes the result `deletion_incomplete`, is reported explicitly for manual removal, and is never treated as a retained evidence source. Rebuild failure reports a separate unsuccessful result with no derived database model. Neither failure restores the raw row or purged derived rows; the user may remove reported files and retry recomputation through §14.12. No FK, filesystem error, or failed processing run may restore or block database deletion.
7. The reset is deliberately global in V1. Selective graph deletion and warn-and-skip are rejected because JSON and implicit dependencies cannot prove that all private derived text was found, and a partial truth model could be mistaken for a complete one.
8. Deletion covers only Exp2Res-managed database records and `out/`. Supplied source files and copies of prior exports outside the managed workspace remain user-controlled; §14.11 reports their known paths but does not delete them.

---
