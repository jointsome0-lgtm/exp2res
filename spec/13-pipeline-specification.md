## §13. Pipeline Specification

A pipeline stage exists only when it has at least one CLI trigger in §14 and produces a downstream-referenceable persisted business artifact or persisted state transition. `processing_runs` telemetry alone does not qualify an operation as a stage.

§14 is the sole canonical home of command forms. Each stage below points to its §14 trigger instead of restating shell syntax.

Unless a stage explicitly says otherwise, its inputs and outputs are current rows (`superseded_at IS NULL`) under §11–§12. Historical rows are inspectable but never implicit processing inputs.

Every stage validates its typed output references under §12 rule 10 before committing business rows. Missing, wrong-type, superseded, or duplicate targets fail the producing run atomically; JSON representation is not an integrity exception.

Every atomic business replacement follows §12 rule 13's one-swap/one-`generation_id` allocation: Stage 3 partitions by correction lineage, while each Stage 4, Stage 5, Stage 6 view, or Stage 10 branch swap has its own shared generation.

Every persisted recomputable business row and completed verifier finding resolves to the stage run that produced it through §12 rule 13 or §11.14, and a failed run owns no business rows or finding rows (§12.13).

Whenever a status-bearing row is offered to resume generation or either export, the consumer applies the canonical `VerificationStatus` allowlists in §16.11. No consumer may replace those allowlists with a denylist or treat an unnamed status as passing.

Every §13 business-state mutation and all coupled managed-output work, including enumeration, writes, removal, and residual-path handling, run while the §8.1 workspace writer lock is held.

Whenever any transition supersedes a current `AssessmentSnapshot`, `ResumeBranch`, or `ResumeBullet`, it also enumerates and attempts to remove all dependent managed assessment/resume artifacts under `out/`. Database invalidation remains committed if cleanup fails; every residual path is reported as an unsuccessful invalidation and no command may report the stale files as current output. `gaps answer` triggers the same enumeration, removal attempt, and residual-path reporting for the managed exports its answered state makes stale, without superseding any row (§14.7).

## §13.1 Stage 1 — Raw Capture and Evidence Recording

Triggers: capture and import commands in §14.2–§14.5; gap-answer capture in §14.7.

Inputs:

