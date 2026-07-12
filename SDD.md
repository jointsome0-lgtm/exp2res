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
- §5 Core Principles — truth over comfort; raw records append-only to automation and deletable by their owner; correction/recompute lifecycle; recorded_at ≠ occurred_at; no precision/ownership inflation; contradictions first-class; explicit promotions and verifier-gated external projections
- §6 System Boundaries — relations to Tick-like, Atlas, GitHub, resume export
- §7 High-Level Architecture — pipeline diagram
- §8 Runtime Architecture — Python, Typer, SQLite, Pydantic; CLI-first
- §9 Domain Model — ontology, actor-scoped raw immutability, current versus superseded derived generations, snapshot-anchored resume branches, claim kinds, confidence layers, evidence strength, and evidence-to-confidence calibration
- §10 Enumerations — canonical Literal aliases plus temporal and general confidence orders and typed JD requirement kinds
- §11 Pydantic Domain Models — lifecycle-managed entities, typed assessment scope/unknown/counterevidence storage, exact resume provenance, and ParsedJD requirements with stable IDs
- §12 SQLite Schema — derived typed JSON storage, normalized fact provenance, and transactional validation of snapshot, counterevidence, JD-requirement, and resume references; normative DDL only for fact_sources and processing_runs
- §13 Pipeline Specification — 10 active stable-number stages with conservative occurred provenance, LLM-backed Stage 4/8 contracts, fact-mediated gap answers, typed assessment content, snapshot-anchored resume generation, and Stage 3–5 lifecycle recompute with explicit view regeneration
- §14 CLI Specification — sole command-form authority; init, capture/local-payload import, owner deletion, extract/recompute, generation with explicit selectors and persisted project scope targets, verifier findings, export
- §15 LLM Contracts — full structured extractor/writer/verifier plus Stage 4 detector and Stage 8 parser I/O; schema retry is distinct from semantic results
- §16 Verification Rules — evidence/status gates and generated-voice-scoped mirror, anti-flattery, ownership, metric, production, temporal, employment, identity, and diagnostic rules
- §17 Self-Assessment Report Format — status-labeled mirror with typed scope/unknown/counterevidence rendering, complete contradictions, and origin-aware prose
- §18 Resume Export Rules — required assessment anchoring, typed JD requirement references, voice-scoped prose, status allowlists, and export-fail conditions
- §19 Integration Contracts — Tick-like / Atlas / GitHub import behavior with structure-only validation of system-of-record prose
- §20 Suggested Repository Structure — placement principles + normative skeleton
- §21 Evals — 34 behavioral tests including temporal provenance, typed JD references, Stage 4/8 contract discipline, voice scoping, typed assessment uncertainty, verifier input closure, deterministic scope selection, generated employment/identity/mirror behavior, and instruction-like JD isolation
- §22 Implementation Plan — Phase 0–5 with definitions of done
- §23 End-to-End Demo — approximate-range retro log → complete typed facts → signal → claim → verified bullet
- §24 Acceptance Criteria — 37 V1 checks
- §25 Risks and Mitigations — resume-drift, flattery, punitive tone, overclaim, single-pass verifier gates, provenance corruption, verifier-gated integration projections, diagnosis, retained private/stale derivations
- §26 README Positioning — intro and taglines
- §27 Key Invariants — reference-only index including typed JD/unknown references, occurred provenance, generated/source voice boundaries, and the local privacy/egress boundary
- §28 Final Design Statement — three layers that must never collapse
- §29 Security and Privacy — private local canonical state, exhaustive user-initiated LLM transit, provider trust, secret/ignore isolation, prompt-injection bounds, and residual risks
- Decision Log — dated one-line decisions with rejected alternatives

---
