## §16. Verification Rules

## §16.1 Evidence Rule

Every typed domain reference must resolve to the current target required by §12 rule 10 when written; missing, wrong-type, superseded, or duplicate IDs fail the producing operation atomically. JSON storage is not an integrity exception.

Every current self-claim and resume bullet — and any row entering verification or export — must resolve a complete current chain through at least one fact, one `fact_sources` row with `support_type = direct`, its non-null `EvidenceItem`, and that item's retained `RawLog`. Every current resume branch must resolve its required current assessment snapshot, every bullet must resolve its current branch, and each source self-claim on that bullet must belong to that exact snapshot. `ResumeBullet.source_self_claim_ids` must be the duplicate-free exact set of claims used by the writer and must be empty iff no claim guided the bullet. Superseded rows are exempt inspect-only history: after a lifecycle swap their references legitimately point at superseded targets, which is why §12 rule 9 keeps them out of processing, verification, generation, and export inputs. A resume bullet's `source_log_ids` must equal the distinct raw logs reachable from its `source_fact_ids`; a non-empty but inconsistent ID list fails verification and export. Owner deletion is handled before those consumers run: §13.13 purges the derived database graph, attempts verified managed-output removal with residual-path reporting, and then rebuilds from retained raw records instead of treating vanished private sources as skippable evidence.

## §16.2 Mirror Rule

Within the generated-voice scope in §16.12, self-assessment claims must be allowed to be uncomfortable.

The system must not rewrite them into motivational language.

## §16.3 Anti-Flattery Rule

In generated voice as defined by §16.12, the following are forbidden without evidence:

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

Every datetime equality, ordering, duration, and interval operation in this rule uses the UTC instant under §12 rule 3; stored ISO 8601 TEXT bytes never participate in temporal comparison.

For non-range values, the normative order from weakest to strongest is `unknown < year < quarter < month < week < exact_day < exact_datetime`. For comparison with ranges, normalize these values to maximum uncertainty widths: `unknown` is unbounded, `year` is 366 days, `quarter` is 92 days, `month` is 31 days, `week` is 7 days, `exact_day` is 1 day, and `exact_datetime` is zero.

For containment and widening checks, normalize an `OccurredAt` to an anchored uncertainty interval. `unknown` is the unbounded timeline; `exact_datetime` is the singleton at `start`; every other non-range value is the half-open interval from `start` to `start +` its maximum uncertainty width above; and `date_range` / `approximate_range` use the half-open interval `[start, end)`. An extractor candidate is contained only when its normalized interval is a subset of its governing record's interval (§13.3 rule 10). The extractor must not re-align the source anchor to manufacture containment.

For `date_range` and `approximate_range`, width is `end - start`; missing, inverted, or zero-width bounds are invalid (§11.1) and verification fails closed. A narrower width is more precise. At equal width, `approximate_range` is weaker than `date_range` or a non-range value; changing from approximate to exact bounds at the same width is therefore an upgrade.

A candidate upgrades temporal precision when its normalized width is narrower than the strongest precision supported by its linked evidence, or when it strengthens exactness at equal width. The verifier must reject that candidate unless additional linked evidence supports the stronger precision.

## §16.8 Employment Rule

Independent projects, competitions, and learning must not be rendered as employment.

## §16.9 Identity Rule

Generated voice under §16.12 must not turn temporary patterns into permanent identity claims.

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

Generated voice under §16.12 must not author medical, psychiatric, or clinical labels.

Allowed:

```text
The user reports burnout under ambitious plans.
```

Forbidden:

```text
The user has depression / ADHD / anxiety disorder.
```

## §16.11 Verification-Status Semantics and Consumer Gates

`VerificationStatus` has one operational meaning per member and is enforced through role-aware allowlists. The Stage 10 column distinguishes a snapshot anchor from a self-claim input; verified-bullet-pack export considers its snapshot anchor, source self-claims, and `ResumeBullet`; assessment export considers its `AssessmentSnapshot` and claim presentation.

| Status | Meaning | May feed Stage 10 | May pass verified-bullet-pack export | May pass assessment export |
|---|---|---|---|---|
| `unverified` | No successful semantic verifier verdict exists for the current row. | No | No | No |
| `supported` | Every material assertion is adequately grounded in current evidence. | Snapshot anchor and self-claim | Snapshot anchor, source self-claim, and bullet | Snapshot and claim presentation |
| `partially_supported` | A grounded core remains, but some phrasing or inference is not fully supported. | Snapshot anchor only | Snapshot anchor only | Snapshot and claim presentation, visibly labeled |
| `inferred_but_acceptable` | A bounded inference is acceptable inside the mirror but not as an external claim. | Snapshot anchor only | Snapshot anchor only | Snapshot and claim presentation, visibly labeled |
| `needs_clarification` | Current evidence is too incomplete or ambiguous for a safe conclusion. | No | No | Snapshot and claim presentation as uncertainty or a question |
| `contradicted` | Current evidence materially conflicts with the assertion. | No | No | Snapshot and claim presentation with contradiction and counterevidence visible |
| `unsupported` | Current evidence does not adequately support the assertion. | No | No | No |
| `rejected` | The candidate violates a verification rule and requires replacement rather than qualification. | No | No | No |

