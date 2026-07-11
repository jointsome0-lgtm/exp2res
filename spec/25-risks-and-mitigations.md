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
verifier loop
source requirements
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

---