```text
manual daily logs
manual retrospective logs
gap answers
corrections
activity-domain evidence records (§19.1)
Atlas knowledge-state snapshots (§19.2)
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
5. Each accepted source record is persisted atomically as one `RawLog` plus its linked `EvidenceItem` records before the command returns; a §19.4 batch persists every accepted pair in one §8.1 writer transaction or persists none.
6. A manual daily log, retrospective log, gap answer, or correction receives its linked `EvidenceItem(strength=manual_claim)` when the `RawLog` is persisted; there is no later normalization stage.
7. Import commands create linked evidence items under §14.5; §19 defines the integration payload contracts.
8. `commit_or_pr` is used for an imported VCS commit. Source or build evidence not represented by a commit has no V1 importer; a future importer must reintroduce its strength value together with its producing flow.
9. Capture and evidence recording do not create self-claims or interpret any input as a strong fact.
10. Automation may not update or delete a retained `RawLog` or its linked `EvidenceItem`s. A correction is an appended, self-contained `RawLog` whose validated `corrects_log_id` identifies its target; §14.4 then invokes §13.13.

Owner deletion is triggered only by §14.11 and follows §13.13. It is a raw-layer lifecycle operation, not another capture stage.

## §13.3 Stage 3 — Experience Fact Extraction

Triggers: fact extraction in §14.6; lifecycle recomputation in §14.12.

Input:

```text
raw_logs + evidence_items + displaced_support_items
```

Persisted outputs:

```text
experience_facts
fact_sources
```

Rules:

1. Extract atomic claims.
2. Preserve temporal provenance under §15.2 and §16.7. A fact copies its governing record's `OccurredAt` by default. A narrower contained placement is legal only when selected evidence present in the extraction context explicitly supports it; extraction may never extend its governing record's source window, raise temporal precision above that explicit support, or raise `TemporalConfidence` above its governing record's placement.
3. Preserve ownership level under §16.4.
4. Do not infer metrics unless evidence explicitly contains metrics.
5. Do not infer production use unless explicitly present.
6. Every fact selects at least one supplied evidence item, and at least one selected item is linked to an effective record: a displaced-record support descriptor is additional scoped support, never a fact's only selection, and a candidate fact whose selections are all `displaced_support_items` fails Stage 3 commit atomically — its content would trace to no effective record and its provenance closure would hydrate no record that states it. Every selected item belongs to the correction lineage being extracted and is selectable under rule 10: it is either linked to an effective record and supplied in `evidence_items`, or a non-`manual_claim` item linked to a displaced record and supplied in `displaced_support_items`. Only selected items receive §12.4 rows. Stage 3 persists each selected item as `direct`. V1 has no separate corroboration producer, so it never infers a `corroborating` row silently.
7. `ExperienceFact.evidence_item_ids` is exactly the duplicate-free selected-item set, and `source_log_ids` is exactly the duplicate-free set of `EvidenceItem.raw_log_id` values reached through it. Every listed raw log therefore contributes at least one selected evidence item. Multiple selected items from one raw log produce separate `fact_sources` rows.
8. `ExperienceFact.confidence` must follow §9.4's evidential scopes, independence rules, and deterministic ceiling for the complete linked `EvidenceItem` set; confidence and evidence strength remain separate axes (§9.3).
9. `ExperienceFact.claim_kind` follows the fact-extractor producer semantics in §15.2; §13 does not restate that contract.
10. The extraction unit is one retained correction lineage: a root `RawLog` plus every retained correction that reaches it through `corrects_log_id`. The root appears first; corrections follow in ascending `recorded_at` order, then ID ascending by byte order. A correction is recorded after its target, which §14.4 requires to exist at capture, so this order is consistent with correction edges. If owner deletion nulls a correction's target, that correction becomes a new lineage root and the same rules apply.

    A retained correction displaces exactly the record named by its non-`NULL` `corrects_log_id`, as a whole record, and no other record. Formally, for a retained lineage member `x`, `displaced(x)` holds if and only if some retained lineage member `y` has `y.corrects_log_id = x.id`. The effective records are exactly the non-displaced lineage members. Thus `C2 → C1 → R` leaves only `C2` effective, while sibling corrections `C1 → R` and `C2 → R` leave both `C1` and `C2` effective in lineage order. An owner-deletion orphan roots its own lineage and, unless another retained correction targets it, is effective.

    Whole-record displacement removes the target's interpretation from current content. A displaced record's `raw_text` and, for a gap answer, its copied `question_text` / `question_reason` context are never Stage 3 or Stage 4 input and may never source a current fact or detection target. A displaced record's linked `manual_claim` items are displaced with it: their §9.4 evidential scope is exactly the owner statement whose interpretation the correction replaced, so they are not extractor input and no current fact may select them. Its linked non-`manual_claim` items remain selectable only as **displaced-record support**: their §9.4 scopes may still establish existence, recorded activity, attribution, or artifact content, but they are never a current-content channel.

    The prose-free **displaced-record support descriptor** is the universal §15 serialization of an `EvidenceItem` linked to a displaced record. Wherever any §15 contract input serializes such an item, it serializes exactly `id`, `raw_log_id`, `strength`, `uri`, and `path`; it never serializes `title`, `summary`, `created_at`, or `metadata`. The exclusion follows field class: `title` and `summary` are the item's unconstrained free-text fields — §11.3 does not restrict `title` to a non-content label, so an imported item's title can carry source-derived prose such as a commit subject or artifact name — while `uri` and `path` are structural locators whose transit §29.3 discloses and §29.4 re-checks immediately before serialization. A displaced `RawLog` is never serialized as an object into any prompt: serialized evidence context carries its identity only as the descriptor's `raw_log_id` reference, while ordinary typed provenance ID fields may repeat that ID without hydrating the object. The descriptor is a call-time projection of retained rows, not a persisted resolution artifact. This projection changes representation only; it never admits a record or item that the receiving contract otherwise excludes. **Effective lineage evidence** means the effective records and every `EvidenceItem` linked to those records.

    The **governing record** for rule 2 source placement, rule 13 project provenance, and §15.2 is per fact: the last, in that same lineage order — equivalently, the latest by (`recorded_at`, then ID) — of the effective records the fact lists in `source_log_ids`. Rule 6 guarantees at least one selected effective record, so every fact's governing record is defined; when the root is the fact's only selected effective record, the root governs. Governing selects placement and project only; it does not displace another effective sibling, and an effective sibling governs only the facts that select it, so sibling corrections with different placements or projects never leak provenance onto each other's facts. The fact inherits its governing record's placement unless the evidence-backed narrowing permitted by rule 2 applies. Before any Stage 3 or Stage 4 call, the service computes displacement, effective records, and displaced-record support directly from retained rows; each candidate fact's governing record then follows deterministically from its validated `source_log_ids` at commit, and §15.2 binds the model output to it. Resolution uses no LLM call, persisted resolution artifact, or ambiguity state: a retained `corrects_log_id` resolves or is `NULL`, cycles are rejected at capture (§11.2), and owner deletion re-roots through §11.2.
11. Extraction computes the complete current fact generation for each selected lineage. A validated replacement and the `superseded_at` transition of the lineage's previous current facts commit atomically; it never appends a second current copy. Repeating extraction may add processing history or a superseded generation, but after success there is exactly one current fact generation for that lineage.
12. If a replacement changes current facts, every current gap, contradiction, signal, claim, snapshot, resume branch, and bullet is invalidated before it can be reused. §14.12 regenerates Stages 4–5; assessment views and resume branches are explicitly parameterized projections regenerated only through §14.9/§14.10 against the new current state, with every invalidated view reported under §13.13.
13. `ExperienceFact.project` is copied provenance, exactly like the default `occurred` placement: it equals its governing record's `project` value under rule 10 — including `None` — and the extractor may not author, rename, re-case, or drop it. §13.6 canonicalizes only at comparison time.

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

Triggers: detection generation in §14.7; lifecycle recomputation in §14.12.

Stage 4 is LLM-backed because detecting semantic conflicts and missing support cannot be reduced to the structural rules below. Its complete structured boundary is §15.8.

Inputs:

```text
all current experience_facts
effective lineage evidence under §13.3 rule 10: per retained correction lineage,
every effective raw log and every EvidenceItem linked to one,
including effective records whose evidence produced no fact
```

The service computes this effective lineage evidence under §13.3 rule 10 before the detector call. A displaced record, its linked evidence items, and its displaced content are neither detector input nor detector targets; Stage 3 `displaced_support_items` descriptors are not supplied to Stage 4. Feeding them back would regenerate current detections from exactly what correction displacement removed from the current fact generation. The displaced records stay retained for history, §16.12 source-segment validation, and owner deletion — they are simply not part of this input.

Persisted outputs:

```text
gap_questions
contradictions
```

Gap triggers are the `GapTrigger` values (§10); each generated gap question records its trigger as `GapQuestion.reason` (§11.10).

Each successful run validates one complete candidate for both output sets. Retention is legal only when that candidate is content-equivalent to the current generation — equal as sets over the detector-authored fields of both outputs (gap: `target_type`, `target_id`, `question`, `reason`, `priority`; contradiction: `title`, `description`, and its two `(ref_type, ref_id)` references compared as an unordered pair, because the detector has no canonical side ordering and a swapped left/right rerun is the same conflict) — and every gap in the current generation has `answered = false`. When both conditions hold, the prior current generation is retained, Stage 4 supersedes nothing, and the run records only telemetry; a direct §14.7 invocation then invalidates no upper layer or managed export, while inside the §14.12 flow the downstream stages still follow §13.13. `answered` and `answer_log_id` are service lifecycle state, not detector content, and never enter the comparison. A current generation containing any gap with `answered = true` is always replaced even when the detector-authored fields are equivalent: the detector derived its candidate from inputs that include the stored answer evidence, so a re-emitted gap is genuinely still open (§14.7); each replacement gap starts with `answered = false` and `answer_log_id = None`, and no question-to-answer link is re-created. Otherwise the run replaces both complete sets together, and the prior current generation becomes superseded in the same transaction; inputs or output references to superseded rows are invalid. One Stage 4 run creates one §13.4 `processing_runs` row whether it retains or replaces the generation.

Stage 4 alone owns the complete current contradiction set. If its current inputs still conflict, the replacement generation must retain a contradiction for that conflict; if evidence-driven inputs no longer conflict, the replacement may omit it. V1 has no direct resolve/dismiss transition on a derived contradiction row.

A changed Stage 4 generation atomically supersedes every current signal, claim, snapshot, resume branch, and resume bullet before those rows can be reused. Regenerating those higher layers requires their §14 triggers; the shared §14.12 flow regenerates Stage 5, while assessment views and resume branches require §14.9/§14.10 (§13.13).

The V1 Stage 4 producer may persist only gaps and contradictions whose polymorphic targets are effective-lineage Stage 1 evidence present in this input or current Stage 3 facts. Effective evidence that produced no fact remains visible and may receive a gap target; a displaced record or any item linked to it can be neither input nor target. Stage 4 rejects targets owned by Stage 5 or later, because the same replacement invalidates those upper generations.

The validated §15.8 result is the complete candidate set for both outputs, never a patch over prior detections. The service assigns entity IDs and lifecycle fields and initializes each new gap with `answered = false` and `answer_log_id = None`. Detector output has no verification status, resolution, dismissal, or resolution-note field. A schema-valid semantic detection set is not a verdict: §15.1 retries only schema or reference invalidity and never retries merely because a conflict or gap was included or omitted.

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

A candidate `SelfSignal.confidence` must satisfy §9.4's propagation caps; a candidate above its computed cap is invalid structured output.

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

Trigger: self-assessment generation in §14.9. Lifecycle recomputation ends at Stage 5 (§13.13); it invalidates views but never regenerates them.

Input:

```text
self-assessment scope and scope target from §14.9
self_signals
experience_facts
contradictions
gap questions
```

Input selection is structural and service-owned; the writer can neither broaden nor narrow it. `global` selects every current fact as subject and every current signal. `project` selects as subject exactly the current facts whose `project`, canonicalized and case-folded like the target (§14.9's NFC + trim, locale-independent case fold), equals the case-folded canonical `scope_target`; a fact with `project = None` is never a subject fact. It then selects every current signal whose `supporting_fact_ids` or `counter_fact_ids` reference at least one subject fact, and supplies the out-of-subject facts those signals reference as §15.4 `context_facts`, so cross-target support and counterevidence stay visible without widening the subject. A project view whose subject set is empty fails the Stage 6 run before any provider call; there is no empty mirror. The complete current unanswered gap set and the complete current contradiction set are never scope-filtered. Every claim's `source_fact_ids` and `source_signal_ids` must name only objects supplied to this §15.4 call; out-of-context provenance is invalid structured output.

The Stage 6 gap input is the complete current unanswered (`answered = false`) set. Answered current rows remain valid §14.7 state until regeneration but are not unknowns and are not writer inputs.

Persisted outputs:

```text
self_claims
assessment_snapshots
```

Assessment dimensions are the `SelfClaimDimension` values (§10), carried by `SelfClaim.dimension` (§11.6). §13 must not restate them.

`SelfClaim.claim_kind` follows the self-assessment-writer producer semantics in §15.4; §13 does not restate that contract.

At the Stage 6 boundary, each candidate `SelfClaim.confidence` must satisfy §9.4's propagation caps; a candidate above its computed cap is invalid structured output.

Synthesis atomically creates a complete current claim generation and a new current snapshot from one coherent current input generation, then supersedes the prior current snapshot for the same assessment view — (`scope`) for `global`, (`scope`, case-folded canonical `scope_target`) for `project` (§11.7) — and the claims owned by that snapshot. The transaction validates the reverse cardinality in §12: after the swap, every current `SelfClaim` appears in exactly one current `AssessmentSnapshot.self_claim_ids`, current snapshots share no claim rows, and no current claim is unowned. Every other view — the other scope and other project targets — remains current. A superseded snapshot's payload and provenance remain inspectable history after correction but cannot become a processing input.

Every new claim and snapshot starts with `verification_status = "unverified"`. Stage 6 may not pre-authorize its own output; Stage 7 alone assigns semantic claim verdicts and derives the current snapshot status under §16.11.

Replacing an assessment view also supersedes every current resume branch and bullet based on that view's prior snapshot before they can be reused.

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

For a project-scoped run, Stage 6 copies the canonical non-blank §14.9 `--project` value into `AssessmentSnapshot.scope_target`; the writer receives it as branch-free structural context but cannot rewrite it. For `global`, Stage 6 persists `scope_target = None`.

Known-gap assertions are emitted as ordinary `SelfClaim(dimension="gap")` rows and receive Stage 7 status. The §15.4 `unknowns` output contains references only and must enumerate every current unanswered Stage 4 `GapQuestion` exactly once; Stage 6 does not scope-filter that set and stores it unchanged in `AssessmentSnapshot.gap_question_ids`. Unknowns are uncertainty/question presentation, not claim-grade assertions: they do not receive an independent status, do not improve the §16.11 snapshot aggregate, and cannot guide Stage 10. Declarative prose about what is or is not true must be a snapshot-owned `SelfClaim` and pass the existing status gate.

Every current Stage 4 contradiction appears exactly once in `AssessmentSnapshot.contradiction_ids`; Stage 6 does not scope-filter conflicts. The writer cannot suppress one as resolved or dismissed; duplicate or stale IDs fail under §12 rule 10.

The writer emits exactly one `SelfClaim(claim_kind="narrative_summary")` whose `claim` equals `AssessmentSnapshot.summary`, and the snapshot includes that claim ID. The summary receives an ordinary Stage 7 verdict and participates in §16.11 aggregation; Stage 6 cannot place separately unverified prose in the snapshot summary.

## §13.7 Stage 7 — Assessment Verification

Trigger: assessment verification in §14.9.

Persisted outputs:

```text
self_claims verification_status and counterevidence
assessment_snapshots verification_status
verification_findings
```

Verifier checks:

1. Every self-claim has sources.
2. Each `SelfClaim.confidence` is justified under §9.4's judgment frame by the strength and scope of its supporting facts' linked evidence; confidence and evidence strength remain separate axes (§9.3).
3. Counterevidence is not hidden — inside the closure or by omission: a contrary `scope_signals` or `scope_facts` member absent from the claim's account grounds a non-passing status and may persist as a typed counterevidence reference to that member.
4. Identity claims are not over-broad.
5. Self-assessment does not become motivational fiction.
6. No clinical/diagnostic claims are generated.
7. No resume-style overclaiming leaks into mirror mode.
8. Every verified claim and snapshot is current and all of its referenced current entities resolve; superseded historical snapshots are inspect-only.
9. The snapshot preserves the complete current Stage 4 contradiction set; verification cannot hide one by scope filtering, relabeling, or omission.
10. Exactly one member claim has `claim_kind = "narrative_summary"`, and its claim text equals `AssessmentSnapshot.summary`.
11. Each claim stays within the snapshot's scope and scope target supplied as §15.5 structural context; a scoped claim that generalizes beyond its subject receives a non-passing status.

For each claim, Stage 7 assembles exactly the §15.5 input closure from current rows, plus the view context — `scope_signals` and `scope_facts`, the snapshot view's complete deterministic §13.6 selection re-derived from current rows — so writer omission of a contrary signal or fact stays visible to check 3 while only the closure deepens into evidence context. The service serializes that closure through §13.3 rule 10's displaced-record support descriptor projection. A required post-projection bundle member that is missing, wrong-type, superseded, duplicated, or otherwise unresolvable fails the Stage 7 run closed before any provider call, and the prior complete verifier state is retained. The exactness and no-narrowing/no-widening rule applies to this displacement-aware bundle: omitting a descriptor, cited signal's counter fact, or other required member would obtain a more permissive verdict from a narrower graph; serializing a complete displaced item, a displaced `RawLog` object, or any row outside the bundle would widen the declared §29.3 transmission surface. The projection-required absence of a displaced `RawLog` object is not a missing member.

Stage 7 obtains a validated §15.5 verdict for every claim in the current snapshot. A completed pass commits all claim statuses and counterevidence, the snapshot status computed by §16.11, and one immutable §11.14 `VerificationFinding` per claim in the same transaction; the derived snapshot aggregate receives no finding row. If any finding remains invalid or missing after §15.1, no claim or snapshot verification update and no finding row commits; an initially generated snapshot therefore remains `unverified`, while a failed re-verification against unchanged inputs retains the prior complete verifier state and prior finding history. The failed `processing_runs` row with its `failure_code` is the durable record of that attempt. The snapshot status is never an independent optimistic label.

One Stage 7 invocation performs one semantic verifier pass per current claim and then terminates after aggregation. A valid non-passing verdict completes verification but closes every consumer gate that disallows its status. Stage 7 returns the complete §15.5 findings to the invoking CLI command, persists the denormalized operational fields plus the complete append-only finding history, and never invokes Stage 6, applies `suggested_rewrite`, edits or drops claim prose, or creates a gap question. The advisory rewrite is persisted only inside its finding and never re-enters a prompt or export; revised claim wording can appear only in a later explicit Stage 6 replacement generation.

If verification or re-verification changes a current claim's status or counterevidence or the current snapshot status, the snapshot's managed view directory under `out/assessment/` (§13.12) is removed or reported as a residual-path failure. Every branch and bullet based on that snapshot is also superseded and dependent managed-resume removal is attempted under the global rule above; a verifier result may not leave a resume current against a changed verifier state.

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
linked evidence context serialized under §13.3 rule 10
```

