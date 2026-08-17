## §24. Acceptance Criteria

V1 is acceptable when:

1. User can add daily and retrospective raw logs, each with a linked `manual_claim` evidence item created in the same operation (§13.1, §14.2–§14.3).
2. User can import at least one external source as linked raw-log and evidence-item records (§13.1, §14.5).
3. Raw logs are append-only to automation, while the owner can hard-delete any raw log without an FK or rebuild failure blocking deletion (§5.3, §13.13).
4. Corrections are stored as self-contained new events linked to their targets; targets are not mutated (§13.1).
5. Experience facts require at least one direct, non-null EvidenceItem-backed source row and derive their source-log IDs through those items (§12.4, §13.3).
6. Self-claims require source facts: Stage 6 rejects an empty `source_fact_ids` list as invalid structured output (§15.4), and §13.7 rule 1 backstops verification.
7. Assessment snapshots preserve uncertainty and contradictions (§13.6).
8. Assessment verifier blocks flattery, unsupported identity claims, and diagnostic claims (§16.3, §16.9, §16.10).
9. Resume bullets require source facts and source logs (§11.8, §18).
10. Resume verifier blocks unsupported ownership, metrics, production claims, and employment framing (§16.4–§16.6, §16.8).
11. Markdown self-assessment export works: Stage 12 publishes `report.md` in the selected snapshot's managed assessment set (§13.12).
12. Verified bullet-pack export produces `bullet_pack.md` and its complete closed companion set (§13.12).
13. Evidence maps are generated for assessment and resume outputs (§13.12).
14. Tests cover no automatic semantic promotion across Tick-like, Atlas, and Exp2Res (§5.10).
15. Re-extraction never leaves more than one current fact generation for a correction lineage (§13.3 rule 11).
16. A correction automatically recomputes its lineage and the complete current gaps and contradictions (§13.13).
17. Raw-log owner deletion purges all current and historical derived rows (§13.13).
18. Every typed JSON/polymorphic reference and Stage 6 claim-to-snapshot cardinality is validated transactionally at write time (§12 rule 10 and §12's Stage 6 transaction checks).
19. `VerificationStatus` has one meaning and explicit allowlist result for Stage 10, verified-bullet-pack export, and assessment export (§16.11).
20. Every resume branch requires the exact current Stage-10-eligible assessment snapshot selected explicitly by §14.10, and persistence/export rejects an absent, superseded, status-ineligible, or claim-inconsistent anchor (§18).
21. V1 review is Stage 7/11 verifier gating of assessment and resume projections before their consumers, over enumerations whose every declared member has a §14 producer (§5.10, §16.11, §10 rule 10).
22. Contradictions are immutable Stage 4 detections (§13.4).
23. Assessment verification performs one semantic pass per current claim, and bullet verification performs one whole-pack semantic pass per branch (§13.7, §13.11).
24. Fact extraction inherits the governing source `OccurredAt` by default (§13.3 rule 2).
25. `JobDescription.parsed` is a validated `ParsedJD` with stable duplicate-free `JDRequirement` IDs (§11.13, §13.8).
26. Stage 4 is LLM-backed through §15.8 and runs only through the single §14.7 detection-generation command or the §14.12 lifecycle flow (§13.4).
27. Stage 8 is LLM-backed through §15.9 (§13.8).
28. §16 natural-language rules bind every LLM-output or other generated-language segment (§16.12).
29. Assessment snapshots replace by assessment-view identity, and V1 declares exactly one view — `global` — so at most one snapshot is current, exactly one after a successful Stage 6 swap (§11.7, §13.6).
30. The owner-controlled local workspace is the only canonical persistence domain (§29.1).
31. Stage 11 rejects a writer-generated bullet that renders an independent project, competition, or learning experience as employment, leaves the candidate prose unchanged, and prevents it from passing verified-bullet-pack export (§16.8, §16.11).
32. Stage 7 rejects a writer-generated permanent-identity claim such as "You are fundamentally..." without applying a rewrite or permitting assessment export (§16.9, §16.11).
33. Stage 7 can mark an uncomfortable, non-flattering, evidence-grounded writer-generated assessment claim `supported` while preserving its prose byte-for-byte and invoking no writer or repair pass (§16.2, §16.11).
34. Two conforming implementations, given the same evidence graph, compute the same evidence-to-confidence ceilings and caps (§9.4).
35. For any current self-claim, Stage 7 assembles exactly the displacement-aware §15.5 provenance closure (§13.7).
36. Assessment subject selection is deterministic and service-owned (§13.6).
37. Managed export directories are keyed only by opaque service-assigned entity IDs in a bounded lowercase-ASCII single-component form (§13.14).
38. Every table derived from a top-level §11 entity has a non-empty `TEXT PRIMARY KEY` `id` that is service-assigned, opaque, immutable, unique per table, and never reused in that table within a workspace (§12 rule 11).
39. Every command establishes schema-version compatibility before any business I/O (§12.14).
40. One workspace permits multiple concurrent readers and exactly one business writer through the held OS advisory lock on `.exp2res/lock` (§8.1).
41. Every row in the seven recomputable tables resolves to exactly one producing `processing_runs` row and one per-swap `generation_id`, and every per-invocation execution value lives only in that run's `llm_calls` telemetry rows (§12 rule 13, §12.13, §12.15).
42. Every accepted persisted or transport object has one closed deterministic interpretation under §11's strict extra-forbid validation policy, whose datetime, mutability, size, count, depth, and character limits are enforced identically at acquisition, before provider calls, on model responses, and at hydration (§11).
43. For every retained correction lineage, before any model call the service deterministically computes whole-record target displacement, the exact effective-record set, and each fact's governing record from retained rows alone — with no resolution LLM, persisted resolution artifact, or ambiguity state — and no displaced record's prose ever becomes current content or reaches a prompt (§13.3 rule 10).
44. Every §14 command obeys one deterministic §14.14 runtime contract: fail-closed workspace discovery, settings precedence, non-interactive consent, stable configuration-independent exit codes, one strict versioned `--json` result envelope, raw-text-free result projections, and a closed inspection surface (§14.14).
45. Temporal, language, Unicode, and local-path behavior has one deterministic V1 interpretation: every boundary datetime is offset-aware and compared, ordered, and hashed by its UTC instant (§11, §12 rule 3); generated and verified prose is English outside §16.13's source-faithful job-description fields while source voice stays byte-for-byte (§16.13); case and normalization identity collapses only at the identities whose owning rule names it (§11); and local locators are POSIX-only (§29.4).
46. Each planned LLM invocation owns exactly one `llm_calls` row, and within each initial or §15.1 validation-retry request round only §15.10's enumerated retryable transport failures may retry it — inside the same foreground action, under one wall-clock invocation deadline, a fail-closed capability and budget preflight, and no narrowing of the transported payload (§15.10, §12.15).
47. Job-description deletion and whole-workspace purge are complete, fail-closed privacy lifecycles: each attempts every dependent managed path, commits its database deletion regardless, reports every residual instead of success, and leaves no retained source text, derived prose, or content-derived telemetry hash behind (§13.13 rule 10, §14.15, §14.16).
48. Every §19-backed import accepts only its own source's closed unwrapped record and keys idempotency by the exact (source system, source identity) pair plus the importer-computed SHA-256 hash of the validated record's §11 canonical bytes, so an exact replay is a counted no-op and changed content under a retained identity is rejected without mutating retained rows (§19.4, §19.3).
49. Domain-routed imports and deferred local views preserve their boundaries: a payload is admitted only by its own §19 source contract and gains no scope beyond it, and V1 stays CLI-first with §30's read-only local views the sole served surface and the JD-to-bullet-pack view deferred (§19.1, §19.2, §30).
50. Every assessment or resume managed-output set is published and consumed only through §13.14's manifest-backed writer, so no user-controlled string becomes a path component and no incomplete, unvalidated, or stale set is ever returned as current output (§13.14).
51. Stage 12 assessment and verified-bullet-pack exports satisfy one closed deterministic contract: re-exporting the same coherent snapshot or branch reproduces byte-identical members, every JSON companion validates as its closed versioned document, and every rendered factual line resolves through complete typed provenance to current rows or the export fails closed (§13.12, §18).
52. The system passes the §21.49 injection matrix: no ingress path — raw capture, import payloads, JD text, or evidence context — can, through embedded instructions, alter service-owned fields, suppress gaps, contradictions, or findings, widen requirement matching or employment claims, trigger undeclared authority, or mutate source content (§29.5).
53. Every agent-backed adapter executes §15 calls only through the versioned §15.12 agent-runner protocol, and an adapter is exempt only through declarations that rule out every agent affordance (§15.12).
54. `[llm]` workspace configuration alone switches among the three closed §15.13 adapters — the default agent-backed Codex CLI runner on `gpt-5.6-sol`, the agent-backed Claude Agent SDK adapter on `claude-opus-4-8`, and the required non-agent-backed OpenAI-compatible direct transport — with no code edit (§15.13, §29.2).
55. Every owner-authored `log today`, `log retro`, `correction add`, and `gaps answer` capture accepts at most 16 repeatable artifact locators and atomically persists its manual claim first plus one inert `artifact_reference` item per distinct stored locator in a canonical order computed from stored values alone (§13.1, §14.2, §29.4).
56. Every non-prompt owner record entering through `log today --file`, `log retro --file`, `correction add --file`, or `gaps answer --file` is an owner capture that needs no affirmation flag, and no capture command confirms (§14.14).
57. Ongoing activity has exactly one honest representation end to end: an open-ended `date_range` or `approximate_range` whose absent `end` is never filled from the clock, never restated as a marker field or extra precision member, and changes only through an owner correction (§11.1).
58. The §17 mirror publishes as two members of one assessment set, `report.md` and `report.html`, rendered from the same section model in the same export transaction with neither member derived from the other's bytes (§17).
59. `view serve` serves §30's mirror and gap-question views over one loopback socket and nothing else, read-only and fail-closed: every request revalidates the selected current managed set and the §16.11 gate before any content, and every refusal is one closed §30 outcome (§30, §14.17).
60. `assess repair` deterministically regenerates a current, fully verified snapshot as a new unverified claim generation that adopts persisted verifier rewrites with no provider call, and that generation reaches export only through an ordinary Stage 7 re-verification (§13.6, §14.9).

---
