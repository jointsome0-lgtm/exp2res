## §13. Pipeline Specification

A pipeline stage exists only when it has at least one CLI trigger in §14 and produces a downstream-referenceable persisted business artifact or persisted state transition. `processing_runs` telemetry alone does not qualify an operation as a stage.

§14 is the sole canonical home of command forms. Each stage below points to its §14 trigger instead of restating shell syntax.

Unless a stage explicitly says otherwise, its inputs and outputs are current rows (`superseded_at IS NULL`) under §11–§12. Historical rows are inspectable but never implicit processing inputs.

Every stage validates its typed output references under §12 rule 10 before committing business rows. Missing, wrong-type, superseded, or duplicate targets fail the producing run atomically; JSON representation is not an integrity exception.

Every atomic business replacement follows §12 rule 13's one-swap/one-`generation_id` allocation: Stage 3 partitions by correction lineage, while each Stage 4, Stage 5, Stage 6 view, or Stage 10 branch swap has its own shared generation.

Every persisted recomputable business row and completed verifier finding resolves to the stage run that produced it through §12 rule 13 or §11.14, and a failed run owns no business rows or finding rows (§12.13).

Whenever a status-bearing row is offered to Stage 10 bullet generation or either export, the consumer applies the canonical `VerificationStatus` allowlists in §16.11. No consumer may replace those allowlists with a denylist or treat an unnamed status as passing.

Every §13 business-state mutation and all coupled managed-output work, including enumeration, writes, removal, and residual-path handling, run while the §8.1 workspace writer lock is held.

**Stale-export invalidation rule.** Exactly three trigger classes make a retained managed export stale: any transition that supersedes a current `AssessmentSnapshot`, `ResumeBranch`, or `ResumeBullet`; a completed Stage 7 or Stage 11 verifier pass that changes any current claim, snapshot, or bullet verification field (§13.7, §13.11); and a `gaps answer` whose answered state the dependent exports no longer reflect, which supersedes no row (§14.7). The triggering operation enumerates every affected manifest-backed set and attempts removal through §13.14; database state remains committed if cleanup fails, every residual path is reported as an unsuccessful invalidation, and no command may report the stale files as current output. The trigger sites name their trigger and affected sets while this rule owns the shared mechanics; privacy deletions remove managed sets under §13.13 and §14.16 rather than through this rule.

Before any writer begins its business operation, while it holds the §8.1 workspace lock, it applies §13.14's managed-output preamble; §13.14 rule 5 owns abandoned-sibling identification and disposition and the boundary that an unreconciled residual stops managed-output publication without blocking a database mutation, invalidation, owner deletion, or workspace purge.

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
8. `commit_or_pr` is used for an imported VCS commit only under §19.3's owner-attribution mapping: `owner_attribution = "owner"` yields `commit_or_pr`, while every other value imports as `artifact_reference`. Source or build evidence not represented by a commit has no V1 importer; a future importer must reintroduce its strength value together with its producing flow.
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
7. `ExperienceFact.evidence_item_ids` is exactly the duplicate-free selected-item set; Stage 3 derives `source_log_ids` as exactly the duplicate-free set of `EvidenceItem.raw_log_id` values reached through it (§15.2, §15.11). Every listed raw log therefore contributes at least one selected evidence item. Multiple selected items from one raw log produce separate `fact_sources` rows.
8. `ExperienceFact.confidence` must follow §9.4's evidential scopes, independence rules, and deterministic ceiling for the complete linked `EvidenceItem` set; confidence and evidence strength remain separate axes (§9.3).
9. `ExperienceFact.claim_kind` follows the fact-extractor producer semantics in §15.2; §13 does not restate that contract.
10. The extraction unit is one retained correction lineage: a root `RawLog` plus every retained correction that reaches it through `corrects_log_id`. The root appears first; corrections follow in ascending `recorded_at` order, then ID ascending by byte order. A correction is recorded after its target, which §14.4 requires to exist at capture, so this order is consistent with correction edges. If owner deletion nulls a correction's target, that correction becomes a new lineage root and the same rules apply.

    A retained correction displaces exactly the record named by its non-`NULL` `corrects_log_id`, as a whole record, and no other record. Formally, for a retained lineage member `x`, `displaced(x)` holds if and only if some retained lineage member `y` has `y.corrects_log_id = x.id`. The effective records are exactly the non-displaced lineage members. Thus `C2 → C1 → R` leaves only `C2` effective, while sibling corrections `C1 → R` and `C2 → R` leave both `C1` and `C2` effective in lineage order. An owner-deletion orphan roots its own lineage and, unless another retained correction targets it, is effective.

    Whole-record displacement removes the target's interpretation from current content. A displaced record's `raw_text` and, for a gap answer, its copied `question_text` / `question_reason` context are never Stage 3 or Stage 4 input and may never source a current fact or detection target. A displaced record's linked `manual_claim` items are displaced with it: their §9.4 evidential scope is exactly the owner statement whose interpretation the correction replaced, so they are not extractor input and no current fact may select them. Its linked non-`manual_claim` items remain selectable only as **displaced-record support**: their §9.4 scopes may still establish existence, recorded activity, attribution, or artifact content, but they are never a current-content channel.

    The prose-free **displaced-record support descriptor** is the universal §15 serialization of an `EvidenceItem` linked to a displaced record. Wherever any §15 contract input serializes such an item, it serializes exactly `id`, `raw_log_id`, `strength`, `uri`, and `path`; it never serializes `title`, `summary`, `created_at`, or `metadata`. The exclusion follows field class: `title` and `summary` are the item's unconstrained free-text fields — §11.3 does not restrict `title` to a non-content label, so an imported item's title can carry source-derived prose such as a commit subject or artifact name — while `uri` and `path` are structural locators whose transit §29.3 discloses and §29.4 re-checks immediately before serialization. A displaced `RawLog` is never serialized as an object into any prompt: serialized evidence context carries its identity only as the descriptor's `raw_log_id` reference, while ordinary typed provenance ID fields may repeat that ID without hydrating the object. The descriptor is a call-time projection of retained rows, not a persisted resolution artifact. This projection changes representation only; it never admits a record or item that the receiving contract otherwise excludes. **Effective lineage evidence** means the effective records and every `EvidenceItem` linked to those records.

    The **governing record** for rule 2 source placement, rule 13 project provenance, and §15.2 is per fact: the last, in that same lineage order — equivalently, the latest by (`recorded_at`, then ID) — of the effective records the fact lists in `source_log_ids`. Rule 6 guarantees at least one selected effective record, so every fact's governing record is defined; when the root is the fact's only selected effective record, the root governs. Governing selects placement and project only; it does not displace another effective sibling, and an effective sibling governs only the facts that select it, so sibling corrections with different placements or projects never leak provenance onto each other's facts. The fact inherits its governing record's placement unless the evidence-backed narrowing permitted by rule 2 applies. Before any Stage 3 or Stage 4 call, the service computes displacement, effective records, and displaced-record support directly from retained rows; each candidate fact's governing record then follows deterministically from its validated evidence selections at commit, and Stage 3 copies the governing placement and project onto the validated candidate under §15.2 and §15.11. Resolution uses no LLM call, persisted resolution artifact, or ambiguity state: a retained `corrects_log_id` resolves or is `NULL`, cycles are rejected at capture (§11.2), and owner deletion re-roots through §11.2.