Persisted outputs:

```text
resume_branches
resume_bullets
```

Generation selects and ranks relevant facts and `supported` self-claims for the supplied job description. Matching is internal to this stage, must not invent relevance, and is persisted on each bullet through `ResumeBullet.target_role_relevance` and `ResumeBullet.matched_jd_requirements`; there is no separate match artifact or stage.

Every `matched_jd_requirements` entry is a duplicate-free stable ID from the exact `ParsedJD.requirements` supplied to this Stage 10 run. The writer cannot emit a free-form requirement label or an ID from another job description. §12 rule 10 and the Stage 10 transaction reject every missing, duplicate, or wrong-job reference before a branch or bullet becomes current.

The Stage 10 producer must copy the exact §14.10 `--jd` ID into its candidate `ResumeBranch.job_description_id`. Stage 11 and Stage 12 recover the typed `ParsedJD` through that persisted association; a Stage 10 candidate with a missing or different job-description ID fails atomically.

The exact assessment snapshot selected under §14.10 is mandatory, must be current, and must be eligible to anchor Stage 10 under §16.11. The new `ResumeBranch.assessment_snapshot_id` equals that selected ID. There is no implicit latest snapshot and no unanchored generation. The snapshot supplies structural anchor, scope, membership, and status context; its title and summary prose are not independent writer inputs. If the matching narrative summary guides selection or wording, Stage 10 passes its `supported` member claim and the bullet lists that claim ID. Only supported member claims may guide generation, and §12 validates every bullet's source-claim membership before commit.

