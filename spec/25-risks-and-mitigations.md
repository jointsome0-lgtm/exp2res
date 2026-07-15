## §25. Risks and Mitigations

Like §27, this section is reference-only: each mitigation names a rule whose normative home is its owning section, and a mitigation line is never a second normative home.

## §25.1 Risk: Exp2Res Becomes a Resume Tool Again

Mitigation:

```text
assessment pipeline comes before resume pipeline
README states resume is secondary export
every resume branch uses §18's required explicit current snapshot anchor with no latest default
self-assessment tests are required before resume tests
```

## §25.2 Risk: The System Becomes Flattering Fiction

Mitigation: anti-flattery verification per §16.3; counterevidence fields, confidence levels, unknowns section, contradictions table.

## §25.3 Risk: The System Becomes Punitive

Mitigation:

```text
non-punitive report language
no moral scoring
no productivity grades
no global worth claims
```

## §25.4 Risk: Agents Overclaim

Mitigation:

```text
structured outputs
Pydantic validation
deterministic fail-closed size, token, context, call-count, and applicable cost-budget preflight before transport, with no input truncation
transactional typed-reference resolution before persistence
fail-closed single-pass verifier gates whose non-passing findings never trigger automatic repair
complete current provenance-chain requirements
unsupported phrase detection
```

## §25.5 Risk: External Integrations Pollute Truth Model

Mitigation:

```text
imported data enters only as a RawLog plus linked EvidenceItem
no automatic semantic promotion
per-source evidence strength
deterministic §9.4 strength-to-confidence ceilings
Stage 7 assessment and Stage 11 resume verifier gates before external projection
```

## §25.6 Risk: Self-Assessment Becomes Diagnosis

Mitigation:

```text
ban diagnostic labels
report observed patterns only
include non-clinical language tests
```

## §25.7 Risk: Deletion Leaves Private or Stale Derivations

Mitigation:

```text
automation cannot delete or rewrite raw records
owner deletion is never blocked by provenance links
correction replaces one coherent current generation
raw-log owner deletion globally purges every derived database generation and verifies managed-export removal
job-description deletion (§14.15) purges every dependent branch, bullet, finding, backup, and managed branch output without FK blocking
workspace purge (§14.16) removes all managed source and derived content, telemetry, backups, and temporary outputs, then applies checkpoint and VACUUM erasure
residual managed paths are reported as deletion_incomplete, never as success
rebuild uses only retained raw records and may be retried without restoring deleted data
external source files and copied exports are reported as outside Exp2Res control
```

---