11. Extraction computes the complete current fact generation for each selected lineage. A validated replacement and the `superseded_at` transition of the lineage's previous current facts commit atomically; it never appends a second current copy. Repeating extraction may add processing history or a superseded generation, but after success there is exactly one current fact generation for that lineage.
12. If a replacement changes current facts, every current gap, contradiction, signal, claim, snapshot, resume branch, and bullet is invalidated before it can be reused. §14.12 regenerates Stages 4–5; assessment views and resume branches are explicitly parameterized projections regenerated only through §14.9/§14.10 against the new current state, with every invalidated view reported under §13.13.
13. `ExperienceFact.project` is copied provenance, exactly like the default `occurred` placement: Stage 3 sets it to its governing record's `project` value under rule 10 — including `None` — after the §15.2 response validates; the extractor does not emit it (§15.11). §13.6 canonicalizes only at comparison time.

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

Known-gap assertions are emitted as ordinary `SelfClaim(dimension="gap")` rows and receive Stage 7 status. Stage 6 service-populates `AssessmentSnapshot.gap_question_ids` with exactly the complete current unanswered Stage 4 gap set it supplied to the writer — no scope filter and no writer echo (§15.4, §15.11) — so every current unanswered `GapQuestion` appears exactly once. Unknowns are uncertainty/question presentation, not claim-grade assertions: they do not receive an independent status, do not improve the §16.11 snapshot aggregate, and cannot guide Stage 10. Declarative prose about what is or is not true must be a snapshot-owned `SelfClaim` and pass the existing status gate.

Every current Stage 4 contradiction appears exactly once in `AssessmentSnapshot.contradiction_ids`; Stage 6 does not scope-filter conflicts. The writer cannot suppress one as resolved or dismissed; duplicate or stale IDs fail under §12 rule 10.

The writer emits exactly one `SelfClaim(claim_kind="narrative_summary")`; Stage 6 service-copies that claim's text into `AssessmentSnapshot.summary` (§15.4, §15.11), and the snapshot includes that claim ID. The summary receives an ordinary Stage 7 verdict and participates in §16.11 aggregation; Stage 6 cannot place separately unverified prose in the snapshot summary.

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

One Stage 7 invocation performs one semantic verifier pass per current claim and then terminates after aggregation. A valid non-passing verdict completes verification but closes every consumer gate that disallows its status. Stage 7 returns the complete §15.5 findings to the invoking CLI command, persists the denormalized operational fields plus the complete append-only finding history, and never invokes Stage 6, applies `suggested_rewrite`, edits or drops claim prose, or creates a gap question. The advisory rewrite follows §11.14's inspect-only lifecycle; revised claim wording can appear only in a later explicit Stage 6 replacement generation.

If verification or re-verification changes a current claim's status or counterevidence or the current snapshot status, every branch and bullet based on that snapshot is superseded in the same transaction, and the affected sets under §13's stale-export invalidation rule are that snapshot's `out/assessment/<snapshot-id>/` set (§13.12) and each dependent `out/branch/<branch-id>/` set; a verifier result may not leave a resume current against a changed verifier state.

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

## §13.10 Stage 10 — Relevance-Aware Bullet Generation

Trigger: verified-bullet-pack generation in §14.10.

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

For each bullet, Stage 10 service-sets `source_self_claim_ids` to the duplicate-free exact set of self-claims it passed to that bullet's writer invocation — the claims that guided selection or wording — empty iff none did (§15.6, §15.11). The writer cannot consume an unlisted claim because it receives exactly the listed set.

