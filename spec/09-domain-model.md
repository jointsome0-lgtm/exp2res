## §9. Domain Model

## §9.1 Ontology Overview

```text
RawLog              = source record immutable to automation and deletable by its owner
EvidenceItem        = source-linked evidence unit persisted during capture/import
ExperienceFact      = atomic statement about what happened
SelfSignal          = pattern signal derived from facts/evidence
SelfClaim           = assessment claim about the user, with confidence and sources
Contradiction       = detected conflict between effective source records and current facts (§13.4)
GapQuestion         = question needed to improve weak/uncertain model
AssessmentSnapshot  = versioned self-assessment at a time
JobDescription      = external context for export
ResumeBranch        = job-targeted resume candidate branch anchored to one assessment snapshot
ResumeBullet        = generated resume phrase with evidence links
VerificationFinding = persisted append-only verifier-attempt result over a self-claim or resume bullet
```

Facts, gaps, contradictions, signals, claims, snapshots, branches, and bullets form replaceable derived generations. `superseded_at IS NULL` means current; a set timestamp means historical and unavailable to new verification, generation, or export. Correction preserves superseded history. Raw-log owner deletion purges every derived generation as the privacy-first exception; job-description deletion purges only the dependent resume state, and workspace purge removes every managed class (§5.3, §13.13, §14.16).

## §9.2 Confidence Layers

`ClaimKind` (§10) classifies persisted internal claims. `ExperienceFact.claim_kind` is produced by the fact extractor (§15.2), and `SelfClaim.claim_kind` by the self-assessment writer (§15.4). A `ResumeBullet` is an export projection governed by its source links and verification fields (§11.8), not a `ClaimKind` carrier.

General claim confidence uses `Confidence` (§10), carried by `ExperienceFact.confidence` (§11.4), `SelfSignal.confidence` (§11.5), and `SelfClaim.confidence` (§11.6). Temporal placement confidence is a separate axis: only `OccurredAt.confidence` uses `TemporalConfidence` (§10–§11.1).

## §9.3 Evidence Strength

Evidence strength values are the `EvidenceStrength` values (§10), carried by `EvidenceItem.strength` (§11.3).

The calibration model that consumes these values is §9.4.

Evidence strength is not the same as confidence.

A strong artifact may support a narrow fact, but not a broad identity claim.

## §9.4 Evidence-to-Confidence Calibration

Calibration is capability-based. `EvidenceStrength` membership remains canonical in §10; the table below attaches an evidential scope to each retained member and is not a second membership list.

| `EvidenceStrength` | Evidential scope |
|---|---|
| `manual_claim` | Owner self-report captured at entry. Establishes what the owner directly states, as self-report. |
| `imported_activity_event` | System-recorded activity telemetry. Establishes that the named activity occurred at the recorded time; not outcome, ownership depth, or quality. |
| `artifact_reference` | Reference to an external artifact. Establishes the artifact's existence and topical content; not authorship depth, outcome, or use. |
| `commit_or_pr` | Imported VCS commit explicitly attributed to the owner by its source contract. Establishes the recorded change and that source-asserted owner attribution; not independently verified identity, ownership depth, outcome, production use, or mastery. |
| `design_doc` | Local design document. Establishes that the design content exists and what it contains; design-level work, not implementation or outcome. |

These values are qualitatively different and deliberately not totally ordered. No rule may rank one `EvidenceStrength` above another.

**Source independence.** Evidence items linked through one `RawLog` count as one source for calibration. A repeated owner assertion carried as `manual_claim` across multiple raw logs is repetition, never independent corroboration. Non-`manual_claim` items from distinct raw logs are independent sources. V1 creates only `direct` `fact_sources` rows (§12.4); before any `corroborating` row may exist, a future producer must define how those rows enter calibration.

**Fact ceiling.** The service computes the ceiling on `ExperienceFact.confidence` from the fact's complete linked item set without LLM judgment. The ceiling is `high` if and only if that set spans at least two distinct raw logs and includes at least one non-`manual_claim` item; otherwise it is `medium`.

Under §13.3's single-lineage extraction, a fact's linked items span multiple raw logs only within its correction lineage. In V1 the `high` ceiling is therefore reachable exactly when an imported root is corrected by the owner and the fact selects a non-`manual_claim` item of that displaced root as displaced-record support plus an item of an effective correction record; cross-lineage corroboration has no V1 producer (§12.4) and cannot create the spanning set.

A ceiling is a cap, never an entitlement. The extractor assigns the lowest defensible `Confidence` at or below it. When the selected extraction context contains materially conflicting statements bearing on the fact, the extractor must assign at most `low`; the conflict itself remains Stage 4 output and is not a calibration artifact.

**Propagation caps.** `SelfSignal.confidence` must not exceed the maximum confidence of its supporting facts. `high` additionally requires at least two supporting facts reached through at least two distinct raw logs. Any non-empty `counter_fact_ids` caps the signal at `medium`.

`SelfClaim.confidence` must not exceed the maximum confidence of its listed source signals and facts. Whether those sources actually cover the breadth of a broad statement is the semantic half, judged by Stage 7 under §13.7 rule 2; a narrow strong fact never entitles a broad claim to its confidence.

The maximum over an empty source list is `unknown`: a signal with no supporting facts or a claim with no listed sources is capped at `unknown`. The cap is therefore total and structural validation never diverges on empty lists; §13.7 rule 1 still fails a sourceless claim at verification.

**Authorization boundary.** Calibration bounds only confidence and only within the linked items' evidential scopes. No strength or ceiling authorizes ownership, metric, production, temporal, or employment content. Sections §16.4–§16.8 evaluate their explicit-support requirements independently of confidence and fail closed even when every linked item supports the highest confidence permitted within its scope.

**Enforcement.** The deterministic ceiling and propagation caps are structured-output validation. A candidate whose `confidence` exceeds its computed cap is invalid output under §15.1: retry once with the validation errors, then mark the run failed. The service never silently lowers a value. Stage 7 judges the semantic half; insufficient scope or breadth produces a non-passing §16.11 status, never a rewrite.

---
