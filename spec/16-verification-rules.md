## §16. Verification Rules

## §16.1 Evidence Rule

Write-time typed-reference and JSON-storage integrity follow §12 rules 2 and 10.

Every current self-claim and resume bullet — and any row entering verification or export — must resolve a complete current chain through at least one fact, one `fact_sources` row with `support_type = direct`, its non-null `EvidenceItem`, and that item's retained `RawLog`. Every current resume branch must resolve its required current assessment snapshot, every bullet must resolve its current branch, and each source self-claim on that bullet must belong to that exact snapshot. `ResumeBullet.source_self_claim_ids` must satisfy §13.10/§15.6's citation contract. Superseded rows are exempt inspect-only history: after a lifecycle swap their references legitimately point at superseded targets, which is why §12 rule 9 keeps them out of processing, verification, generation, and export inputs. A resume bullet's `source_log_ids` must equal the distinct raw logs reachable from its `source_fact_ids`; a non-empty but inconsistent ID list fails verification and export. Owner deletion is handled before those consumers run: §13.13's purge-and-rebuild reset (rules 5–6) removes the derived graph rather than leaving vanished private sources as skippable evidence.

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

1. A verifier must normalize every source and candidate time expression to `OccurredAt` before comparing precision.
2. A candidate with no temporal expression does not introduce a precision claim.
3. Temporal comparisons in this rule use the UTC instant under §12 rule 3.
4. For non-range values, the normative order from weakest to strongest is `unknown < year < quarter < month < week < exact_day < exact_datetime`.
5. For comparison with ranges, normalize those non-range values to maximum uncertainty widths:
   - `unknown` is unbounded.
   - `year` is 366 days.
   - `quarter` is 92 days.
   - `month` is 31 days.
   - `week` is 7 days.
   - `exact_day` is 1 day.
   - `exact_datetime` is zero.
6. For containment and widening checks, normalize an `OccurredAt` to an anchored uncertainty interval:
   - `unknown` is the unbounded timeline.
   - `exact_datetime` is the singleton at `start`.
   - Every other non-range value is the half-open interval from `start` to `start +` its maximum uncertainty width under rule 5.
   - A closed `date_range` / `approximate_range` uses the half-open interval `[start, end)`.
   - An open-ended range (§11.1) uses `[start, ∞)`, anchored below and unbounded above.
7. An extractor candidate is contained only when its normalized interval is a subset of its governing record's interval (§13.3 rule 10).
8. The extractor must not re-align the source anchor to manufacture containment.
9. Where an open-ended placement is the governing interval of a containment check (§13.3 rule 2, §15.2), a bounded candidate is tested against the placement's **attested window** `[start, R.recorded_at)`, in which `R` is the `RawLog` carrying the placement — for a derived row, its governing record under §13.3 rule 10.

   An open-ended placement is unbounded as a statement about the future but bounded as evidence, because the record carrying it could attest only what had already happened when it was recorded.