Stage 10 calls the §15.6 writer once per planned bullet in an isolated model context. Each invocation contains only facts selected for that bullet, their linked evidence context serialized under §13.3 rule 10, the `supported` snapshot-member self-claims selected for that bullet, explicit branch context, and the typed selected job description. The required descriptor substitution and displaced-`RawLog` omission preserve provenance without supplying displaced prose. One invocation can never read another bullet's facts, claims, or output.

After every planned invocation validates and the service assigns candidate bullet IDs, deterministic service code selects and orders the complete persisted batch before the atomic commit. It retains every valid candidate except exact duplicates and sorts by `ResumeBullet.target_section` in §10 `ResumeTargetSection` declaration order, then by the earliest position of any `matched_jd_requirements` member in the selected `ParsedJD.requirements` list (an empty match list sorts after every matched bullet), then by the validated `text` value ascending in UTF-8 byte order, and finally — reachable only between exact-duplicate candidates — by the deterministic Stage 10 plan-invocation index ascending. No random component of an allocated entity ID participates in ordering or retention. Two candidates are exact duplicates only when their validated `text` values are UTF-8 byte-equal after §11 text-hygiene validation; no normalization, case fold, punctuation fold, or semantic judgment participates. The first candidate in that sort order is retained and every later exact duplicate is dropped before persistence. Retained texts are therefore unique, so the (`target_section`, earliest-match position, `text` bytes) key totally orders the persisted batch from persisted state alone. After that suppression, if two distinct retained texts become byte-equal under Stage 12's mandatory generated-voice LF-newline and NFC projection, the complete Stage 10 candidate batch fails closed with a projection-collision error rather than dropping, rewriting, or ambiguously rendering either bullet. Stage 12 recomputes that persisted-state key for rendering, so no persisted order field is required.

Semantic near-duplicate detection is a named post-V1 check. V1 performs no LLM coherence, ordering, deduplication, or rewrite pass after the isolated calls, and §15.6 never receives sibling bullets. Coherence is limited to the deterministic selection, grouping, ordering, exact-duplicate suppression, schema validation, provenance checks, and verifier gates specified here and in §18; Stage 10 commits that complete retained branch/bullet batch atomically or none of it.

The supplied facts and eligible self-claims must all be current. A replacement fact or assessment generation supersedes every dependent current resume branch and bullet, making their managed exports stale under §13's stale-export invalidation rule; bullet generation must be run again rather than silently carrying old selections forward. Generating a branch name that folds equal to a current branch's under §14.10's NFC case-folded replacement identity atomically supersedes that branch's prior current row and bullets — the Stage 10 transaction enforces the folded-identity match under the workspace writer lock — so at most one generation of the named branch is current and no two current branches fold equal.

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

## §13.11 Stage 11 — Bullet Verification

Trigger: verified-bullet-pack verification in §14.10.

Persisted output:

```text
resume_bullets verification_status, unsupported_phrases, and verifier_reason
verification_findings
```

The verifier inspects phrases, not only whole bullets.

Stage 11 owns the semantic transition from each current bullet's initial `unverified` status to one §16.11 verdict. It validates one §15.7 finding for every current bullet and, for a completed pass, commits the complete branch finding set as one immutable §11.14 row per bullet in the same transaction as the denormalized bullet updates. If any finding remains invalid or missing after §15.1, no bullet verification update and no finding row commits; a new branch remains `unverified`, while a failed re-verification against unchanged inputs retains the prior complete verifier state and prior finding history. The failed `processing_runs` row with its `failure_code` is the durable record of that attempt. A branch remains ineligible for verified-bullet-pack export unless every bullet satisfies the applicable §16.11 allowlist.

One Stage 11 invocation performs one semantic verifier pass per current bullet, returns the complete findings to the invoking CLI command, persists the denormalized `verification_status`, `unsupported_phrases`, and `verifier_reason` plus the complete append-only finding history, and terminates. It never invokes Stage 10, applies the advisory `suggested_rewrite`, rewrites or drops a bullet, or creates a gap question. The advisory rewrite follows §11.14's inspect-only lifecycle; revised bullet wording requires an explicit Stage 10 generation, which supersedes the prior current branch generation.

If verification or re-verification changes any current bullet verification field, the affected set under §13's stale-export invalidation rule is the branch's `out/branch/<branch-id>/` set, and its removal or residual report completes before the new finding set is reported current. A verifier result may not leave an older valid matching manifest current against changed bullet verdicts.

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

Triggers: assessment export in §14.9 and verified-bullet-pack export in §14.10.

Persisted assessment outputs:

```text
out/assessment/<snapshot-id>/report.md
out/assessment/<snapshot-id>/self_claims.json
out/assessment/<snapshot-id>/evidence_map.json
out/assessment/<snapshot-id>/manifest.json
```

`<snapshot-id>` is the exported `AssessmentSnapshot.id` in §13.14's service-owned path-key form. No scope or scope-target text contributes a path component. The matching manifest carries the exact assessment-view identity; §13.14 rule 5 owns same-view replacement of prior assessment sets at publication.

Persisted verified-bullet-pack outputs:

```text
out/branch/<branch-id>/bullet_pack.md
out/branch/<branch-id>/evidence_map.json
out/branch/<branch-id>/verification_report.json
out/branch/<branch-id>/gaps.json
out/branch/<branch-id>/contradictions.json
out/branch/<branch-id>/manifest.json
```

