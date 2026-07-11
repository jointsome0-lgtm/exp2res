## §9. Domain Model

## §9.1 Ontology Overview

```text
RawLog              = immutable user/imported source record
EvidenceItem        = source-linked evidence unit persisted during capture/import
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

`ClaimKind` (§10) classifies persisted internal claims. `ExperienceFact.claim_kind` is produced by the fact extractor (§15.2), and `SelfClaim.claim_kind` by the self-assessment writer (§15.4). A `ResumeBullet` is an export projection governed by its source links and verification fields (§11.8), not a `ClaimKind` carrier.

General claim confidence uses `Confidence` (§10), carried by `ExperienceFact.confidence` (§11.4), `SelfSignal.confidence` (§11.5), and `SelfClaim.confidence` (§11.6). Temporal placement confidence is a separate axis: only `OccurredAt.confidence` uses `TemporalConfidence` (§10–§11.1).

## §9.3 Evidence Strength

Evidence strength values are the `EvidenceStrength` values (§10), carried by `EvidenceItem.strength` (§11.3).

Evidence strength is not the same as confidence.

A strong artifact may support a narrow fact, but not a broad identity claim.

---