Thus the Stage 10 snapshot-anchor allowlist is exactly `supported`, `partially_supported`, and `inferred_but_acceptable`; only a `supported` self-claim may guide bullet generation, and only a `supported` bullet may enter the verified bullet pack. Assessment export permits `supported`, `partially_supported`, `inferred_but_acceptable`, `needs_clarification`, and `contradicted` snapshots because the mirror must preserve visibly labeled weakness and conflict. `unverified` blocks all three gated consumer classes in the table: validation or generation alone is not verification.

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

Stage 7 is the only operation that may write this aggregate while the snapshot is current. Claim verification fields, the aggregate, and dependent branch/bullet supersession commit in one database transaction. Stage 7 and assessment export must reject a snapshot unless exactly one member claim is a `narrative_summary` whose claim text equals `AssessmentSnapshot.summary`; every gated consumer must also reject a stored aggregate that does not equal a fresh reduction of the current claims. Manifest-backed managed-set removal is attempted under §13.13 and §13.14, cannot roll back that database state, and reports residual paths on failure. Stage 10 initializes bullets to `unverified`, and Stage 11 alone assigns their semantic verdicts.

## §16.12 Generated-Voice Boundary

Verification has two orthogonal scopes:

1. Structural validation applies to every payload: required keys, field types, closed-enum values, typed-reference resolution, current/superseded constraints, §16.1 provenance chains, and §16.11 status semantics and allowlists. Natural-language origin never exempts malformed structure.
2. The natural-language rules in §16.2–§16.10 bind only Exp2Res-authored voice. By default this includes generated fact, signal, claim, gap, contradiction, verifier, and bullet language from §15; system-authored report prose in §17; and generated bullet-pack prose in §18. §16.3, §16.9, and §16.10 use this boundary explicitly. For §16.4–§16.8, source text may be an evidence operand, but only the generated candidate phrase can violate the rule.

The §16.2–§16.10 prohibitions are owner-referential: they constrain generated language that characterizes the owner — skill, experience, identity, health, impact — wherever it appears. A generated description of an external demand, such as §15.9 `ParsedJD` requirement, signal, keyword, or red-flag text, remains generated voice for structural validation and §15.9's parse-fidelity rules, but faithfully preserved demand wording ("expert Python", "production operations") characterizes the vacancy, not the owner; §16.3–§16.10 neither reject it nor force its rewriting. The moment any Exp2Res-authored text asserts that the owner meets a demand — in a bullet, claim, or report line — that assertion is owner-referential generated voice and every applicable rule binds in full.

Source voice is owner or system-of-record material, not an Exp2Res claim. `RawLog.raw_text`, owner-authored gap-answer text, `JobDescription.raw_text`, imported artifact content, and natural-language values in §19 payloads receive structure-only validation at ingestion. Voice rules may consult them as evidence but must never reject, rewrite, redact, normalize, or block their persistence because of their wording. A retained source may therefore contain flattery terms, permanent-identity wording, diagnostic language, metrics, production claims, or employment language without itself violating §16.

Every natural-language field emitted by an LLM is generated voice by default, including parser text, detector questions/descriptions, verifier counterevidence/reasons, warnings, and text that merely resembles a quotation. A rendered segment retains source voice only when its contract carries a typed source reference and the renderer verifies the segment byte-for-byte against the referenced persisted source value or a contiguous substring of it. Untagged, unresolved, normalized, or paraphrased text is generated voice. Validators scan every generated segment and only the structure around a validated source segment; they must not concatenate mixed-origin text and run a full-blob voice scan.

`GapQuestion.question` is generated voice and must pass §16 before Stage 4 persistence. At `gaps answer` capture, the service verifies that `RawLog.metadata.question_text` is an exact copy of that already validated question. Once copied into the owner-controlled raw record, the field is immutable source context for later extraction and is not rewritten or blocked by a later voice scan; this one-way handoff cannot admit unvalidated question text. In every case, a voice finding must never force a rewrite of owner memory or system-of-record material.

This subsection does not change §16.1 or any §16.11 status meaning, aggregation rule, or consumer allowlist. Voice compliance is a phrase/content check on generated candidates; status gates remain the independent permission layer for assessment and verified-bullet-pack consumers.

## §16.13 Language Scope

Except for the source-faithful mixed-language job-description fields below, V1 Exp2Res-authored natural-language output is English. This applies to every generated segment under §16.12, including facts, signals, claims, questions, contradictions, verifier prose and warnings, generated bullets, and §17–§18 report and bullet-pack export prose. A non-English generated segment is a §16 voice violation evaluated at §16.12's segment boundary; mixed source/generated content is never concatenated to evade that check. The §16.3 anti-flattery term list and the §16.9/§16.10 phrase rules are specified and verified for English, which is the honest generated-language coverage boundary of V1.

Source voice may be in any language and remains byte-for-byte preserved under §16.12; it is never rejected, translated, normalized, or rewritten because of language. Cross-language extraction is in scope: Russian-language or other-language source text may produce English facts, claims, and other generated prose. Meaning-preserving translation occurs only inside generated voice, and it never weakens the evidence and overclaim rules in §16. A quoted source segment remains source voice only through §16.12's typed-reference and byte-for-byte check; an English paraphrase of a Russian source is generated voice.

For a mixed-language job description, §15.9 requirement and keyword text must preserve the vacancy's demand modality and meaning. Faithfully preserved demand wording may remain non-English; it remains generated voice for structural validation and §15.9 parse fidelity and characterizes the vacancy under §16.12. Every Exp2Res-authored assertion about the owner remains English and fully bound by §16.2–§16.10.

Localized or multilingual generated output is explicitly deferred beyond V1.

---