`<branch-id>` is the exported `ResumeBranch.id` in §13.14's service-owned path-key form. Within the managed-output filesystem shape, `ResumeBranch.name` and every other user-controlled string appear only as manifest data, never in a path component; the dedicated `out/branch/` parent is disjoint from `out/assessment/` without a reserved branch display name.

Every JSON companion above other than `manifest.json` is one closed document: its top-level and nested objects reject undeclared fields, its required `schema_version` is the integer `1`, and any missing field, extra field, wrong type, unsupported version, duplicate typed ID, or unresolved typed reference fails export before §13.14 publication. `manifest.json` is independently closed and versioned under §13.14. Field types come from their named §10/§11 owners; these export projections do not create new enum domains or persisted models. The reusable nested projections are defined once here:

```text
CounterevidenceExport = {statement, source_ref_type, source_ref_id}
GapExport = {id, target_type, target_id, question, reason, priority, answered}
ContradictionExport = {id, title, description, left_ref_type, left_ref_id, right_ref_type, right_ref_id}
ClaimLink = {claim_id, source_signal_ids, source_fact_ids}
SignalLink = {signal_id, supporting_fact_ids, counter_fact_ids}
FactLink = {fact_id, evidence_item_ids, source_log_ids}
EvidenceLink = {evidence_item_id, raw_log_id}
```

The document field sets are exact:

```text
self_claims.json = {
  schema_version,
  snapshot: {id, created_at, scope, scope_target, title, verification_status},
  claims: list[{id, claim, claim_kind, dimension, confidence, verification_status,
                uncertainty, source_signal_ids, source_fact_ids,
                counterevidence: list[CounterevidenceExport]}],
  unknowns: list[GapExport],
  contradictions: list[ContradictionExport]
}

assessment evidence_map.json = {
  schema_version, output_kind, entity_id, rendered_claim_ids,
  claim_links: list[ClaimLink], signal_links: list[SignalLink],
  fact_links: list[FactLink], evidence_links: list[EvidenceLink]
}

bullet-pack evidence_map.json = {
  schema_version, output_kind, entity_id,
  rendered_bullets: list[{bullet_id, text, target_section, matched_jd_requirements,
                          source_self_claim_ids, source_fact_ids, source_log_ids}],
  claim_links: list[ClaimLink], signal_links: list[SignalLink],
  fact_links: list[FactLink], evidence_links: list[EvidenceLink]
}

verification_report.json = {
  schema_version, branch_id,
  findings: list[{bullet_id, verification_status, unsupported_phrases, verifier_reason}]
}

gaps.json = {schema_version, assessment_snapshot_id, gaps: list[GapExport]}
contradictions.json = {
  schema_version, assessment_snapshot_id,
  contradictions: list[ContradictionExport]
}
```

For an assessment export, `self_claims.json.snapshot.id` and assessment `evidence_map.json.entity_id` both equal the selected `AssessmentSnapshot.id`, and the evidence map's `output_kind` equals the assessment `ManagedOutputKind` member in §10 and its matching §13.14 manifest value. For a bullet-pack export, bullet-pack `evidence_map.json.entity_id` and `verification_report.json.branch_id` both equal the selected `ResumeBranch.id`; its evidence-map `output_kind` equals the resume `ManagedOutputKind` member in §10 and its matching manifest value; and both `gaps.json.assessment_snapshot_id` and `contradictions.json.assessment_snapshot_id` equal that branch's persisted `assessment_snapshot_id`. Any disagreement fails closed. The manifest discriminator is an internal managed-output kind tied to `ResumeBranch`; it is not the product-facing artifact name.

For an assessment export, `self_claims.json` carries exactly the selected snapshot's current claims, typed unknown-gap presentation state, and contradictions; `rendered_claim_ids` is the duplicate-free set of claims whose prose appears in `report.md`. For a bullet-pack export, `rendered_bullets` contains exactly every current retained branch bullet in §13.10 render order, and its `text` is the LF-newline- and NFC-normalized pre-Markdown-escape value used by §18. Each complete `rendered_bullets` row is one evidence-map segment: every sentence or logical line within that exact text inherits the row's complete typed provenance sets, and a bullet whose sentences cannot all be supported by those sets fails verification/export rather than acquiring renderer-authored bridge text. `verification_report.json.findings` contains exactly one row for every `rendered_bullets.bullet_id`, in the same order and with no other ID; every field equals that current bullet's denormalized §11.8 status projection. Append-only §11.14 finding history and `suggested_rewrite` never export. `gaps.json` and `contradictions.json` contain exactly the branch snapshot's referenced current rows, including each gap's current answered marker, without answer prose.

Each evidence map is a complete typed link closure, not free-form explanatory prose. `claim_links` resolves every rendered/source claim to its direct signals and facts; `signal_links` resolves those signals to supporting and counter facts; `fact_links` resolves every direct or signal-reached fact to evidence-item and raw-log IDs; and `evidence_links` resolves each reached item to its raw-log ID. A facts-only bullet legally has no claim edge and starts at its exact `source_fact_ids`; no unresolved placeholder is allowed. Every rendered bullet sentence must therefore round-trip from its exact `rendered_bullets` entry through these typed links to the current domain rows. A missing closure member, an unused extra member, or disagreement with the persisted §11 relations fails export.