For each bullet, `source_self_claim_ids` is the duplicate-free exact set of self-claims that guided its selection or wording and is empty iff no self-claim did. The writer may not consume an unlisted claim or list a claim it did not use.

Stage 10 calls the §15.6 writer once per planned bullet in an isolated model context. Each invocation contains only facts selected for that bullet, their linked evidence context serialized under §13.3 rule 10, the `supported` snapshot-member self-claims selected for that bullet, explicit branch context, and the typed selected job description. The required descriptor substitution and displaced-`RawLog` omission preserve provenance without supplying displaced prose. Stage 10 validates every result and commits the complete branch/bullet batch atomically; one invocation can never read another bullet's facts or claims.

The supplied facts and eligible self-claims must all be current. A replacement fact or assessment generation supersedes every dependent current resume branch and bullet and attempts dependent managed-export removal under the global rule above; resume generation must be run again rather than silently carrying old selections forward. Generating a branch name that folds equal to a current branch's under §14.10's NFC case-folded replacement identity atomically supersedes that branch's prior current row and bullets — the Stage 10 transaction enforces the folded-identity match under the workspace writer lock — so at most one generation of the named branch is current and no two current branches fold equal.

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
verification_findings
```

The verifier inspects phrases, not only whole bullets.

Stage 11 owns the semantic transition from each current bullet's initial `unverified` status to one §16.11 verdict. It validates one §15.7 finding for every current bullet and, for a completed pass, commits the complete branch finding set as one immutable §11.14 row per bullet in the same transaction as the denormalized bullet updates. If any finding remains invalid or missing after §15.1, no bullet verification update and no finding row commits; a new branch remains `unverified`, while a failed re-verification against unchanged inputs retains the prior complete verifier state and prior finding history. The failed `processing_runs` row with its `failure_code` is the durable record of that attempt. A branch remains ineligible for resume export unless every bullet satisfies the resume-export allowlist.

One Stage 11 invocation performs one semantic verifier pass per current bullet, returns the complete findings to the invoking CLI command, persists the denormalized `verification_status`, `unsupported_phrases`, and `verifier_reason` plus the complete append-only finding history, and terminates. It never invokes Stage 10, applies the advisory `suggested_rewrite`, rewrites or drops a bullet, or creates a gap question. The advisory rewrite is persisted only inside its finding and never re-enters a prompt or export; revised bullet wording requires an explicit Stage 10 generation, which supersedes the prior current branch generation.

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
out/assessment/<view>/self_assessment.md
out/assessment/<view>/self_claims.json
out/assessment/<view>/evidence_map.json
out/assessment/<view>/gap_questions.md
out/assessment/<view>/contradictions.md
```

