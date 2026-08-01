## §22. Implementation Plan

Phases sequence the pipeline stages of §13. Commands per phase are specified in §14, tables in §12, models in §11, LLM contracts in §15. From the first model-backed slice (Phase 1) onward, an agent-backed adapter executes every §15 call only through the versioned §15.12 isolated agent-runner protocol; that runner boundary is part of each LLM-backed phase's definition of done.

Each phase below states the §13 pipeline stages it builds, its definition of done, and the offline test groups that prove that definition:

- **Phase 0 — Skeleton**
  - Pipeline stages (§13): runtime skeleton (§8, §14.1) + Stage 1, manual capture only.
  - Definition of done:
    - Local database can be created with its schema version recorded at initialization.
    - Incompatible workspaces fail closed before business I/O.
    - Every available command satisfies §14.14's workspace, configuration, non-interactive, exit-code, and JSON-envelope contract.
    - Daily and retrospective logs can be added with linked `manual_claim` evidence items and inspected.
    - Automation cannot rewrite those logs.
    - Owner deletion cannot be FK-blocked.
  - Offline DoD groups: model/schema unit and lifecycle tests for the skeleton and Stage 1.

- **Phase 1 — Fact Extraction**
  - Pipeline stages (§13): Stage 3 (evidence items are persisted by Stage 1).
  - Definition of done:
    - Correction lineages become atomic current facts with evidence-backed §12.4 rows.
    - Multiple items from one raw log remain representable.
    - Reruns replace rather than duplicate the current generation.
  - Offline DoD groups: model/schema unit, lifecycle, fake-runner contract, and property/parameterized invariant tests for Stage 3.

- **Phase 2 — Gaps and Contradictions**
  - Pipeline stages (§13): Stage 4 + correction/recompute flow (§5.3, §13.13, §14.4/§14.12).
  - Definition of done:
    - Weak facts generate useful questions.
    - Contradictions are immutable complete-generation detections, not hidden or transitioned in place.
    - Corrections append linked records and rebuild the current Stage 3–4 state (the flow extends through Stage 5 with view reporting when Phase 3 lands).
  - Offline DoD groups: model/schema unit, lifecycle, fake-runner contract, and property/parameterized invariant tests for Stage 4 and correction/recompute.

- **Phase 3 — Self-Signals and Assessment**
  - Pipeline stages (§13): Stages 5–7 + assessment export (Stage 12) + the §14.17 global view-serving slice.
  - Definition of done:
    - System transactionally rejects every missing/wrong typed reference.
    - System produces one coherent current evidence-backed assessment per explicitly generated view.
    - Corrected snapshots remain inspectable superseded history.
    - Owner deletion purges all generations, rebuilds Stages 3–5 from retained raw records, and reports purged views for explicit §14.9 regeneration.
    - The Phase 3 view sub-slice serves §30's global mirror and gap-question routes through `view serve` and satisfies pending §24.59/§21.57, without adding the deferred project-scoped or JD-to-bullet-pack views.
  - Offline DoD groups: model/schema unit, lifecycle, fake-runner contract, property/parameterized invariant, golden assessment-export, and deterministic loopback serving tests for Stages 5–7, assessment export, and §30's transport, projection, revalidation, concurrency, and interruption contracts.

- **Phase 4 — Verified Bullet-Pack Export**
  - Pipeline stages (§13): Stages 8 and 10–12.
  - Definition of done:
    - System generates a deterministic job-targeted verified bullet pack from one exact eligible assessment snapshot with the closed versioned §13.12 companions.
    - Status-ineligible or superseded snapshots, claims, and bullets are blocked.
    - Lifecycle changes invalidate managed exports.
    - A full resume document model remains deferred post-mirror.
  - Offline DoD groups: model/schema unit, lifecycle, fake-runner contract, property/parameterized invariant, and golden export tests for Stages 8 and 10–12.

- **Phase 5 — Integrations**
  - Pipeline stages (§13): Stage 1 importers (§19).
  - Definition of done:
    - External evidence can enter as raw logs and evidence items without automatic overclaiming.
  - Offline DoD groups: model/schema unit, lifecycle, and property/parameterized invariant tests for §19 importers.

---
