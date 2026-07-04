# Exp2Res — System Design Document

**Version:** 0.2  
**Status:** Draft / implementation-oriented  
**Project:** Exp2Res — Experience to Self-Assessment to Resume  
**Primary goal:** Build a local-first, provenance-heavy self-assessment system from immutable experience evidence.  
**Secondary goal:** Generate job-targeted resume exports from the same evidence model without unsupported claims.  
**Primary user:** The developer using the system to understand himself honestly, orient through real experience, and optionally export a truthful resume for a specific vacancy.

---

## § Index

Section numbers are stable: issues and the Decision Log cite them as `§13` / `§13.2`. Never renumber. New sections take the next free number or a sub-number; update this index when sections change. Retired numbers are never reused: §12.1–§12.3, §12.5, §12.6, §12.9, §12.12 (2026-07-04, derivable tables replaced by §12's derivation rules from §11); §12.7, §12.8, §12.10, §12.11 (2026-07-04, issue #2 — Contradiction, GapQuestion, JobDescription, ResumeBranch got §11 models, their DDL is now derived).

Layout: this file is the map. Each top-level § lives in `spec/NN-slug.md` (file name starts with the § number); the Decision Log lives in `DECISION-LOG.md`. Point reads: open the § file. Full pass: read `spec/` files in index order.

- §0 Change From v0.1 — recentering: mirror first, resume is a secondary export
- §1 Executive Summary — evidence → facts → signals → assessment → optional exports
- §2 Product Framing — weak framings to avoid; strong framing
- §3 Core Purpose — orientation, not impressiveness
- §4 Goals and Non-Goals — product/cognitive goals; forbidden inflations
- §5 Core Principles — truth over comfort; append-only; recorded_at ≠ occurred_at; no precision/ownership inflation; contradictions first-class; no automatic semantic promotion
- §6 System Boundaries — relations to Tick-like, Atlas, GitHub, resume export
- §7 High-Level Architecture — pipeline diagram
- §8 Runtime Architecture — Python, Typer, SQLite, Pydantic; CLI-first
- §9 Domain Model — ontology, claim kinds, confidence layers, evidence strength
- §10 Enumerations — Literal types: temporal, entry/source, ownership, context, claims, verification, entity refs, gap triggers
- §11 Pydantic Domain Models — OccurredAt, RawLog, EvidenceItem, ExperienceFact, SelfSignal, SelfClaim, AssessmentSnapshot, ResumeBullet, Contradiction, GapQuestion, JobDescription, ResumeBranch
- §12 SQLite Schema — derivation rules from §11 models; normative DDL only for the storage artifacts fact_sources and processing_runs
- §13 Pipeline Specification — 12 stages: capture → normalize → extract → gaps → signals → assess → verify → jd → match → generate → verify → export
- §14 CLI Specification — init, log, correction, import, extract, gaps, signals, assess, jd/match/resume/verify/export
- §15 LLM Contracts — structured I/O for extractor, signal extractor, assessment writer/verifier, resume writer/verifier
- §16 Verification Rules — evidence, mirror, anti-flattery, ownership, metric, production, temporal, employment, identity, diagnostic
- §17 Self-Assessment Report Format — mirror report skeleton and tone
- §18 Resume Export Rules — pipeline and export-fail conditions
- §19 Integration Contracts — Tick-like / Atlas / GitHub import behavior
- §20 Suggested Repository Structure — placement principles + normative skeleton
- §21 Evals — 10 behavioral tests against overclaiming
- §22 Implementation Plan — Phase 0–5 with definitions of done
- §23 End-to-End Demo — retro log → facts → signal → claim → verified bullet
- §24 Acceptance Criteria — 14 V1 checks
- §25 Risks and Mitigations — resume-drift, flattery, punitive tone, overclaim, integration pollution, diagnosis
- §26 README Positioning — intro and taglines
- §27 Key Invariants — the non-negotiables list
- §28 Final Design Statement — three layers that must never collapse
- Decision Log — dated one-line decisions with rejected alternatives

---

