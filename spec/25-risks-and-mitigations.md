## §25. Risks and Mitigations

## §25.1 Risk: Exp2Res Becomes a Resume Tool Again

Mitigation:

```text
assessment pipeline comes before resume pipeline
README states resume is secondary export
resume branch references assessment snapshot
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
transactional typed-reference resolution before persistence
verifier loop
complete current provenance-chain requirements
unsupported phrase detection
```

## §25.5 Risk: External Integrations Pollute Truth Model

Mitigation:

```text
imported data enters only as a RawLog plus linked EvidenceItem
no automatic semantic promotion
per-source evidence strength
review gates for high-impact claims
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
owner deletion globally purges every derived database generation and verifies managed-export removal
residual managed paths are reported as deletion_incomplete, never as success
rebuild uses only retained raw records and may be retried without restoring deleted data
external source files and copied exports are reported as outside Exp2Res control
```

---
