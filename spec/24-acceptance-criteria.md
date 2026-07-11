## §24. Acceptance Criteria

V1 is acceptable when:

1. User can add daily and retrospective raw logs, each with a linked `manual_claim` evidence item created in the same operation.
2. User can import at least one external source as linked raw-log and evidence-item records.
3. Raw logs are append-only to automation, while the owner can hard-delete any raw log without an FK or rebuild failure blocking deletion.
4. Corrections are stored as self-contained new events linked to their targets; targets are not mutated.
5. Experience facts require at least one direct, non-null EvidenceItem-backed source row and derive their source-log IDs through those items.
6. Self-claims require source facts/signals.
7. Assessment snapshots preserve uncertainty and contradictions.
8. Assessment verifier blocks flattery, unsupported identity claims, and diagnostic claims.
9. Resume bullets require source facts and source logs.
10. Resume verifier blocks unsupported ownership, metrics, production claims, and employment framing.
11. Markdown self-assessment export works.
12. Markdown resume export works.
13. Evidence maps are generated for assessment and resume outputs.
14. Tests cover no automatic semantic promotion across Tick-like, Atlas, and Exp2Res.
15. Re-extraction never leaves more than one current fact generation for a correction lineage, downstream stages never mix current and superseded generations, and every invalidation removes dependent managed exports or reports their residual paths as failure.
16. A correction automatically recomputes its lineage and the complete current gaps, contradictions, signals, claims, and assessment snapshot; superseded snapshots are inspect-only and dependent resume output is invalidated.
17. Owner deletion purges all current and historical derived rows, attempts verified removal of managed exports, commits even if output removal or rebuilding fails, reports any residual managed path instead of claiming success, and can rebuild only from retained raw records.
18. Every typed JSON/polymorphic reference and Stage 6 claim-to-snapshot cardinality is validated transactionally at write time, every fact source is a non-null evidence-item link, and multiple evidence items from one raw log can support one fact without collision.

---
