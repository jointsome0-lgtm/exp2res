## §9. Domain Model

## §9.1 Ontology Overview

```text
RawLog              = source record immutable to automation and deletable by its owner
EvidenceItem        = source-linked evidence unit persisted during capture/import
ExperienceFact      = atomic statement about what happened
SelfClaim           = assessment claim about the user, with confidence and sources
Contradiction       = detected conflict between effective source records and current facts (§13.4)
GapQuestion         = question needed to improve weak/uncertain model
AssessmentSnapshot  = versioned self-assessment at a time
JobDescription      = external context for export
ResumeBranch        = job-targeted resume candidate branch anchored to one assessment snapshot
ResumeBullet        = generated resume phrase with evidence links
VerificationFinding = persisted append-only verifier-attempt result over a self-claim or resume bullet
```

Facts, gaps, contradictions, claims, snapshots, branches, and bullets form replaceable derived generations under §11's supersession lifecycle:

- One current generation exists per replacement identity.
- Superseded rows are inspect-only history that §12 rule 9 keeps out of processing, verification, generation, and export inputs.
- Correction preserves superseded history.
- Raw-log owner deletion purges every derived generation as the privacy-first exception.
- Job-description deletion purges only the dependent resume state.
- Workspace purge removes every managed class (§5.3, §13.13, §14.16).

## §9.2 Confidence Layers

`ClaimKind` (§10) classifies persisted internal claims. `ExperienceFact.claim_kind` is produced by the fact extractor (§15.2), and `SelfClaim.claim_kind` by the self-assessment writer (§15.4). A `ResumeBullet` is an export projection governed by its source links and verification fields (§11.8), not a `ClaimKind` carrier.

General claim confidence uses `Confidence` (§10), carried by `ExperienceFact.confidence` (§11.4) and `SelfClaim.confidence` (§11.6). Temporal placement confidence is a separate axis: only `OccurredAt.confidence` uses `TemporalConfidence` (§10–§11.1).

## §9.3 Evidence Strength

Evidence strength values are the `EvidenceStrength` values (§10), carried by `EvidenceItem.strength` (§11.3).

The calibration model that consumes these values is §9.4.

Evidence strength is not the same as confidence.

A strong artifact may support a narrow fact, but not a broad identity claim.

## §9.4 Evidence-to-Confidence Calibration

Calibration is capability-based. `EvidenceStrength` membership remains canonical in §10. The table below attaches an evidential scope to each retained member and is not a second membership list.

| `EvidenceStrength` | Evidential scope |
|---|---|
| `manual_claim` | Owner self-report captured at entry. Establishes what the owner directly states, as self-report. |
| `imported_activity_event` | Imported activity-domain evidence. Supports only activity the source explicitly reports as having occurred at the supplied `OccurredAt` placement; a diary note, verbal note, plan, or aggregate does not by itself establish completion, outcome, ownership depth, or quality beyond that source statement. |
| `knowledge_state_snapshot` | Source-attributed Atlas knowledge state on the source's own scales. Establishes narrow studied/learning-grade support within the declared subjects, trail, and references; not implementation, built or production use, outcome, ownership depth, mastery, or a direct claim. |
| `artifact_reference` | Reference to an external artifact. Establishes the artifact's existence and topical content; not authorship depth, outcome, or use. |
| `commit_or_pr` | Imported VCS commit explicitly attributed to the owner by its source contract. Establishes the recorded change and that source-asserted owner attribution; not independently verified identity, ownership depth, outcome, production use, or mastery. |
| `design_doc` | Local design document. Establishes that the design content exists and what it contains; design-level work, not implementation or outcome. |

These values are qualitatively different and deliberately not totally ordered. No rule may rank one `EvidenceStrength` above another.

`knowledge_state_snapshot` participates in the fact ceiling below as one non-`manual_claim` item from its source `RawLog`. Its high authority within the row's knowledge-attribution scope does not grant `Confidence = "high"` or change the source-count rule.

**Source independence.** Evidence items linked through one `RawLog` count as one source for calibration. A repeated owner assertion carried as `manual_claim` across multiple raw logs is repetition, never independent corroboration. Non-`manual_claim` items from distinct raw logs are independent sources. V1 creates only `direct` `fact_sources` rows (§12.4). Before any `corroborating` row may exist, a future producer must define how those rows enter calibration.

**Fact ceiling.** The service computes the ceiling on `ExperienceFact.confidence` from the fact's complete linked item set without LLM judgment. The ceiling is `high` if and only if that set spans at least two distinct raw logs and includes at least one non-`manual_claim` item; otherwise it is `medium`.