10. An open-ended candidate is instead tested against the unclipped `[start, ∞)` and is contained exactly when its `start` is at or after the governing `start`, which keeps §13.3 rule 2's default copy legal at any attested width.
11. The clip in rule 9 applies to open-ended placements only: a closed range keeps its stated `[start, end)` even when `end` falls after `recorded_at`, because the owner stated that bound.
12. An attested window that is empty or inverted — a `start` at or after `recorded_at` — contains nothing, so a record of activity that has not yet begun licenses no bounded narrowing at all.
13. An open-ended candidate is never contained in a closed governing interval, because replacing a stated end with no end is widening.
14. In the entailment direction, a selected open-ended support keeps `[start, ∞)` and is a subset of no bounded candidate: an ongoing record never by itself entails a bounded placement, so both directions fail toward the weaker claim.
15. For a closed `date_range` or `approximate_range`, width is `end - start`; inverted or zero-width bounds are invalid (§11.1) and verification fails closed.
16. An open-ended range's width is unbounded, so it is the weakest range form: every closed candidate width is narrower than it, and bounding an ongoing period is an upgrade requiring additional linked evidence that states the bound.
17. A narrower width is more precise.
18. At equal width, `approximate_range` is weaker than `date_range` or a non-range value; changing from approximate to exact bounds at the same width is therefore an upgrade.
19. A candidate upgrades temporal precision when its normalized width is narrower than the strongest precision supported by its linked evidence, or when it strengthens exactness at equal width.
20. The verifier must reject that candidate unless additional linked evidence supports the stronger precision.
21. An open-ended placement supports exactly one temporal statement — activity from `start` with no recorded end as of `R.recorded_at` — and says nothing about the reading present.
22. Generated content that supplies an end date, or that asserts continuation past `R.recorded_at` in phrasing such as "to date", "currently", or "still ongoing today", claims temporal information no evidence carries and fails this rule within §16.12's generated-voice scope; a source excerpt keeps its own words unchanged.