Every JSON companion uses this exact byte encoding: object keys are serialized in Unicode code-point order with no insignificant whitespace; strings emit non-ASCII code points as raw UTF-8, escape quotation mark and reverse solidus only as `\"` and `\\`, use the short forms `\b`, `\f`, `\n`, `\r`, and `\t`, and use lowercase `\u00xx` for every remaining control code point; integers use minimal decimal form; booleans and null use their JSON literals; and offset-aware datetimes normalize to UTC as `YYYY-MM-DDThh:mm:ss.ffffffZ` with exactly six fractional digits. Each JSON document ends in exactly one LF. Set-valued and closure lists are ascending by typed ID UTF-8 bytes; `rendered_bullets` and `findings` use §13.10 render order; claims, gaps, and contradictions use their row IDs; a claim's counterevidence sorts by (`source_ref_type`, `source_ref_id`) UTF-8 bytes; and model-authored ordered string lists retain persisted order.

Exp2Res-authored natural-language values are LF-newline- and NFC-normalized only in the export projection, without mutating persisted rows; typed IDs and structural values are never normalized. `bullet_pack.md` and every JSON document use UTF-8, LF line endings, and exactly one final LF. In `report.md`, structural and generated segments use that same encoding and newline rule, while a §16.12–§16.13 source-voice fenced interior is the explicit exception: its code points and newline bytes, including CRLF when present in the persisted source substring, remain byte-exact. §17 owns the shared deterministic report/Markdown escaping, source exception, and empty-section rules; §18 cites them for the bullet pack. Re-exporting the same coherent snapshot or branch state produces byte-identical fixed-member bytes and therefore identical member hashes; `manifest.json` remains §13.14 publication metadata.

Export accepts only a current snapshot or branch whose complete current provenance chain resolves under §12 rule 10 and §16.1 and whose status-bearing inputs pass the applicable §16.11 allowlist. Assessment export validates the snapshot aggregate, every referenced claim, and the exact-one matching `narrative_summary` invariant; verified-bullet-pack export validates the branch's exact snapshot anchor, every selected self-claim, and every bullet. It then validates the complete fixed member set and publishes it only through §13.14. Unexpected missing, inconsistent, status-ineligible, manifest-mismatched, extra, or hash-inconsistent inputs fail closed. Raw-log owner deletion does not leave a partial database graph for export to skip (§13.13 rules 5–6 own the purge-and-rebuild reset); export remains unavailable until recomputation and the explicit §14.9/§14.10 view and branch regenerations succeed.

Assessment export also validates the project scope target and the typed unknown-gap references — resolvable, duplicate-free, current rows — before §17 rendering; a gap answered after synthesis renders as answered-since-synthesis context under §17 and never fails export. Bullet verification and export recover the Stage 10 job description through `ResumeBranch.job_description_id` and resolve every bullet's `matched_jd_requirements` against that `ParsedJD`. Neither consumer may substitute free-form unknown prose or requirement labels when a typed reference or branch association is absent.

## §13.13 Derived Lifecycle and Recompute

Trigger: lifecycle recomputation in §14.12; correction and raw-log owner-deletion operations in §14.4 and §14.11 and job-description deletion in §14.15 invoke the same service flow.

This subsection orchestrates existing stages and is not a pipeline stage. Each correction (§14.4), raw-log owner deletion (§14.11), job-description deletion (§14.15), or recompute (§14.12) lifecycle flow creates one orchestration `processing_runs` row with `stage = "13.13"`; every stage run it invokes creates its own row under that stage's stable §13 identifier and links to the orchestration row through `parent_run_id`. A direct single-stage command leaves `parent_run_id = NULL`. Identifier `13.13` is legal for this telemetry row only, and telemetry alone still does not make the operation a pipeline stage.

Rules:

1. Selected-lineage recomputation under §14.12 replaces Stage 3 facts for that correction lineage, then regenerates the complete current Stage 4–5 graph from all current facts. Full recomputation under §14.12 replaces facts for every lineage before the same global Stage 4–5 rebuild. Stage 4 inside this flow follows its §13.4 retain-or-replace rule; a retained detection generation does not halt the flow's Stage 5 regeneration. Lifecycle recomputation ends at Stage 5: Stages 3–5 are parameterless shared derivations, while Stage 6–7 assessment views and Stage 10–11 resume branches are explicitly parameterized projections that only their §14.9/§14.10 commands regenerate.
2. A recompute validates every stage's complete candidate output, including §12 rule 10, before the business-state swap. A successful swap leaves at most one current generation per lineage, assessment view, or named branch and marks the replaced generation `superseded_at`; payloads are never updated in place.
3. Where an active stage explicitly requires replacement, that stage rule controls — including Stage 4's retain-or-replace equivalence rule and complete replacements in Stages 5–6 and replacement of an existing named branch in Stage 10. For other standalone reruns whose inputs have not changed, the stage may retain the prior current generation or replace it. No rerun may expose duplicate current facts, signals, claims, snapshots, gaps, or contradictions. If validation fails before a source change, the prior current generation remains current and no partial candidate output is inserted.
4. Correction capture and invalidation are one atomic database visibility boundary before rebuilding starts: the transaction inserts the new raw/evidence records, supersedes current facts for that correction lineage, and supersedes every current gap, contradiction, signal, claim, snapshot, resume branch, and resume bullet. Managed exports are enumerated and removal is attempted as part of the same operation; residual paths are reported as an unsuccessful invalidation rather than silently retained. A crash or recompute failure can therefore leave the correction plus no replacement current graph, but can never leave the pre-correction graph current against the changed source set. The correction remains stored and §14.12 is the retry surface.
5. Raw-log owner deletion is a privacy-first global reset. The service first enumerates and attempts to remove every managed `out/` artifact and every managed §12.14 backup, with any residual backup path governed by rule 6 exactly like a residual export path, then atomically purges every current and historical fact, fact source, gap, contradiction, signal, claim, snapshot, resume branch, resume bullet, and verification finding while hard-deleting the selected `RawLog` and cascading its evidence items. Findings belong to the derived purge because their reasons, quoted unsupported phrases, advisory rewrites, and counterevidence statements are generated prose. Job descriptions and `processing_runs`/`llm_calls` telemetry remain subject to content-hash redaction: the same purge transaction sets `input_hash` and `output_hash` to `NULL` on every retained `llm_calls` row, not only rows provably tied to the deleted record. A deterministic hash of guessable purged content would otherwise remain an oracle that confirms deleted text by hashing candidates, and a selective closure that misses one content path fails open. Retained telemetry keeps identifiers, timing, statuses, token counts, accounting values, retry counts, stable failure codes, and `prompt_policy_hash`, none derived from owner content bytes; `prompt_policy_hash` hashes fixed contract instructions plus the structured-output schema revision only. Opaque IDs may stop resolving. The service then attempts a full Stage 3–5 recompute from every surviving lineage through fresh runs whose call rows record fresh hashes over surviving content only; redaction applies to every call row committed before the purge transaction. Surviving `gap_answer` raw logs stay interpretable through their §14.7 self-containment; the rebuild never re-links them to regenerated questions.
6. Database deletion commits even if managed-path removal or rebuilding fails. Every managed-output enumeration and removal uses §13.14's canonical-root containment and no-follow contract; migration-backup removal applies equivalent POSIX canonicalization and workspace containment without treating a path as source input. The service never follows a symlink during deletion, and any symlink entry or path that resolves outside the canonical workspace is left untouched and reported as residual for manual removal. The command verifies the managed paths before reporting success: any residual path makes the result `deletion_incomplete`, is reported explicitly, and is never treated as a retained evidence source. Rebuild failure reports a separate unsuccessful result with no derived database model. Neither failure restores the deleted owner record or purged derived rows; the user may remove reported files and, for raw-log deletion, retry recomputation through §14.12. No FK, filesystem error, or failed processing run may restore or block database deletion.
7. The raw-log reset is deliberately global in V1. Selective graph deletion and warn-and-skip are rejected because JSON and implicit dependencies cannot prove that all private derived text was found, and a partial truth model could be mistaken for a complete one.
8. Raw-log deletion covers only Exp2Res-managed database records, `out/`, and managed §12.14 migration backups. Supplied source files and copies of prior exports outside the managed workspace remain user-controlled; §14.11 reports their known paths but does not delete them.
9. Invalidated-view reporting: except for rule 10 job-description deletion, whose §14.15 purge report has no regeneration command against the deleted JD, every transaction that supersedes or purges current snapshots and branches — inside this flow or in a direct §14.6/§14.7/§14.8 generation — captures each affected assessment view — scope, scope target, snapshot ID — and, for each affected branch, its name, retained job-description ID, and anchoring view. The invoking command reports every invalidated view with its executable §14.9 regeneration command, and every invalidated branch with that captured context plus the §14.10 command shape — a branch command cannot be executable as printed, because §14.10 requires a current `--snapshot` that exists only after its view is regenerated. Every printed command quotes each argument value with POSIX single-quote shell quoting (an embedded single quote becomes `'\''`), so a target or branch name containing whitespace or shell metacharacters stays copy-paste-safe and selects the exact stored value. After raw-log owner deletion this report is command output only, never persisted derived state. A bare `recompute` retried after a crash rebuilds Stages 3–5 and reports that no current assessment view exists, pointing at §14.9; it never infers a desired view set from historical or purged rows.
10. Job-description deletion is a privacy-first dependent purge, never an FK-blocked request. Before its database transaction, the service captures the selected `JobDescription` inspection projection and every current or historical `ResumeBranch` whose `job_description_id` names it, including each branch ID and name; it deduplicates and attempts removal of every managed §12.14 migration backup and each exact `out/branch/<branch-id>/` set derived from those captured opaque IDs, because either may retain the deleted vacancy or generated prose. No branch outside the captured set can own or spare one of those directories: §12 rule 11 IDs never collide or are reused, so a later branch for another job description has a distinct path even when its stored name is byte-equal or fold-equal, independent of filesystem case or normalization behavior. A matching, missing, or invalid manifest never redirects deletion to a name-derived path; §13.14 governs exact-path validation, no-follow removal, and residual reporting. In one transaction the flow deletes every `VerificationFinding` targeting a bullet of those branches, every bullet of those branches, every captured branch, and then the selected `JobDescription`; current assessment views and all current or historical snapshots, claims, and their findings remain untouched because they do not depend on a job description. The same transaction applies rule 5's global content-hash redaction to every retained `llm_calls` row committed before it; `processing_runs` and `llm_calls` otherwise survive under their content-free §12.13/§12.15 field limits. No recompute follows: job descriptions feed only Stages 8, 10, and 11, and no Stage 3–7 state depends on them. §14.15 owns the complete deletion report.

