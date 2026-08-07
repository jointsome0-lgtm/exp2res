# Exp2Res — System Design Document

**Version:** 0.3  
**Status:** Implementation-ready  
**Project:** Exp2Res — Experience to Self-Assessment to Verified Bullet Pack
**Primary goal:** Build a local-first, provenance-heavy self-assessment system from owner-controlled experience evidence that automation cannot rewrite.
**Secondary goal:** Generate job-targeted verified bullet packs from the same evidence model without unsupported claims; a full resume document model is deferred to a post-mirror iteration.
**Primary user:** The developer using the system to understand himself honestly, orient through real experience, and optionally export a verified bullet pack for a specific vacancy.

---

## § Index

Section numbers are stable: issues and the Decision Log cite them as `§13` / `§13.2`. Never renumber. New sections take the next free number or a sub-number; update this index when sections change. Retired numbers are never reused: §12.1–§12.3, §12.5, §12.6, §12.9, §12.12 (2026-07-04, derivable tables replaced by §12's derivation rules from §11); §12.7, §12.8, §12.10, §12.11 (2026-07-04, issue #2 — Contradiction, GapQuestion, JobDescription, ResumeBranch got §11 models, their DDL is now derived); §13.2, §13.9 (2026-07-11, issue #19 — evidence recording folded into §13.1 and relevance matching into §13.10); §11.5, §13.5, §14.8, §15.3, §23.3 (2026-08-05, issue #76 — the persisted signal layer removed; pattern extraction moved inside §15.4 as required non-persisted output).

Layout: this file is the map. Each top-level § lives in `spec/NN-slug.md` (file name starts with the § number); a § may own an authored canon artifact beside its file (for example `spec/21-evals-cases.toml`) — normative spec text linked from the § file and named in its map line; the Decision Log lives in `DECISION-LOG.md`. Point reads: open the § file plus every canon artifact it links. Full pass: read `spec/` files in index order, including authored canon artifacts.

Map-line budget: each index line below is a router — the §'s role, the few terms that distinguish it from its neighbors, and the name of any authored canon artifact the § owns — at most 250 characters. A map line changes only when its routing content goes stale — the §'s role, a distinguishing term, or the set of owned canon artifacts changes — not on every § edit (this narrows the 2026-07-04 same-commit map-line rule).

Rule-ordinal stability: rule ordinals inside a §'s numbered lists (cited as `§13.3 rule 10`) are stable anchors exactly like § numbers — never renumbered, merged, split, or mid-inserted, and an ordinal never changes meaning; a new rule appends at the end of its list, and a retired ordinal is never reused.

- §0 Historical Change Note — recentering: mirror first, resume is a secondary export
- §1 Executive Summary — evidence → facts → assessment → single-pass verifier gates → optional verified-bullet-pack export, with a full resume document deferred post-mirror
- §2 Product Framing — weak framings to avoid; mirror-first framing and the V1 verified-bullet-pack boundary
- §3 Core Purpose — orientation, not impressiveness
- §4 Goals and Non-Goals — product/cognitive goals, V1 verified-bullet-pack scope and closed companions, deferred full resume document model, and forbidden inflations
- §5 Core Principles — truth over comfort; owner-controlled, automation-append-only raw records with owner deletion authority; recorded_at ≠ occurred_at; no precision/ownership inflation; contradictions first-class; no automatic semantic promotion
- §6 System Boundaries — relations to Tick-like activity intake, Atlas knowledge-state snapshots, upstream-acquired GitHub commit records, and the verified bullet-pack export projection
- §7 High-Level Architecture — pipeline diagram
- §8 Runtime Architecture — Python, Typer, SQLite, Pydantic, CLI-first; §8.1 one-business-writer/many-reader workspace locking, WAL discipline, secure_delete; database compatibility and migration owned by §12.14
- §9 Domain Model — ontology, raw-versus-derived lifecycle, claim kinds; §9.2 confidence layers, §9.3 evidence strength, §9.4 evidence-to-confidence calibration including the owner-locator trust position
- §10 Enumerations — canonical Literal aliases: confidence orders, evidence-strength values, Atlas snapshot values, verifier targets, JD requirement kinds, GitHub owner attribution, managed-output kinds, CLI result statuses
- §11 Pydantic Domain Models — strict extra-forbid validation policy with boundary size/text/datetime/path limits and Unicode identity; one model per entity, RawLog through VerificationFinding, with field authorship and lifecycle-only mutation
- §12 SQLite Schema — §11-derived DDL rules: ISO 8601 TEXT datetimes, entity-table TEXT PRIMARY KEY identity, provenance and correction-lineage columns; normative DDL for fact_sources, processing_runs, llm_calls; §12.14 schema_meta and migration
- §13 Pipeline Specification — the nine active stable-number stages, raw capture and correction displacement through extraction, detection, assessment, verification, JD parsing, and export; §13.13 recompute lifecycle, §13.14 managed-output writer
- §14 CLI Specification — sole command-form authority for every workspace command, capture through export and inspection; §14.14 global runtime contract: result envelope, exit taxonomy, configuration precedence, workspace timezone
- §15 LLM Contracts — per-stage extractor, writer, verifier, detector, and parser I/O contracts; §15.10 transport/budget/cancellation, §15.11 field-ownership matrix, §15.12 isolated agent-runner protocol, §15.13 closed V1 adapter lineup
- §16 Verification Rules — evidence, mirror, anti-flattery, ownership, metric, production, temporal, employment, identity, diagnostic; §16.11 status semantics and gates, §16.12 generated-voice boundary, §16.13 language scope, §16.14 owner reference
- §17 Self-Assessment Report Format — the mirror report's fixed section model with deterministic claim selection and ordering, rendered as a byte-identical canonical Markdown member plus a self-contained inert HTML member, under closed escaping rules
- §18 Verified Bullet-Pack Export Rules — persisted bullets plus §13.12 closed companions, required assessment anchoring, typed JD requirement references, status allowlists, export-fail conditions; full resume document model deferred
- §19 Integration Contracts — activity-domain intake, Atlas knowledge-state snapshots, GitHub commits; §19.4 source-local records with per-source identity, idempotent duplicates, importer-computed hashes, independent per-record processing
- §20 Suggested Repository Structure — placement principles + normative skeleton
- §21 Evals — 60 behavioral Given/When/Then tests with stable `## §21.N` identities; each case body lives in the CI-validated authored canon artifact `spec/21-evals-cases.toml`
- §22 Implementation Plan — Phases 0–5 with definitions of done naming offline test groups; the §15.12 agent-runner boundary lands in Phase 1, the verified bullet pack in Phase 4
- §23 End-to-End Demo — approximate-range retro log → complete typed facts → claim → complete two-bullet verified pack plus closed companions
- §24 Acceptance Criteria — 60 V1 pass/fail checks under frozen item numbers, spanning capture, pipeline, CLI runtime, managed exports, integrations, privacy lifecycles, security and injection, concurrency, identity, and migration
- §25 Risks and Mitigations — reference-only mitigations: resume drift, flattery, punitive tone, overclaim, integration pollution, diagnosis, and retained private or stale derivations
- §26 README Positioning — verified-bullet-pack intro and taglines; MIT license, stage-tracking README status per §22 phase progress, and deferred security-policy positioning
- §27 Key Invariants — reference-only index including typed JD/unknown references, occurred provenance, generated/source voice boundaries, and the local privacy/egress boundary
- §28 Final Design Statement — three layers that must never collapse
- §29 Security and Privacy — local canonical boundary and private default; authorized outward transit and provider trust, exhaustive LLM transmission surface, secret/ignore/prompt isolation, prompt-injection bounds, lifecycle limits, change control
- §30 Local Views — loopback-URL-only selfos embedding: the §14.17-served read-only mirror over §17's revalidated HTML member plus the unanswered-gap-question projection, one closed route and outcome set, and the deferred JD-to-bullet-pack view
- Decision Log — dated one-line decisions with rejected alternatives

---