Under §13.3's single-lineage extraction, a fact's linked items span multiple raw logs only within its correction lineage. An owner-supplied locator accepted through §14, authorized under §29.4, and persisted under §13.1 remains an assertion by the same owner as the prose beside it: its `artifact_reference` scope does not make the raw log independent and does not raise a single-log fact above `medium`. What the locator opens is reachability within an owner-only correction lineage. In V1 the `high` ceiling is therefore reachable when either (a) an imported root is corrected by the owner, or (b) an owner-captured root carrying an artifact locator is displaced by an owner correction, and the fact selects the displaced root's non-`manual_claim` item as §13.3 rule 10 displaced-record support plus an item of an effective correction record. Either route supplies two distinct raw logs and at least one non-`manual_claim` item. Cross-lineage corroboration has no V1 producer (§12.4) and cannot create the spanning set.

**Trust position.** Route (b) is a deliberate trust decision, not an unnoticed consequence of persisting locators, and it is not narrowed to locators the service can check. Exp2Res is a mirror of its owner (§2): it takes an owner-typed locator as the good-faith statement it is and verifies content, not the owner's good faith. The trust it extends is bounded by the paragraph above — an owner-typed locator never makes its own raw log an independent source, so route (b) still requires an owner correction to produce a second raw log — and by the authorization boundary below, where §16.4–§16.8 evaluate ownership, metric, production, temporal, and employment support independently of confidence and fail closed at any ceiling. What route (b) grants is one confidence step for a fact whose root the owner both evidenced and corrected, never permission to claim more. The verifiability asymmetry between locator forms is known and accepted: a local `path` is canonicalized and authorized at capture and re-checked immediately before every §15 serialization (§29.4), while a remote `uri` is validated for syntax only and is never dereferenced (§29.4) — yet both carry the same `artifact_reference` scope, because neither is ever read and the calibration rests on the owner's assertion in both cases. Conditioning the ceiling on a resolvable locator would cap an owner whose artifacts sit behind a closed network perimeter below an owner with public URLs, over evidence Exp2Res reads in neither case.

A ceiling is a cap, never an entitlement. The extractor assigns the lowest defensible `Confidence` at or below it. When the selected extraction context contains materially conflicting statements bearing on the fact, the extractor must assign at most `low`. The conflict itself remains Stage 4 output and is not a calibration artifact.

**Propagation caps.** `SelfClaim.confidence` must not exceed the maximum confidence of its listed source facts. Whether those sources actually cover the breadth of a broad statement is the semantic half, judged by Stage 7 under §13.7 rule 2. A narrow strong fact never entitles a broad claim to its confidence.

A claim that cites §15.4 patterns through `source_pattern_labels` additionally carries the pattern-generalization caps, computed deterministically at the Stage 6 boundary from the response's own patterns before they are discarded and re-checkable afterward from the persisted split: the boundary persists the cited patterns' counter facts as the claim's `counter_fact_ids` (§11.6), and §15.4's equality rule makes a pattern-citing claim's closure exactly its cited patterns' fact union, so the claim's supporting facts are `source_fact_ids` minus `counter_fact_ids` and no unrelated direct source fact can enter the claim to bypass the recurrence requirement. The claim's confidence must not exceed the maximum confidence of those supporting facts — a counter fact never raises the source maximum a generalization rests on — `high` requires that the supporting facts include at least two facts reached through at least two distinct raw logs, and a non-empty `counter_fact_ids` caps the claim at `medium`. A `pattern_signal` claim cites at least one pattern (§15.4), so a pattern generalization can never outrank the recurrence and counterevidence recorded against it.

The maximum over an empty source list is `unknown`: a claim with no listed sources is capped at `unknown`. The cap is therefore total and structural validation never diverges on empty lists. §15.4 rejects an empty `source_fact_ids` list as invalid structured output and §13.7 rule 1 fails any sourceless row that reaches verification, so the empty case is a defensive bound, never a licensed persisted state (§16.1).

**Authorization boundary.** Calibration bounds only confidence and only within the linked items' evidential scopes. No strength or ceiling authorizes ownership, metric, production, temporal, or employment content. Sections §16.4–§16.8 evaluate their explicit-support requirements independently of confidence and fail closed even when every linked item supports the highest confidence permitted within its scope.

**Enforcement.** The deterministic ceiling and propagation caps are structured-output validation. A candidate whose `confidence` exceeds its computed cap is invalid output under §15.1: retry once with the validation errors, then mark the run failed. The service never silently lowers a value. Stage 7 judges the semantic half. Insufficient scope or breadth produces a non-passing §16.11 status, never a rewrite.

---
