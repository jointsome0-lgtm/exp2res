## Decision Log

Format: `YYYY-MM-DD — decision in one phrase; rejected alternative and why.`

- 2026-07-03 — Keep the SDD as a single file navigated via the § Index (map-as-interface);
  a physical split into a map file plus per-section files is deferred until after the
  structural dedup pass, and if done, must be a purely mechanical commit (concatenated
  section files must reproduce the original). Revisit triggers: code lands and sections
  graduate into living docs/tests; full-pass reviews start hitting context limits; parallel
  per-section editing becomes the norm; the file exceeds ~30–40K tokens despite dedup.
  Rejected alternative: splitting now — point reads already load only the needed section
  (§ Index + grep), a split does not fix the actual pain (cross-section duplication and
  drift), and splitting before dedup would migrate content about to be merged or deleted.
- 2026-07-04 — Structural dedup pass: §12 recognized as derived from §11 and replaced with
  derivation rules — with the exception of fact_sources and processing_runs, plus (found
  during the loss check) contradictions, gap_questions, job_descriptions, resume_branches,
  which also have no Pydantic counterpart and keep normative DDL; §22 compressed to a
  phase → §13-stages → definition-of-done table (commands live in §14; `exp2res logs list`
  rehomed to §14.11); anti-flattery mantras consolidated into §16 (+§5.10, §27) with
  one-line references from §4.3, §5.1, §15.4, §21.1, §25.2. Version not bumped — no
  decisions changed. Rejected alternative: leaving duplicates as-is — duplication cost is
  paid again in every polishing session and cross-copy drift risk grows.
- 2026-07-04 — Executed the deferred physical split (see 2026-07-03): `SDD.md` is now the map (§ Index + numbering rules), each top-level § lives in `spec/NN-slug.md`, decisions in `DECISION-LOG.md`; § numbering and all content unchanged — head + `spec/` files + log concatenate back to the pre-split file byte-for-byte (verified). Trigger: context root — file boundaries enforce the section-scoped reading the convention only requested (the exp2res monolith already exceeded the default 2000-line read window, silently truncating whole-file reads; split kept symmetric across SDD-stage repos). Rejected alternative: single file + convention — conventions don't bind agents, structure does.
- 2026-07-04 — Map aligned with the atlas pattern: § Index preamble gains "retired numbers are never reused" plus the registry (§12.1–§12.3, §12.5, §12.6, §12.9, §12.12 — the dedup's derivable tables); header Date field dropped (stale one day after writing — git is the date authority); henceforth a § edit updates its map line and status in the same commit. Rejected alternative: hand-maintained Date and per-§ retirement notes only — a duplicate of git metadata that had already drifted, and a registry readers can't find from the map.
- 2026-07-04 — §20 rewritten from a 112-line speculative file tree to placement principles + a normative skeleton (issue #1, the atlas §8 pattern): grep across spec/ showed no other § relies on any source path in the tree — the only load-bearing paths (out/*, .exp2res/, logs/) are runtime workspace owned by §14.1/§13.12 and were never in it, and docs/SDD.md was already falsified by the physical split. Skeleton keeps the root the repo already has plus the layer packages; module/test/example names now derive from §13, §21+§27, §19/§14 instead of being pre-named. Rejected alternative: keeping the full tree — it restates §13/§21/§27 at 112-line cost per full pass and drifts the moment code lands.
- 2026-07-04 — Issue #2: the four §9.1 ontology entities that had only DDL — Contradiction,
  GapQuestion, JobDescription, ResumeBranch — get Pydantic models (§11.9–§11.12) and §12 now
  derives their tables (§12.7, §12.8, §12.10, §12.11 retired); normative DDL remains only for
  the two non-ontology storage artifacts (fact_sources, processing_runs), making §12's
  exception list principled instead of enumerated. Grounds: they cross the LLM boundary
  (§15.3/§15.4 inputs, stage-4/8 outputs) where §15.1 mandates Pydantic validation; §11 already
  holds typed refs to them (AssessmentSnapshot.gap_question_ids/contradiction_ids,
  ResumeBullet.branch_id); and §5's "contradictions are first-class" is hollow if Contradiction
  is an entity without a domain model. Supporting changes: §10 gains EntityRefType and
  GapTrigger (now the canonical home of §13.4's trigger list); derivation rules gain
  bool→INTEGER and polymorphic-ref-without-FK; §11 preamble fixes its scope as "persisted
  ontology entities" and records why VerificationFinding stays contract-level (§15.5/§15.7,
  stored denormalized on its targets). Rejected alternative: declaring the four
  storage-level-only in §12 — saves no spec volume (models replace equal-sized DDL), leaves a
  third of the ontology without domain representation, and defers the same modeling work to
  implementation time where it would happen ad hoc and unreviewed.
- 2026-07-05 — Issue #24: §20 principle 3 points job descriptions to §13.8
  (Stage 8 — Job Description Parsing) instead of §13.11 (Resume Verification).
  Rejected alternative: leaving the pointer for readers to infer — it sends
  `examples/` population to the wrong pipeline stage.
- 2026-07-05 — Issue #6: §10 is the canonical, mechanically extractable home for every enum/Literal value list used by §11 persisted models, and §13/§27 are reference-only mirrors where they mention canonical lists or rules; §11 replaces inline Literals with named §10 aliases, §13.5/§13.6 point to the §10 alias plus §11 field instead of listing values, and §27 points to canonical §16 rule numbers with a same-commit sync rule. The shape is intentionally compatible with a post-MVP generated docs/schema/lint pass, but no generator, linter, separate registry file, runtime schema tooling, generated documentation, or MVP test infrastructure is introduced now. Version not bumped — no enum values, verifier behavior, schema fields, or export safety rules changed. Rejected alternative: leaving model-local literals or prose restatements — it preserves multiple homes for the same list and repeats the atlas drift failure; rejected alternative: implementing generated enum docs now — useful later, but premature before MVP and likely to turn an SDD dedup issue into tooling work.