## §13.14 Managed-Output Writer

This subsection is the sole managed-output path, manifest, publication, and filesystem-operation contract for §13.12 exports and §13.13 lifecycle cleanup. It is a support contract, not a pipeline stage, creates no business row or `processing_runs` row, and adds no CLI form.

1. **ID-derived path identity.** The two reserved managed parents are `out/assessment/` and `out/branch/`. An assessment set lives only at `out/assessment/<snapshot-id>/`; a resume set lives only at `out/branch/<branch-id>/`. The component is the exact opaque entity ID assigned under §12 rule 11, not a name, view, selector, title, or other user-controlled value. For `AssessmentSnapshot` and `ResumeBranch`, the service allocator additionally emits that ID as 1–128 lowercase ASCII bytes matching `^[a-z0-9][a-z0-9_-]{0,127}$`; the writer re-validates this invariant before any filesystem operation and fails closed on a nonconforming stored ID rather than encoding, truncating, normalizing, or substituting it. Lowercase ASCII single components plus collision-free, never-reused IDs eliminate traversal, dot-segment, reserved-name, confusable-normalization, and case-fold alias classes structurally. Within the managed-output filesystem shape, snapshot title and view identity, and branch name and job-description identity, are manifest data only.
2. **Closed versioned manifest.** `manifest.json` is strict UTF-8 JSON using §11's validation, datetime, string-hygiene, and `extra = forbid` policy. Its common fields are exactly `manifest_version` (integer `1`), `output_kind` (`ManagedOutputKind`, §10), `entity_id`, `generation_id`, `produced_by_run_id`, `created_at`, `identity`, `source_ids`, `render_input_sha256`, and `members`. `entity_id`, `generation_id`, and `produced_by_run_id` exactly match the exported snapshot or branch and its non-null §12 rule 13 production provenance; `created_at` is the offset-aware manifest creation time. For the assessment kind, `identity` is exactly `{snapshot_title, scope, scope_target}` and `source_ids` is exactly `{self_claim_ids, self_signal_ids, experience_fact_ids, evidence_item_ids, raw_log_ids, gap_question_ids, contradiction_ids}`. For the resume kind, `identity` is exactly `{branch_name, job_description_id, assessment_snapshot_id}` and `source_ids` is exactly `{resume_bullet_ids, assessment_snapshot_ids, job_description_ids, self_claim_ids, self_signal_ids, experience_fact_ids, evidence_item_ids, raw_log_ids, gap_question_ids, contradiction_ids, jd_requirement_ids}`; each of `assessment_snapshot_ids` and `job_description_ids` contains exactly the one ID also named by `identity`, and the complete consumed snapshot and job-description projections, including parsed requirement order, participate in `render_input_sha256` below. Every source list is the complete duplicate-free, ID-byte-ordered set actually read to render any member; no source ID is omitted, inferred from prose, or included without being read. These completeness lists are local managed metadata, not a §11 provider, source-acquisition, model-response, or SQLite-hydration boundary, and neither §11's per-list cap nor its total-object cap truncates or rejects an otherwise valid complete manifest; each individual string retains its §11 bound and hygiene.

   `render_input_sha256` uses §11's canonical-serialization and lowercase SHA-256 rules over the exact closed, type-tagged render-input bundle read from the export transaction's coherent database snapshot: the selected entity; every source projection consumed by §13.12 and §17 or §18; their storage-level generation and production provenance where applicable; every lifecycle-owned field read by rendering or the §16.11 gate; `manifest_version`; and `output_kind`. Entries are partitioned by canonical entity type and ID-byte-ordered within type. No database value read to render, validate, or gate a member may be excluded, and no filesystem value enters this hash. Thus a gap answer or Stage 7/11 status transition invalidates an old set even when every entity ID and generation ID is unchanged. The §13.12 members and schemas define the initial, not-yet-emitted `manifest_version = 1` rendering contract because the project remains preimplementation; after a conforming implementation emits that version, any rendering-contract change that would change member bytes for an unchanged bundle requires a new `manifest_version`.

   `members` is a filename-byte-ordered list of closed `{name, sha256}` objects. Its names equal the applicable §13.12 fixed member filenames exactly, contain no separator, and exclude `manifest.json`; the final directory contains exactly those regular files plus `manifest.json`, with no extra entry. Each digest uses §11's SHA-256/lowercase-hex representation over the exact stored member bytes; §11's canonical JSON serialization does not rewrite Markdown or other member bytes for this hash. A manifest version, kind, identity shape, source-list shape, member name, or extra field outside this closed schema is invalid.
