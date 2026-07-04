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