`<view>` is the deterministic slug of the exported snapshot's assessment view (§11.7): `global` for the global scope, and `project--<target>` for a project view, where `<target>` is the case-folded canonical `scope_target` with every UTF-8 byte outside `a-z 0-9 . _ -` percent-encoded as `%XX` with uppercase hex digits, and a leading or trailing `.` of the resulting segment additionally encoded as `%2E` — no view directory is a dot segment, hidden name, or Windows-invalid trailing-dot name, and targets such as `Exp2Res` and `Exp2Res.` keep distinct directories. Case folding matches view identity, so two views never collide on a case-insensitive filesystem, and the encoding is injective, so distinct targets never share a directory. Each snapshot's exports live only in its view directory; exporting one view never touches another's files.

Persisted resume outputs:

```text
out/<branch>/resume.md
out/<branch>/evidence_map.json
out/<branch>/verification_report.md
out/<branch>/gap_questions.md
```

Export accepts only a current snapshot or branch whose complete current provenance chain resolves under §12 rule 10 and §16.1 and whose status-bearing inputs pass the applicable §16.11 allowlist. Assessment export validates the snapshot aggregate, every referenced claim, and the exact-one matching `narrative_summary` invariant; resume export validates the branch's exact snapshot anchor, every selected self-claim, and every bullet. Unexpected missing, inconsistent, or status-ineligible inputs fail closed. Raw-log owner deletion does not leave a partial database graph for export to skip: §13.13 purges all derived generations, attempts verified managed-output removal, and reports residual paths as incomplete before rebuilding. Export remains unavailable until recomputation and the explicit §14.9/§14.10 view and branch regenerations succeed.

