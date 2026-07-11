## §21. Evals

## §21.1 No Unsupported Self-Claim

Test (enforces §16.3):

```text
Given one weak raw log
When assessment writer says "strong expertise"
Then verifier rejects or rewrites the claim
```

## §21.2 No Automatic Skill From Tick-like

Test:

```text
Given Tick-like event "worked on verifier"
When facts are extracted
Then system may create weak activity fact
But must not create "verifier loop expert"
```

## §21.3 Atlas Artifact Does Not Equal Mastery

Test:

```text
Given an Atlas artifact reference mentions Kafka
When Exp2Res imports it
Then it may create context evidence
But must not claim Kafka mastery
```

## §21.4 No Hidden Contradiction

Test:

```text
Given evidence supports both high ambition and burnout under plans
When assessment is generated
Then contradiction/risk is preserved
```

## §21.5 No Invented Metrics

Test:

```text
Given no metric in evidence
When resume writer creates "reduced latency by 40%"
Then verifier rejects it
```

## §21.6 No Ownership Upgrade

Test:

```text
Given source says participated
When output says led/designed/owned
Then verifier rejects it

Given source ownership_level = unknown
When output says observed
Then verifier rejects it
```

## §21.7 Temporal Precision Preservation

Test:

```text
Given source precision = month
When output contains exact day
Then verifier rejects it

Given source precision = month
When output contains a two-day date_range
Then verifier rejects it

Given source precision = approximate_range
When output changes the same bounds to date_range
Then verifier rejects it
```

## §21.8 No Diagnostic Labels

Test:

```text
Given user reports burnout
When assessment is generated
Then system may mention reported burnout
But must not assign clinical diagnoses
```

## §21.9 Resume Requires Evidence

Test:

```text
Given bullet has no source_fact_ids or source_log_ids
Then export fails
```

## §21.10 Assessment Requires Evidence

Test:

```text
Given self_claim has no source facts/signals
Then assessment verification fails
```

## §21.11 Raw-Layer Authority Is Actor-Scoped

Test:

```text
Given a retained raw log
When an importer, extractor, verifier, or other automation attempts to update or delete it
Then the operation is rejected and the raw log is unchanged

Given the owner selects the same raw log with §14.11
Then deletion is allowed and no provenance foreign key may block it
```

## §21.12 Re-Extraction Has One Current Generation

Test:

```text
Given one unchanged correction lineage has already produced facts
When extraction runs again for that lineage
Then no duplicate current fact generation exists
And every higher-layer input is either from the one coherent current generation or is unavailable
And every managed export tied to a superseded snapshot/branch is removed or reported as a residual-path failure
```

## §21.13 Correction Recomputes the Current Model

Test:

```text
Given a current fact, signal, claim, snapshot, and resume branch derive from log_001
When the owner appends a self-contained correction targeting log_001
Then log_001 remains unchanged
And correction capture plus invalidation become visible atomically before rebuild
And the lineage facts plus the complete current gap, contradiction, signal, claim, and snapshot generations are replaced
And the old snapshot remains inspectable but cannot verify, generate, or export
And the old resume branch and managed exports are unavailable until regenerated
```

## §21.14 Owner Deletion Is a Privacy Reset

Test:

```text
Given raw log log_001 has evidence, current and historical derivations, snapshots, and managed exports
When the owner deletes log_001
Then log_001 and its evidence are absent
And all current and historical derived rows are purged before rebuild
And managed-export removal is attempted and verified
And surviving raw lineages are recomputed
And a rebuild failure does not restore log_001 or any purged derived content
And a managed output that cannot be removed is reported as a residual path while database deletion remains committed
```

## §21.15 Provenance References Resolve at Write Time

Test:

```text
Given a producer emits a missing, wrong-type, superseded, or duplicate typed provenance ID
When the stage attempts to persist its candidate output
Then no candidate business output is committed
And the processing run fails with the field, ID, and expected target type recorded

Given one raw log has two selected EvidenceItems that support one fact
When the fact is persisted
Then fact_sources contains one row per EvidenceItem
And source_log_ids contains the raw-log ID once
And confidence calibration considers both distinct strengths

Given a Stage 6 candidate shares one current SelfClaim across snapshots or leaves a current SelfClaim unowned
When the candidate transaction is validated
Then the complete batch fails before commit
```

---
