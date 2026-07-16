## §21. Evals

The `## §21.N` headings below are the stable eval identities: issues, the Decision Log, `tests/coverage_map.toml`, and `tests/acceptance_ledger.toml` cite them, and they are never renumbered. Each eval's Given/When/Then case body lives under its ID in the authored canon artifact [`spec/21-evals-cases.toml`](21-evals-cases.toml); every artifact case is normative acceptance — reviewed spec text with the same authority as this file, never a projection of implementation code. An `Enforces` line under a heading names the rules that eval enforces and mirrors the artifact's `enforces` value. A new eval adds its stable heading here, its case in the artifact, and its coverage-map row; `scripts/check_eval_cases.py` validates in offline CI that the headings here, the artifact's cases, and `tests/coverage_map.toml` correspond one-to-one, and that this file carries no fenced case bodies.

## §21.1 No Unsupported Self-Claim

Enforces §16.3.

## §21.2 No Automatic Skill From Tick-like

## §21.3 Atlas Snapshot Does Not Equal Mastery

## §21.4 No Hidden Contradiction

## §21.5 No Invented Metrics

## §21.6 No Ownership Upgrade

## §21.7 Temporal Precision Preservation

## §21.8 No Diagnostic Labels

## §21.9 Resume Requires Evidence

## §21.10 Assessment Requires Evidence

## §21.11 Raw-Layer Authority Is Actor-Scoped

## §21.12 Re-Extraction Has One Current Generation

## §21.13 Correction Recomputes the Current Model

## §21.14 Owner Deletion Is a Privacy Reset

## §21.15 Provenance References Resolve at Write Time

## §21.16 Verification Statuses Are Allowlisted

## §21.17 Bullet Generation Has One Exact Snapshot Anchor

## §21.18 V1 Review Is Verifier Gating

## §21.19 Contradictions Are Immutable Generation Outputs

## §21.20 Verification Does Not Imply Automatic Repair

## §21.21 Occurred Provenance Is Conservative

## §21.22 Typed JD Requirement References

## §21.23 Stage 4 Contract Is Complete and Schema-Only Retried

## §21.24 Stage 8 Contract Persists Only Typed ParsedJD

## §21.25 Voice Rules Bind Generated Text, Not Source Text

## §21.26 Assessment Unknowns and Counterevidence Surface Without a Gate Bypass

## §21.27 Generated Employment Framing Is Rejected

Enforces §16.8.

## §21.28 Permanent Identity Claims Are Rejected

Enforces §16.9.

## §21.29 Evidence-Grounded Mirror Prose Passes Unchanged

Enforces §16.2.

## §21.30 Instruction-Like Job-Description Text Is Data

Enforces §29.5.

## §21.31 Confidence Is Calibrated, Never Authorized

## §21.32 Assessment Verifier Receives the Exact Provenance Closure

## §21.33 Assessment Scope Selects Deterministically and Views Replace by Identity

## §21.34 Managed Exports Are ID-Keyed and Manifest-Identified

## §21.35 Entity Identity Is Unique, Immutable, and Never Reused

## §21.36 Schema Compatibility and Migration Are Fail-Closed

Enforces §12.14, §13.13, and §14.1.

## §21.37 Concurrent Processes Cannot Corrupt the Workspace

Enforces §8.1, §12 rule 12, §13, and §14.

## §21.38 Every Derived Row Resolves to Its Producing Run and Generation

Enforces §11.14, §12 rule 13, §12.13, §12.15, §13.7, §13.11, §13.13, and §14.13.

## §21.39 Boundaries Are Strict, Typed, and Bounded

Enforces §11's Model validation policy, §12 rule 2, §15.1, §19, and §29.4.

## §21.40 Correction Displacement Is Computable and Lossless

Enforces §9.4, §12.4, §13.3–§13.4, §13.7, §13.10, §14.4, §15.2, §15.5–§15.8, and §29.3.

## §21.41 CLI Runtime Contract Is Deterministic and Machine-Readable

Enforces §8.1, §12.14, §13.13, §14.1, §14.14, §15.10, §16.11, and §29.

## §21.42 Temporal, Language, Unicode, and Path Semantics Are Deterministic

Enforces §11's Model validation policy and §11.1, §12 rule 3, §13.6 and §13.12, §14.9–§14.10 and §14.14 rule 8, §15.1–§15.2, §16.6 and §16.12–§16.13, §19, and §29.4; extends §21.39's canonical-hash coverage.

## §21.43 LLM Transport Is Bounded, Foreground, and Fail-Closed

Enforces §8.1, §12 rule 13, §12.13, §12.15, §13.3, §13.7, §13.10–§13.11, §13.13, §14.14, §15.1, §15.10, §29.2, and §29.4.

## §21.44 JD Deletion and Workspace Purge Are Complete Privacy Operations

Enforces §8.1, §13.13 rules 5–6 and 10, §14.14–§14.16, and §29.2 and §29.6.

## §21.45 Integration Imports Are Versioned, Idempotent, and Atomic

Enforces §8.1, §10's `OwnerAttribution`, §11's Model validation policy, §13.1 rule 5, §14.5, §14.14 rule 5, §19.1–§19.4, and §29.4–§29.5; extends §21.39's boundary coverage.

## §21.46 Domain-Routed Imports and Local Views Preserve Authority Boundaries

Enforces §5.10, §6.1–§6.2, §9.4, §10, §13.12, §13.14, §14.5, §14.7, §14.10, §14.14, §16.11, §17–§18, §19.1–§19.2, §19.4, §25.5, §29.2–§29.3, and §30.

## §21.47 Managed-Output Publication Is Atomic, Manifest-Gated, and Contained

Enforces §8.1, §11, §13.12–§13.14, §14.14, and §29.2.

## §21.48 Stage 12 Exports Are Closed, Byte-Identical, and Evidence-Complete

Enforces §5.5, §11, §13.10, §13.12, §13.14, §14.10, §14.14 rule 5, §15.6, §16.7, §16.11, and §17–§18.

## §21.49 Prompt-Injection Threat-Path Matrix

Enforces §11's field-authorship policy, §12.13 and §12.15, §13.3 rule 10, §14.5 and §14.14, §15.1–§15.2 and §15.5–§15.10, §16.3–§16.8 and §16.12–§16.13, §17–§18, and §29.4–§29.5; extends §21.30's JD instruction isolation and §21.39's strict-boundary coverage.

## §21.50 Agent-Backed Runners Are Structurally Confined

Enforces §15.10 rule 4, §15.12, §29.2, §29.4, and §29.6; extends §21.43's transport coverage and §21.49's injection matrix.

---