§17 owns the deterministic rendering that keeps the distinction visible in the mirror.

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
You report burnout under ambitious plans.
```

Forbidden:

```text
You have depression / ADHD / anxiety disorder.
```

## §16.11 Verification-Status Semantics and Consumer Gates

`VerificationStatus` has one operational meaning per member and is enforced through role-aware allowlists. The Stage 10 gate distinguishes a snapshot anchor from a self-claim input; verified-bullet-pack export considers its snapshot anchor, source self-claims, and `ResumeBullet`; assessment export considers its `AssessmentSnapshot` and claim presentation.

Each member, its required meaning, and the roles it may enter at each of the three gates — Stage 10, verified-bullet-pack export, and assessment export:

- **`unverified`.** No successful semantic verifier verdict exists for the current row.
  - May feed Stage 10: no.
  - May pass verified-bullet-pack export: no.
  - May pass assessment export: no.
- **`supported`.** Every material assertion is adequately grounded in current evidence.
  - May feed Stage 10: snapshot anchor and self-claim.
  - May pass verified-bullet-pack export: snapshot anchor, source self-claim, and bullet.
  - May pass assessment export: snapshot and claim presentation.
- **`partially_supported`.** A grounded core remains, but some phrasing or inference is not fully supported.
  - May feed Stage 10: snapshot anchor only.
  - May pass verified-bullet-pack export: snapshot anchor only.
  - May pass assessment export: snapshot and claim presentation, visibly labeled.
- **`inferred_but_acceptable`.** A bounded inference is acceptable inside the mirror but not as an external claim.
  - May feed Stage 10: snapshot anchor only.
  - May pass verified-bullet-pack export: snapshot anchor only.
  - May pass assessment export: snapshot and claim presentation, visibly labeled.
- **`needs_clarification`.** Current evidence is too incomplete or ambiguous for a safe conclusion.
  - May feed Stage 10: no.
  - May pass verified-bullet-pack export: no.
  - May pass assessment export: snapshot and claim presentation as uncertainty or a question.
- **`contradicted`.** Current evidence materially conflicts with the assertion.
  - May feed Stage 10: no.
  - May pass verified-bullet-pack export: no.
  - May pass assessment export: snapshot and claim presentation with inline counterevidence and any coexisting detection row visible.
- **`unsupported`.** Current evidence does not adequately support the assertion.
  - May feed Stage 10: no.
  - May pass verified-bullet-pack export: no.
  - May pass assessment export: no.
- **`rejected`.** The candidate violates a verification rule and requires replacement rather than qualification.
  - May feed Stage 10: no.
  - May pass verified-bullet-pack export: no.
  - May pass assessment export: no.

Thus the Stage 10 snapshot-anchor allowlist is exactly `supported`, `partially_supported`, and `inferred_but_acceptable`. Only a `supported` self-claim may guide bullet generation, and only a `supported` bullet may enter the verified bullet pack. Assessment export permits `supported`, `partially_supported`, `inferred_but_acceptable`, `needs_clarification`, and `contradicted` snapshots because the mirror must preserve visibly labeled weakness and conflict. `unverified` blocks all three gated consumer classes above: validation or generation alone is not verification.

Stage 6 initializes every new claim and snapshot to `unverified`. Stage 7 verifies every claim, then computes the snapshot status atomically from the complete claim-status set. Any `unverified` claim leaves the snapshot `unverified`. An empty claim set is invalid under §11.7/§12 and cannot be aggregated. Otherwise the first status present in this most-restrictive-first precedence is the aggregate:

```text
rejected
unsupported
contradicted
needs_clarification
partially_supported
inferred_but_acceptable
supported
```

Stage 7 is the only operation that may write this aggregate while the snapshot is current. Claim verification fields, the aggregate, and dependent branch/bullet supersession commit in one database transaction. Stage 7 and assessment export must reject a snapshot unless exactly one member claim is a `narrative_summary` whose claim text equals `AssessmentSnapshot.summary`. Every gated consumer must also reject a stored aggregate that does not equal a fresh reduction of the current claims. Manifest-backed managed-set removal follows §13's stale-export invalidation rule and cannot roll back that database state. Stage 10 initializes bullets to `unverified`, and Stage 11 alone assigns their semantic verdicts.

## §16.12 Generated-Voice Boundary

Verification has two orthogonal scopes:

1. Structural validation applies to every payload: required keys, field types, closed-enum values, typed-reference resolution, current/superseded constraints, §16.1 provenance chains, and §16.11 status semantics and allowlists. Natural-language origin never exempts malformed structure.
2. The natural-language rules in §16.2–§16.10 bind only Exp2Res-authored voice. By default this includes generated fact, claim, gap, contradiction, verifier, and bullet language from §15; system-authored report prose in §17; and generated bullet-pack prose in §18. §16.3, §16.9, and §16.10 use this boundary explicitly. For §16.4–§16.8, source text may be an evidence operand, but only the generated candidate phrase can violate the rule.

**Owner-referential scope of §16.2–§16.10.**

- The §16.2–§16.10 prohibitions are owner-referential: they constrain generated language that characterizes the owner — skill, experience, identity, health, impact — wherever it appears.
- A generated description of an external demand, such as §15.9 `ParsedJD` requirement, signal, keyword, or red-flag text, remains generated voice for structural validation and §15.9's parse-fidelity rules.
- Faithfully preserved demand wording ("expert Python", "production operations") characterizes the vacancy, not the owner; §16.3–§16.10 neither reject it nor force its rewriting.
- The moment any Exp2Res-authored text asserts that the owner meets a demand — in a bullet, claim, or report line — that assertion is owner-referential generated voice and every applicable rule binds in full.

**Source voice.**

- Source voice is owner or system-of-record material, not an Exp2Res claim.
- `RawLog.raw_text`, owner-captured gap-answer text, `JobDescription.raw_text`, imported artifact content, and natural-language values in §19 payloads receive structure-only validation at ingestion.
- Voice rules may consult them as evidence.
- Voice rules must never reject, rewrite, redact, normalize, or block their persistence because of their wording.
- A retained source may therefore contain flattery terms, permanent-identity wording, diagnostic language, metrics, production claims, or employment language without itself violating §16.

**Which segments are generated voice.**

- Every natural-language field emitted by an LLM is generated voice by default, including parser text, detector questions/descriptions, verifier counterevidence/reasons, warnings, and text that merely resembles a quotation.
- A rendered segment retains source voice only when its contract carries a typed source reference and the renderer verifies the segment byte-for-byte against the referenced persisted source value or a contiguous substring of it.
- Untagged, unresolved, normalized, or paraphrased text is generated voice.
- Validators scan every generated segment and only the structure around a validated source segment.
- Validators must not concatenate mixed-origin text and run a full-blob voice scan.

**Gap questions crossing into source context.**

- `GapQuestion.question` is generated voice and must pass §16 before Stage 4 persistence.
- At `gaps answer` capture, the service verifies that `RawLog.metadata.question_text` is an exact copy of that already validated question.
- Once copied into the owner-controlled raw record, the field is immutable source context for later extraction and is not rewritten or blocked by a later voice scan; this one-way handoff cannot admit unvalidated question text.
- In every case, a voice finding must never force a rewrite of owner memory or system-of-record material.

**Relation to the other subsections.**

- This subsection does not change §16.1 or any §16.11 status meaning, aggregation rule, or consumer allowlist.
- Voice compliance is a phrase/content check on generated candidates; status gates remain the independent permission layer for assessment and verified-bullet-pack consumers.

## §16.13 Language Scope

Except for the source-faithful mixed-language job-description fields below, V1 Exp2Res-authored natural-language output is English. This applies to every generated segment under §16.12, including facts, claims, questions, contradictions, verifier prose and warnings, generated bullets, and §17–§18 report and bullet-pack export prose. A non-English generated segment is a §16 voice violation evaluated at §16.12's segment boundary; mixed source/generated content is never concatenated to evade that check. The §16.3 anti-flattery term list and the §16.9/§16.10 phrase rules are specified and verified for English, which is the honest generated-language coverage boundary of V1.

Source voice may be in any language and remains byte-for-byte preserved under §16.12; it is never rejected, translated, normalized, or rewritten because of language. Cross-language extraction is in scope: Russian-language or other-language source text may produce English facts, claims, and other generated prose. Meaning-preserving translation occurs only inside generated voice, and it never weakens the evidence and overclaim rules in §16. A quoted source segment remains source voice only through §16.12's typed-reference and byte-for-byte check; an English paraphrase of a Russian source is generated voice.

For a mixed-language job description, §15.9 requirement and keyword text must preserve the vacancy's demand modality and meaning. Faithfully preserved demand wording may remain non-English; it remains generated voice for structural validation and §15.9 parse fidelity and characterizes the vacancy under §16.12. Every Exp2Res-authored assertion about the owner remains English and fully bound by §16.2–§16.10.

**Script fidelity for source-named tokens.** Cross-language extraction carries proper nouns across the language boundary: a generated segment that names a source-named entity — a project label, product or organization name, acronym, or identifier — reproduces the source's spelling in its source script, compared under NFC; byte-exactness remains the stricter standard reserved for §16.12 source-voice excerpts, since generated prose is NFC-normalized at export (§13.12, §17). Transliteration, romanization, or per-character script substitution of such a token is out of form: the result matches neither the source spelling nor any recognized name, so search, deduplication, and §11's comparison identity all fail on it. This is a semantic rule of generated voice, judged like the other §16 rules at §16.12's segment boundary.

Its deterministic half is the **mixed-script tripwire**, part of §15.1 structured-output validation. Tokenization and script classes are closed and Unicode-version-independent: the Latin letter class is exactly U+0041–U+005A, U+0061–U+007A, U+00C0–U+00D6, U+00D8–U+00F6, and U+00F8–U+024F; the Cyrillic letter class is exactly U+0400–U+052F; the continuation class is exactly the combining marks U+0300–U+036F, which join a token without carrying a script. A token is a maximal run of code points from those three classes; every other code point — including digits, hyphens, and other punctuation — terminates it, so a hyphenated construct such as a source's "Кафка-consumer" is two single-script tokens, not a mixed one. A **mixed-script token** is a token containing letters of both classes. Every mixed-script token in a model-authored response string must equal, under NFC, a complete mixed-script token obtained by the same tokenization from the strings of that call's serialized typed input. Tokenization reads each string's code points as written — normalization never runs before tokenization, since a composition leaving the closed classes would split the run and hide a mixed token — and NFC applies only to the token-identity comparison. A mixed-script token that occurs nowhere in the input is model-invented, and the response is invalid structured output on the §15.1 retry path, with a content-free diagnostic naming the field location and a stable code, never the token bytes. The input-relative test is sound because every legitimate mixed token in a response is a copy from input — a preserved source token, a verifier quote of a supplied candidate phrase, and a cited source excerpt all arrive in the call's input — while a transliteration or confusable mutant is by construction absent from it. The tripwire binds model responses only: it is not a §11 hydration or boundary rule, never rejects a stored row, and never evaluates owner or source text, which keep §16.12's byte-for-byte protection. The Latin/Cyrillic pair is V1's honest deterministic coverage boundary; a mix involving any other script remains governed by the semantic rule above.

Localized or multilingual generated output is explicitly deferred beyond V1.

## §16.14 Owner-Reference Rule

Owner-referential generated voice under §16.12 refers to the owner in the second person — "you", "your" — or names no subject at all. Subject-free phrasing is always legal and is the expected form for fact and bullet prose, which state what was done rather than who did it; §18 bullet prose stays subject-free because an external reader consumes it. Where a generated segment needs a referring expression for the owner — a claim, gap question, contradiction description, or verifier counterevidence statement — that expression is second person. The mirror addresses its owner; it does not narrate a case study about a third party.

Third-person owner reference in generated voice is forbidden: third-person pronouns standing for the owner, role nouns standing for the owner — "the user", "the subject", "the author", "the owner", "the developer", "the candidate" — and the owner's personal name. Those nouns are examples: any generated wording whose referent is the owner and whose grammatical person is third is out. First-person owner voice — generated prose that speaks as the owner, "I", "my" — is equally outside the form: generated voice never puts words in the owner's mouth. A generated segment that refers to the owner outside the second-person-or-subject-free form — in the first or third person or by name — is a §16 voice violation evaluated at §16.12's segment boundary; when Stage 7 verifies a claim candidate that carries one, the verdict is `rejected` under §16.11 — a phrasing violation requiring replacement, not a qualifiable weakness.

The rule is owner-referential in exactly §16.12's sense, so nothing outside that scope changes. Source voice keeps its wording byte-for-byte in any person, including the owner's name; a §16.12 byte-verified source segment never violates this rule, and a voice finding never rewrites owner memory. Faithfully preserved demand wording keeps the vacancy's own nouns — a job description's "the candidate must" characterizes the vacancy, not the owner (§15.9). Third-person nouns whose referent is not the owner remain legal in generated voice: a claim about software the owner shipped may describe that software's users. A verifier's verbatim quote of a violating candidate phrase — §15.5's `unsupported_phrases` or a finding's reason — mentions the candidate's wording as diagnostic evidence rather than referring to the owner; it neither violates this rule nor licenses new owner-referential prose around it. That referent ambiguity — "the user" may denote the owner or a product's user — is why V1 enforcement is semantic (§15.1 rule 10's instruction binding, §13.7's Stage 7 judgment) rather than a deterministic phrase gate or a §16.3-style closed term list.

This rule binds every generation and verification call from adoption forward — §15.1 rule 10 covers every §15 contract's generated output at emission, and §16.12's segment boundary covers persistence-time voice checks. What it does not do is rewrite stored history: a persisted verdict is the immutable record of the rule set its Stage 7 run applied, and a pre-adoption verdict keeps that recorded status until the owner's next §13.7 re-verification — which always applies the current rules and supersedes dependent state through §13.7's own invalidation. No upgrade gate retroactively invalidates derived state over phrasing form. The same stored-history semantics covers §13.4's equal-key retention: a post-adoption rerun constrains the candidate it emits, while the retention decision may keep pre-adoption prose current until a replacing generation; making prompt-policy identity a retention condition is a §13.4 lifecycle decision this rule does not take.

---
