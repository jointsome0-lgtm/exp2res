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

And confidence:

```text
low
medium
high
unknown
```

## §9.3 Evidence Strength

Evidence strength values:

```text
weak_note
manual_claim
imported_activity_event
artifact_reference
code_artifact
commit_or_pr
test_or_demo
design_doc
external_feedback
verified_outcome
```

Evidence strength is not the same as confidence.

A strong artifact may support a narrow fact, but not a broad identity claim.

---

