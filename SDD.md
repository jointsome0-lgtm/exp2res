# Exp2Res — System Design Document

**Version:** 0.2  
**Status:** Draft / implementation-oriented  
**Project:** Exp2Res — Experience to Self-Assessment to Resume  
**Primary goal:** Build a local-first, provenance-heavy self-assessment system from owner-controlled experience evidence that automation cannot rewrite.
**Secondary goal:** Generate job-targeted resume exports from the same evidence model without unsupported claims.  
**Primary user:** The developer using the system to understand himself honestly, orient through real experience, and optionally export a truthful resume for a specific vacancy.

---

## § Index

Section numbers are stable: issues and the Decision Log cite them as `§13` / `§13.2`. Never renumber. New sections take the next free number or a sub-number; update this index when sections change. Retired numbers are never reused: §12.1–§12.3, §12.5, §12.6, §12.9, §12.12 (2026-07-04, derivable tables replaced by §12's derivation rules from §11); §12.7, §12.8, §12.10, §12.11 (2026-07-04, issue #2 — Contradiction, GapQuestion, JobDescription, ResumeBranch got §11 models, their DDL is now derived); §13.2, §13.9 (2026-07-11, issue #19 — evidence recording folded into §13.1 and relevance matching into §13.10).

Layout: this file is the map. Each top-level § lives in `spec/NN-slug.md` (file name starts with the § number); the Decision Log lives in `DECISION-LOG.md`. Point reads: open the § file. Full pass: read `spec/` files in index order.

- §0 Historical Change Note — recentering: mirror first, resume is a secondary export
- §1 Executive Summary — evidence → facts → signals → assessment → single-pass verifier gates → optional exports
- §2 Product Framing — weak framings to avoid; strong framing
- §3 Core Purpose — orientation, not impressiveness
- §4 Goals and Non-Goals — product/cognitive goals; forbidden inflations
- §5 Core Principles — truth over comfort; raw records append-only to automation and deletable by their owner; whole-record correction displacement and recompute lifecycle via §13.3 effective-set semantics; recorded_at ≠ occurred_at; no precision/ownership inflation; contradictions first-class; explicit promotions and verifier-gated external projections
- §6 System Boundaries — relations to Tick-like, Atlas, GitHub, resume export
- §7 High-Level Architecture — pipeline diagram
- §8 Runtime Architecture — Python, Typer, SQLite, Pydantic; CLI-first, with a one-business-writer/many-reader workspace advisory lock, WAL transaction discipline, and database compatibility and migration owned by §12.14
- §9 Domain Model — ontology, actor-scoped raw immutability, current versus superseded derived generations, snapshot-anchored resume branches, append-only verifier-attempt history, claim kinds, confidence layers, evidence strength, and evidence-to-confidence calibration with scoped displaced-record support for corrected imports
- §10 Enumerations — canonical Literal aliases plus temporal and general confidence orders, typed verifier targets, typed JD requirement kinds, and CLI result statuses
- §11 Pydantic Domain Models — canonical strict extra-forbid validation, field authorship, lifecycle-only mutation, and hash-serialization policy; bounded inert service/importer metadata; boundary size/count/depth and text-hygiene limits with offset-aware datetime acceptance; lifecycle-managed entities with service-assigned, opaque, immutable, non-reused per-table IDs; correction-link deletion re-rooting with §13.3 displacement semantics; storage-level production-provenance boundary; typed assessment scope/unknown/counterevidence storage; append-only VerificationFinding history; exact resume provenance; and ParsedJD requirements with stable IDs
- §12 SQLite Schema — per-entity-table `TEXT PRIMARY KEY` identity, derived typed JSON storage with same-policy/limit fail-closed hydration, normalized fact provenance, per-row producing-run and atomic-generation provenance, transactional reference and correction-lineage evidence-selectability validation, a partial unique exact-name backstop for current named resume branches behind Stage 10's case-folded replacement identity, and normative DDL for fact_sources, parent-linked privacy-safe processing-run execution identity with per-call llm_calls telemetry, and append-only schema_meta; fail-closed compatibility plus verified-backup, single-transaction migration
- §13 Pipeline Specification — 10 active stable-number stages with run/generation traceability, deterministic whole-record correction displacement, effective-record and per-fact governing-record resolution, scoped survival and universal prose-free §15 projection of non-manual displaced-record support, conservative occurred provenance, LLM-backed Stage 4/8 contracts, atomic durable verifier findings, fact-mediated gap answers, typed assessment content, snapshot-anchored resume generation, workspace-locked mutations and coupled managed-output work, Stage 3–5 lifecycle recompute grouped under non-stage 13.13 orchestration telemetry, and owner deletion of findings, managed exports, and migration backups with retained-telemetry content-hash redaction
- §14 CLI Specification — sole command-form authority with the §8.1 workspace business-writer/read-only classification and a global runtime contract for nearest-parent discovery, configuration precedence, non-interactive controls, stable exit taxonomy, and a versioned JSON result envelope; idempotent non-destructive init, pre-business-I/O compatibility, read-only database status, explicit migration, capture/local-payload import, self-contained correction capture with whole-record restatement framing, owner deletion, extract/recompute, generation with explicit selectors and persisted project scope targets, verifier findings, read-only run/finding inspection, export
- §15 LLM Contracts — full structured extractor/writer/verifier plus Stage 4 detector and Stage 8 parser I/O over effective correction-lineage content with universal prose-free displaced-record support descriptors and no displaced `RawLog` prompt objects; strict service-owned-field rejection, deterministic size/structure preflight, processing-run execution identity, findings-only advisory-rewrite persistence, and schema retry distinct from semantic results
- §16 Verification Rules — evidence/status gates and generated-voice-scoped mirror, anti-flattery, ownership, metric, production, temporal, employment, identity, and diagnostic rules
- §17 Self-Assessment Report Format — status-labeled mirror with typed scope/unknown/counterevidence rendering, complete contradictions, and origin-aware prose
- §18 Resume Export Rules — required assessment anchoring, typed JD requirement references, voice-scoped prose, status allowlists, and export-fail conditions
- §19 Integration Contracts — Tick-like / Atlas / GitHub import behavior with strict bounded/hygienic structural validation and unchanged system-of-record prose
- §20 Suggested Repository Structure — placement principles + normative skeleton
- §21 Evals — 41 behavioral tests including deterministic machine-readable CLI runtime behavior, computable whole-record correction displacement, scoped-support survival, and prose-free downstream projection, strict typed bounded transport/hydration behavior, producing-run and atomic-generation provenance, durable verifier history and orchestration parentage, concurrent-process workspace isolation, fail-closed schema compatibility and all-or-nothing migration recovery, unique immutable non-reused entity identity, temporal provenance, typed JD references, Stage 4/8 contract discipline, voice scoping, typed assessment uncertainty, verifier input closure, deterministic scope selection, generated employment/identity/mirror behavior, and instruction-like JD isolation
- §22 Implementation Plan — Phase 0–5 with definitions of done, including the global CLI runtime contract, schema-version initialization, and the pre-business-I/O compatibility gate
- §23 End-to-End Demo — approximate-range retro log → complete typed facts → signal → claim → verified bullet
- §24 Acceptance Criteria — 44 V1 checks including the deterministic global CLI runtime and machine-readable result contract, deterministic whole-record correction displacement, scoped-support survival, and prose-free downstream projection, one closed strict bounded interpretation per accepted object, producing-run/generation traceability, parent-linked lifecycle orchestration, durable verifier findings with telemetry-only failures, privacy-safe run identity, owner-deletion purge, one-writer/many-reader workspace locking, coherent snapshot reads, bounded contention and crash recovery, schema compatibility and verified atomic migration, and the per-table primary-key identity and no-reuse contract
- §25 Risks and Mitigations — resume-drift, flattery, punitive tone, overclaim, single-pass verifier gates, provenance corruption, verifier-gated integration projections, diagnosis, retained private/stale derivations
- §26 README Positioning — intro and taglines; MIT license and deferred security-policy positioning
- §27 Key Invariants — reference-only index including typed JD/unknown references, occurred provenance, generated/source voice boundaries, and the local privacy/egress boundary
- §28 Final Design Statement — three layers that must never collapse
- §29 Security and Privacy — private local canonical state, exhaustive displacement-aware user-initiated LLM transit, provider trust, size/structure and secret preflight, ignore isolation including descriptor locators, prompt-injection bounds, and residual risks
- Decision Log — dated one-line decisions with rejected alternatives

---