Assessment export also validates the project scope target and the typed unknown-gap references — resolvable, duplicate-free, current rows — before §17 rendering; a gap answered after synthesis renders as answered-since-synthesis context under §17 and never fails export. Resume verification and export recover the Stage 10 job description through `ResumeBranch.job_description_id` and resolve every bullet's `matched_jd_requirements` against that `ParsedJD`. Neither consumer may substitute free-form unknown prose or requirement labels when a typed reference or branch association is absent.

## §13.13 Derived Lifecycle and Recompute

Trigger: lifecycle recomputation in §14.12; correction and raw-log owner-deletion operations in §14.4 and §14.11 and job-description deletion in §14.15 invoke the same service flow.

This subsection orchestrates existing stages and is not a pipeline stage. Each correction (§14.4), raw-log owner deletion (§14.11), job-description deletion (§14.15), or recompute (§14.12) lifecycle flow creates one orchestration `processing_runs` row with `stage = "13.13"`; every stage run it invokes creates its own row under that stage's stable §13 identifier and links to the orchestration row through `parent_run_id`. A direct single-stage command leaves `parent_run_id = NULL`. Identifier `13.13` is legal for this telemetry row only, and telemetry alone still does not make the operation a pipeline stage.

Rules:

1. Selected-lineage recomputation under §14.12 replaces Stage 3 facts for that correction lineage, then regenerates the complete current Stage 4–5 graph from all current facts. Full recomputation under §14.12 replaces facts for every lineage before the same global Stage 4–5 rebuild. Stage 4 inside this flow follows its §13.4 retain-or-replace rule; a retained detection generation does not halt the flow's Stage 5 regeneration. Lifecycle recomputation ends at Stage 5: Stages 3–5 are parameterless shared derivations, while Stage 6–7 assessment views and Stage 10–11 resume branches are explicitly parameterized projections that only their §14.9/§14.10 commands regenerate.
2. A recompute validates every stage's complete candidate output, including §12 rule 10, before the business-state swap. A successful swap leaves at most one current generation per lineage, assessment view, or named branch and marks the replaced generation `superseded_at`; payloads are never updated in place.
3. Where an active stage explicitly requires replacement, that stage rule controls — including Stage 4's retain-or-replace equivalence rule and complete replacements in Stages 5–6 and replacement of an existing named branch in Stage 10. For other standalone reruns whose inputs have not changed, the stage may retain the prior current generation or replace it. No rerun may expose duplicate current facts, signals, claims, snapshots, gaps, or contradictions. If validation fails before a source change, the prior current generation remains current and no partial candidate output is inserted.
4. Correction capture and invalidation are one atomic database visibility boundary before rebuilding starts: the transaction inserts the new raw/evidence records, supersedes current facts for that correction lineage, and supersedes every current gap, contradiction, signal, claim, snapshot, resume branch, and resume bullet. Managed exports are enumerated and removal is attempted as part of the same operation; residual paths are reported as an unsuccessful invalidation rather than silently retained. A crash or recompute failure can therefore leave the correction plus no replacement current graph, but can never leave the pre-correction graph current against the changed source set. The correction remains stored and §14.12 is the retry surface.
5. Raw-log owner deletion is a privacy-first global reset. The service first enumerates and attempts to remove every managed `out/` artifact and every managed §12.14 backup, with any residual backup path governed by rule 6 exactly like a residual export path, then atomically purges every current and historical fact, fact source, gap, contradiction, signal, claim, snapshot, resume branch, resume bullet, and verification finding while hard-deleting the selected `RawLog` and cascading its evidence items. Findings belong to the derived purge because their reasons, quoted unsupported phrases, advisory rewrites, and counterevidence statements are generated prose. Job descriptions and `processing_runs`/`llm_calls` telemetry remain subject to content-hash redaction: the same purge transaction sets `input_hash` and `output_hash` to `NULL` on every retained `llm_calls` row, not only rows provably tied to the deleted record. A deterministic hash of guessable purged content would otherwise remain an oracle that confirms deleted text by hashing candidates, and a selective closure that misses one content path fails open. Retained telemetry keeps identifiers, timing, statuses, token counts, accounting values, retry counts, stable failure codes, and `prompt_policy_hash`, none derived from owner content bytes; `prompt_policy_hash` hashes fixed contract instructions plus the structured-output schema revision only. Opaque IDs may stop resolving. The service then attempts a full Stage 3–5 recompute from every surviving lineage through fresh runs whose call rows record fresh hashes over surviving content only; redaction applies to every call row committed before the purge transaction. Surviving `gap_answer` raw logs stay interpretable through their §14.7 self-containment; the rebuild never re-links them to regenerated questions.
6. Database deletion commits even if managed-path removal or rebuilding fails. Before removing any managed path, the service applies POSIX canonicalization and workspace-containment checks equivalent to §29.4's path boundary but never treats a path as source input: it never follows a symlink during deletion, and any symlink entry or path that resolves outside the canonical workspace is left untouched and reported as residual for manual removal. The command verifies the managed paths before reporting success: any residual path makes the result `deletion_incomplete`, is reported explicitly, and is never treated as a retained evidence source. Rebuild failure reports a separate unsuccessful result with no derived database model. Neither failure restores the deleted owner record or purged derived rows; the user may remove reported files and, for raw-log deletion, retry recomputation through §14.12. No FK, filesystem error, or failed processing run may restore or block database deletion.
7. The raw-log reset is deliberately global in V1. Selective graph deletion and warn-and-skip are rejected because JSON and implicit dependencies cannot prove that all private derived text was found, and a partial truth model could be mistaken for a complete one.
8. Raw-log deletion covers only Exp2Res-managed database records, `out/`, and managed §12.14 migration backups. Supplied source files and copies of prior exports outside the managed workspace remain user-controlled; §14.11 reports their known paths but does not delete them.
9. Invalidated-view reporting: except for rule 10 job-description deletion, whose §14.15 purge report has no regeneration command against the deleted JD, every transaction that supersedes or purges current snapshots and branches — inside this flow or in a direct §14.6/§14.7/§14.8 generation — captures each affected assessment view — scope, scope target, snapshot ID — and, for each affected branch, its name, retained job-description ID, and anchoring view. The invoking command reports every invalidated view with its executable §14.9 regeneration command, and every invalidated branch with that captured context plus the §14.10 command shape — a branch command cannot be executable as printed, because §14.10 requires a current `--snapshot` that exists only after its view is regenerated. Every printed command quotes each argument value with POSIX single-quote shell quoting (an embedded single quote becomes `'\''`), so a target or branch name containing whitespace or shell metacharacters stays copy-paste-safe and selects the exact stored value. After raw-log owner deletion this report is command output only, never persisted derived state. A bare `recompute` retried after a crash rebuilds Stages 3–5 and reports that no current assessment view exists, pointing at §14.9; it never infers a desired view set from historical or purged rows.
10. Job-description deletion is a privacy-first dependent purge, never an FK-blocked request. Before its database transaction, the service captures the selected `JobDescription` inspection projection and every current or historical `ResumeBranch` whose `job_description_id` names it, including each branch ID and name; it deduplicates and attempts removal of every managed §12.14 migration backup and of the managed `out/<branch>/` directories those branches own, because either may retain the deleted vacancy or generated prose. Directory ownership is decided by actual canonical path, not by folded name alone: for each captured branch, the managed `out/<branch>/` path derived from its stored spelling is removed unless a current branch outside the captured set owns that same canonical directory — its stored name is byte-equal, or the two spellings resolve to one canonical path on a case-insensitive volume. A name reused by a later `resume generate` against a different job description therefore keeps that other branch's current export untouched, while a fold-equal current branch with a different spelling on a case-sensitive volume owns a different directory and spares nothing: the captured branch's own stale directory is still removed. A captured directory with no surviving current owner is stale managed output and is removed. Residuals follow rule 6. In one transaction it deletes every `VerificationFinding` targeting a bullet of those branches, every bullet of those branches, every captured branch, and then the selected `JobDescription`; current assessment views and all current or historical snapshots, claims, and their findings remain untouched because they do not depend on a job description. The same transaction applies rule 5's global content-hash redaction to every retained `llm_calls` row committed before it; `processing_runs` and `llm_calls` otherwise survive under their content-free §12.13/§12.15 field limits. No recompute follows: job descriptions feed only Stages 8, 10, and 11, and no Stage 3–7 state depends on them. §14.15 owns the complete deletion report.

---
