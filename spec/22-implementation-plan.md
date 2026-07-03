## §22. Implementation Plan

Phases sequence the pipeline stages of §13. Commands per phase are specified in §14, tables in §12, models in §11, LLM contracts in §15.

| Phase | Pipeline stages (§13) | Definition of done |
|-------|-----------------------|--------------------|
| 0 — Skeleton | Runtime skeleton (§8, §14.1) + Stage 1, manual capture only | Local database can be created; daily and retrospective logs can be added and inspected. |
| 1 — Evidence and Fact Extraction | Stages 2–3 | Raw logs become atomic facts with source_log_ids; no fact can exist without a source. |
| 2 — Gaps and Contradictions | Stage 4 + correction flow (§5.3, §14.4) | Weak facts generate useful questions; contradictions are stored, not hidden; corrections append new records. |
| 3 — Self-Signals and Assessment | Stages 5–7 + assessment export (Stage 12) | System produces an evidence-backed self-assessment with strengths, gaps, contradictions, unknowns, and evidence map. |
| 4 — Resume Export | Stages 8–12 | System generates a job-targeted Markdown resume with evidence map and verification report; unsupported bullets are blocked. |
| 5 — Integrations | Stage 1 importers (§19) | External evidence can enter as raw logs/evidence items without automatic overclaiming. |

---