3. **Matching and currentness.** A structurally valid manifest is *matching* only when its managed parent and directory component agree with `output_kind` and `entity_id`, its entity and production fields agree with the persisted row, its identity and complete source lists agree with the graph used for rendering, its fixed member set is exact, every listed entry is a no-follow regular file, and every member digest matches. A matching manifest may identify a retained historical set for deterministic stale-view cleanup; it is *current output* only when the entity is current, all source rows and generation provenance still match current database state, the applicable §16.11 gate still passes, and recomputing `render_input_sha256` from that same coherent state equals the manifest value. A directory with a missing, invalid, mismatched, member-hash-inconsistent, or render-input-hash-inconsistent manifest is never returned, indexed, or treated as current output. Assessment-view replacement and stale-set removal compare the manifest's exact (`scope`, case-folded canonical `scope_target`) identity while paths remain ID-keyed. A known captured entity-ID path may still require no-follow privacy removal under §13.13; an invalid manifest never authorizes a different path or suppresses residual reporting.
4. **Complete candidate construction.** Under the §8.1 writer lock, export renders and validates the complete fixed member set in an owner-private candidate sibling inside the applicable managed parent on the same filesystem. Candidate names match the reserved `.exp2res-candidate-<entity-id>-<nonce>` form, where the nonce is 32 service-assigned lowercase hexadecimal ASCII bytes and no user value contributes. The writer creates every directory and member with rule 7's modes, writes each member completely, validates its content and exact bytes, flushes every member with `fsync` or the platform's documented durability equivalent, then writes `manifest.json` last. It validates the final manifest and member hashes, flushes the manifest and candidate directory, and flushes the managed parent before publication. A partial candidate has no valid final-path manifest and is never current.
5. **Atomic publication, replacement, and recovery.** Before publishing an assessment candidate, the writer enumerates matching manifests and removes or reports every different snapshot-ID set naming the same assessment view; failure to remove one aborts publication with that residual and never touches another view. With no final entry, publication is one same-filesystem atomic rename of the complete candidate into the final ID path. If a valid prior set already occupies that exact path and its member bytes plus `render_input_sha256` equal the candidate, publication is an idempotent no-op and the candidate is removed. Otherwise the writer uses one atomic directory exchange when available. Where exchange is unavailable, it atomically renames the prior set to a reserved `.exp2res-rollback-<entity-id>-<nonce>` sibling, flushes the parent, then atomically renames the complete candidate into the final path. This fallback may expose a final-path absence, never a partial or rollback set, and no reader treats that intermediate state as current. A non-empty final path without a valid matching prior manifest is never overwritten; it is reported as residual and publication aborts.

   A successful exchange or candidate-to-final rename is the publication visibility commit point: the new final entry is already complete, and a later parent-flush or old-sibling-cleanup failure cannot be reported as if publication never occurred. If the fallback candidate rename fails after moving the prior set, the writer attempts one no-follow atomic restoration from rollback to the absent final path; restoration success returns the prior set to current, while restoration failure leaves that prior set recoverable only as a reported rollback residual and leaves no current final-path set. The writer never claims an impossible rollback guarantee after an arbitrary filesystem failure.

   The next writer's §13 preamble identifies abandoned siblings only by the two reserved service-owned forms. It removes an abandoned candidate. For a rollback sibling, it restores that valid prior set when the final path is absent, or removes the rollback only after the final path has a valid matching manifest; an invalid or ambiguous state is left untouched and appears exactly once in that command's residual list. Such a residual stops managed-output publication before rendering or final-path I/O, but it never blocks correction invalidation, `gaps answer`, raw-log or job-description deletion, workspace purge, or another database mutation whose owning rule commits despite managed-cleanup failure; those operations attempt any in-scope sibling removal under rule 6 and retain the residual in their incomplete result when removal fails. All reconciliation uses rule 6 and completes while the workspace lock is held. This protocol is the only crash recovery for managed publication; no lockfile makes a set current.
6. **Canonical containment and no-follow operations.** The writer requires the workspace's `out/` entry itself to be a real directory rather than a symlink, establishes its canonical real path, requires it to remain beneath the canonical workspace root, requires every existing descendant ancestor to be a real directory beneath that `out/` root, inspects each entry without dereferencing it, and performs destination creation relative to already validated parent directories. Every managed write, rename, manifest/member read, enumeration, and delete rechecks canonical root containment and uses no-follow semantics for every path component and final entry, extending §13.13 rule 6 from deletion to the complete managed-output surface. A symlink, a non-directory ancestor, an entry resolving outside the root, or a changed path between validation and operation is skipped, never traversed, and reported as residual; this is true even when a symlink target is inside the workspace. No manifest field grants path authority.
7. **Owner-private modes.** The managed parents, candidate and rollback siblings, and final set directories are created as `0700`; every member and manifest is created as `0600`, without relying on process umask, under §29.2. Publication preserves those modes, and a mode-setting failure aborts before the set can become current.
8. **Failure boundary.** Any rendering, schema, source-graph, member, manifest, disk-full, flush, or rename failure before rule 5's successful final-entry exchange or candidate rename publishes no candidate. A prior final set remains current when it was not moved or when fallback restoration succeeds; if restoration itself fails, the prior set remains only as the reported rollback residual and no final-path set is current. The candidate is removed, or its exact path is reported as residual if no-follow cleanup cannot complete. After the visibility commit point, the writer immediately flushes the managed parent, removes the exchanged-out candidate or rollback sibling, and flushes the parent again. Failure in either post-commit step leaves the complete new set current in the live filesystem but reports durability or cleanup incomplete, reports every retained old sibling as residual, emits no successful §14.14 export result, and cannot report unqualified success; the next writer revalidates and reconciles the observed entries rather than assuming whether an unflushed rename survived a crash. Export publication never mutates database state. A post-generation invalidation or privacy deletion remains committed under §13.13 even when its managed cleanup fails, and existing residual-path/exit semantics continue to apply. No command reports a managed path from a pre-commit failed or incomplete set through §14.14; every later read revalidates the matching manifest, render-input hash, and member hashes and fails closed on mismatch.

---
