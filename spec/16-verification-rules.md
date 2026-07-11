## §16. Verification Rules

## §16.1 Evidence Rule

Every typed domain reference must resolve to the current target required by §12 rule 10 when written; missing, wrong-type, superseded, or duplicate IDs fail the producing operation atomically. JSON storage is not an integrity exception.

Every current self-claim and resume bullet — and any row entering verification or export — must resolve a complete current chain through at least one fact, one `fact_sources` row with `support_type = direct`, its non-null `EvidenceItem`, and that item's retained `RawLog`. Every current resume branch must resolve its required current assessment snapshot, every bullet must resolve its current branch, and each source self-claim on that bullet must belong to that exact snapshot. `ResumeBullet.source_self_claim_ids` must be the duplicate-free exact set of claims used by the writer and must be empty iff no claim guided the bullet. Superseded rows are exempt inspect-only history: after a lifecycle swap their references legitimately point at superseded targets, which is why §12 rule 9 keeps them out of processing, verification, generation, and export inputs. A resume bullet's `source_log_ids` must equal the distinct raw logs reachable from its `source_fact_ids`; a non-empty but inconsistent ID list fails verification and export. Owner deletion is handled before those consumers run: §13.13 purges the derived database graph, attempts verified managed-output removal with residual-path reporting, and then rebuilds from retained raw records instead of treating vanished private sources as skippable evidence.

## §16.2 Mirror Rule

Self-assessment claims must be allowed to be uncomfortable.

The system must not rewrite them into motivational language.

## §16.3 Anti-Flattery Rule

Forbidden without evidence:

```text
exceptional
world-class
highly skilled
expert
production-grade
proven leader
visionary
```

## §16.4 Ownership Rule

A verifier must normalize every ownership-bearing source and candidate phrase to `OwnershipLevel` and compare them using the normative order in §10. A candidate ownership level must not rank above the strongest level explicitly supported by its linked evidence. If no linked source establishes ownership, the supported level is `unknown`, which authorizes only `unknown`; an ownership-bearing phrase that cannot be normalized fails closed. General claim `confidence` does not change the supported ownership rank.

## §16.5 Metric Rule

Numeric metrics must appear in source logs, imported artifacts, or gap answers.

## §16.6 Production Rule

Do not claim impact/production/customer/scale/revenue/reliability unless evidence explicitly supports it.

## §16.7 Temporal Rule

A verifier must normalize every source and candidate time expression to `OccurredAt` before comparing precision. A candidate with no temporal expression does not introduce a precision claim.

For non-range values, the normative order from weakest to strongest is `unknown < year < quarter < month < week < exact_day < exact_datetime`. For comparison with ranges, normalize these values to maximum uncertainty widths: `unknown` is unbounded, `year` is 366 days, `quarter` is 92 days, `month` is 31 days, `week` is 7 days, `exact_day` is 1 day, and `exact_datetime` is zero.

For `date_range` and `approximate_range`, width is `end - start`; missing, inverted, or zero-width bounds are invalid (§11.1) and verification fails closed. A narrower width is more precise. At equal width, `approximate_range` is weaker than `date_range` or a non-range value; changing from approximate to exact bounds at the same width is therefore an upgrade.

A candidate upgrades temporal precision when its normalized width is narrower than the strongest precision supported by its linked evidence, or when it strengthens exactness at equal width. The verifier must reject that candidate unless additional linked evidence supports the stronger precision.

## §16.8 Employment Rule

Independent projects, competitions, and learning must not be rendered as employment.

## §16.9 Identity Rule

Do not turn temporary patterns into permanent identity claims.

Allowed:

```text
Current evidence suggests...
A recurring pattern appears...
In recent projects...
```

Forbidden:

```text
You are fundamentally...
You will always...
Your true identity is...
```

## §16.10 Diagnostic Rule

The system must not generate medical, psychiatric, or clinical labels.

Allowed:

```text
The user reports burnout under ambitious plans.
```

Forbidden:

```text
The user has depression / ADHD / anxiety disorder.
```

## §16.11 Verification-Status Semantics and Consumer Gates

`VerificationStatus` has one operational meaning per member and is enforced through role-aware allowlists. The Stage 10 column distinguishes a snapshot anchor from a self-claim input; resume export considers its snapshot anchor, source self-claims, and `ResumeBullet`; assessment export considers its `AssessmentSnapshot` and claim presentation.

| Status | Meaning | May feed Stage 10 | May pass resume export | May pass assessment export |
|---|---|---|---|---|
| `unverified` | No successful semantic verifier verdict exists for the current row. | No | No | No |
| `supported` | Every material assertion is adequately grounded in current evidence. | Snapshot anchor and self-claim | Snapshot anchor, source self-claim, and bullet | Snapshot and claim presentation |
| `partially_supported` | A grounded core remains, but some phrasing or inference is not fully supported. | Snapshot anchor only | Snapshot anchor only | Snapshot and claim presentation, visibly labeled |
| `inferred_but_acceptable` | A bounded inference is acceptable inside the mirror but not as an external claim. | Snapshot anchor only | Snapshot anchor only | Snapshot and claim presentation, visibly labeled |
| `needs_clarification` | Current evidence is too incomplete or ambiguous for a safe conclusion. | No | No | Snapshot and claim presentation as uncertainty or a question |
| `contradicted` | Current evidence materially conflicts with the assertion. | No | No | Snapshot and claim presentation with contradiction and counterevidence visible |
| `unsupported` | Current evidence does not adequately support the assertion. | No | No | No |
| `rejected` | The candidate violates a verification rule and requires replacement rather than qualification. | No | No | No |

Thus the Stage 10 snapshot-anchor allowlist is exactly `supported`, `partially_supported`, and `inferred_but_acceptable`; only a `supported` self-claim may guide resume generation, and only a `supported` bullet may export. Assessment export permits `supported`, `partially_supported`, `inferred_but_acceptable`, `needs_clarification`, and `contradicted` snapshots because the mirror must preserve visibly labeled weakness and conflict. `unverified` blocks all three gated consumer classes in the table: validation or generation alone is not verification.

Stage 6 initializes every new claim and snapshot to `unverified`. Stage 7 verifies every claim, then computes the snapshot status atomically from the complete claim-status set. Any `unverified` claim leaves the snapshot `unverified`; an empty claim set is invalid under §11.7/§12 and cannot be aggregated; otherwise the first status present in this most-restrictive-first precedence is the aggregate:

```text
rejected
unsupported
contradicted
needs_clarification
partially_supported
inferred_but_acceptable
supported
```

Stage 7 is the only operation that may write this aggregate while the snapshot is current. Claim verification fields, the aggregate, and dependent branch/bullet supersession commit in one database transaction. Stage 7 and assessment export must reject a snapshot unless exactly one member claim is a `narrative_summary` whose claim text equals `AssessmentSnapshot.summary`; every gated consumer must also reject a stored aggregate that does not equal a fresh reduction of the current claims. Managed-file removal is attempted under §13's lifecycle rules, cannot roll back that database state, and reports residual paths on failure. Stage 10 initializes bullets to `unverified`, and Stage 11 alone assigns their semantic verdicts.

---
