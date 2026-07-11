## §9. Domain Model

## §9.1 Ontology Overview

```text
RawLog              = immutable user/imported source record
EvidenceItem        = normalized referenceable evidence unit
ExperienceFact      = atomic statement about what happened
SelfSignal          = pattern signal derived from facts/evidence
SelfClaim           = assessment claim about the user, with confidence and sources
Contradiction       = conflict between claims, facts, or signals
GapQuestion         = question needed to improve weak/uncertain model
AssessmentSnapshot  = versioned self-assessment at a time
JobDescription      = external context for export
ResumeBranch        = job-targeted resume candidate branch
ResumeBullet        = generated resume phrase with evidence links
VerificationFinding = verifier output over claim/bullet/snapshot
```

## §9.2 Confidence Layers

Every non-raw claim should have a type:

```text
observed_fact
inferred_fact
pattern_signal
hypothesis
narrative_summary
export_claim
```

General claim confidence uses `Confidence` (§10), carried by `ExperienceFact.confidence` (§11.4), `SelfSignal.confidence` (§11.5), and `SelfClaim.confidence` (§11.6). Temporal placement confidence is a separate axis: only `OccurredAt.confidence` uses `TemporalConfidence` (§10–§11.1).

## §9.3 Evidence Strength

Evidence strength values are the `EvidenceStrength` values (§10), carried by `EvidenceItem.strength` (§11.3).

Evidence strength is not the same as confidence.

A strong artifact may support a narrow fact, but not a broad identity claim.

---
