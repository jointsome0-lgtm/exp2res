## §22. Implementation Plan

Phases sequence the pipeline stages of §13. Commands per phase are specified in §14, tables in §12, models in §11, LLM contracts in §15.

| Phase | Pipeline stages (§13) | Definition of done |
|-------|-----------------------|--------------------|
| 0 — Skeleton | Runtime skeleton (§8, §14.1) + Stage 1, manual capture only | Local database can be created; daily and retrospective logs can be added with linked `manual_claim` evidence items and inspected; automation cannot rewrite them and owner deletion cannot be FK-blocked. |
| 1 — Fact Extraction | Stage 3 (evidence items are persisted by Stage 1) | Correction lineages become atomic current facts with evidence-backed §12.4 rows; multiple items from one raw log remain representable; reruns replace rather than duplicate the current generation. |
| 2 — Gaps and Contradictions | Stage 4 + correction/recompute flow (§5.3, §13.13, §14.4/§14.12) | Weak facts generate useful questions; contradictions are immutable complete-generation detections, not hidden or transitioned in place; corrections append linked records and rebuild current derived state. |
| 3 — Self-Signals and Assessment | Stages 5–7 + assessment export (Stage 12) | System transactionally rejects every missing/wrong typed reference and produces one coherent current evidence-backed assessment; corrected snapshots remain inspectable superseded history, while owner deletion purges all generations and rebuilds from retained raw records. |
| 4 — Resume Export | Stages 8 and 10–12 | System generates a job-targeted Markdown resume from one exact eligible assessment snapshot with evidence map and verification report; status-ineligible or superseded snapshots, claims, and bullets are blocked, and lifecycle changes invalidate managed exports. |
| 5 — Integrations | Stage 1 importers (§19) | External evidence can enter as raw logs and evidence items without automatic overclaiming. |

---
