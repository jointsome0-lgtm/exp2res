## §24. Acceptance Criteria

V1 is acceptable when:

1. User can add daily and retrospective raw logs, each with a linked `manual_claim` evidence item created in the same operation.
2. User can import at least one external source as linked raw-log and evidence-item records.
3. Raw logs are append-only to automation, while the owner can hard-delete any raw log without an FK or rebuild failure blocking deletion.
4. Corrections are stored as self-contained new events linked to their targets; targets are not mutated.
5. Experience facts require at least one direct, non-null EvidenceItem-backed source row and derive their source-log IDs through those items.
6. Self-claims require source facts: Stage 6 rejects an empty `source_fact_ids` list as invalid structured output (§15.4), and §13.7 rule 1 backstops verification.
7. Assessment snapshots preserve uncertainty and contradictions.
8. Assessment verifier blocks flattery, unsupported identity claims, and diagnostic claims.
9. Resume bullets require source facts and source logs.
10. Resume verifier blocks unsupported ownership, metrics, production claims, and employment framing.
11. Markdown self-assessment export works.
12. Verified bullet-pack export produces `bullet_pack.md` and the complete closed companion set in §13.12.
13. Evidence maps are generated for assessment and resume outputs.
14. Tests cover no automatic semantic promotion across Tick-like, Atlas, and Exp2Res.
15. Re-extraction never leaves more than one current fact generation for a correction lineage:
    - Downstream stages never mix current and superseded generations.
    - Every invalidation removes dependent managed exports or reports their residual paths as failure.
16. A correction automatically recomputes its lineage and the complete current gaps and contradictions:
    - Every current claim, snapshot, branch, and bullet is superseded.
    - Each invalidated assessment view is reported with its executable §14.9 regeneration command.
    - Each branch is reported with its name, retained job-description ID, and former view.
    - Superseded snapshots are inspect-only.
    - Stage 6–7 run only through §14.9.
17. Raw-log owner deletion purges all current and historical derived rows:
    - It attempts verified removal of managed exports.
    - It commits even if output removal or rebuilding fails.
    - It reports any residual managed path instead of claiming success.
    - It can rebuild only from retained raw records, and the automatic rebuild ends at Stage 4.
    - Purged assessment views are reported for explicit regeneration as command output only.
18. Every typed JSON/polymorphic reference and Stage 6 claim-to-snapshot cardinality is validated transactionally at write time:
    - Every fact source is a non-null evidence-item link.
    - Multiple evidence items from one raw log can support one fact without collision.
19. `VerificationStatus` has one §16.11 meaning and explicit allowlist result for Stage 10, verified-bullet-pack export, and assessment export:
    - `unverified` blocks every gated consumer.
    - Snapshot status is the deterministic Stage 7 reduction of its current claim statuses, including the required matching `narrative_summary` claim.
    - Only a `supported` self-claim may guide bullet generation.
    - Only a `supported` bullet may enter the verified bullet pack.
20. Every resume branch requires the exact current Stage-10-eligible assessment snapshot selected explicitly by §14.10, and persistence/export rejects an absent, superseded, status-ineligible, or claim-inconsistent anchor.
21. V1 review is Stage 7/11 verifier gating of assessment and resume projections before their consumers:
    - Every `SourceType`, `EvidenceStrength`, and `AssessmentScope` value has a §14 producer.
    - No regenerated derived row carries an owner confirm, dispute, or override state.
22. Contradictions are immutable Stage 4 detections:
    - Every current snapshot references and renders the complete current contradiction set without scope or status filtering.
    - Outside owner deletion, disappearance occurs only through an evidence-driven Stage 4 replacement generation that preserves the prior detection as superseded history.
23. Assessment and bullet verification perform one semantic pass per current claim or bullet:
    - Valid negative findings reach the owner without invoking a writer, mutating or dropping derived prose, or creating gaps.
    - §15.1 retry remains schema-only.
    - Revised wording requires an explicit replacement generation.
24. Fact extraction inherits the governing source `OccurredAt` by default:
    - It permits a contained narrower placement only with explicit selected in-context support.
    - It rejects any widened window, unsupported temporal-precision increase, or temporal-confidence increase above the governing source.
25. `JobDescription.parsed` is a validated `ParsedJD` with stable duplicate-free `JDRequirement` IDs:
    - Every resume branch persists its required exact §14.10 job-description selection as a validated reference.
    - Every resume bullet requirement reference resolves transactionally in the exact Stage 10 job description.
    - Free-form, missing, duplicate, or wrong-job values fail the batch.
26. Stage 4 is LLM-backed through §15.8 and runs only through the single §14.7 detection-generation command or the §14.12 lifecycle flow:
    - Either flow decides retention per output set under §13.4: a set with an equal structural-key comparison (for gaps, additionally all-unanswered) keeps its existing rows, IDs, and prose, while exactly a failing set is replaced.
    - Either flow reports every invalidated artifact class under §13.4's dependency-graph supersession, where a replacement of either set supersedes every current claim, snapshot, branch, and bullet while per-set retention leaves those layers current.
    - A rerun whose §11 canonical input hash, provider, model, and prompt-policy identity equal the most recent completed §13.4 run's recorded first-call telemetry completes as a full retention with no provider call and no `llm_calls` row.
    - Stage 4 restricts targets to supplied Stage 1 evidence and current Stage 3 facts.
    - The persisted §11 model, §12 rule 10 validator, and §15.8 producer expose exactly the same closed `DetectionRefType` target domain.
    - Stage 4 exposes no verdict or resolution state.
    - Stage 4 uses §15.1 only for invalid structured output or references.
    - Stage 4 rejects generated-voice violations atomically without another LLM call.
27. Stage 8 is LLM-backed through §15.9:
    - It sends no `JobDescription.id`, because no entity exists at call time.
    - It stores no partial or untyped parse.
    - It assigns the entity ID and stable requirement IDs only after a valid parser candidate.
    - It validates the final `ParsedJD`.
    - It uses §15.1 only for model-response invalidity.
    - It handles service-ID allocation failure locally or atomically without another LLM call.
28. §16 natural-language rules bind every LLM-output or other generated-language segment:
    - They never reject or rewrite `RawLog.raw_text`, owner-authored gap-answer text, or §19 system-of-record text.
    - A source excerpt remains exempt only with a typed source reference and byte-for-byte value/substring validation.
    - Structural validation applies everywhere.
    - Mixed-origin rendering preserves segment boundaries.
29. Project snapshots store the canonical scope target and replace by assessment-view identity — (scope, case-folded canonical target) — so `global` and distinct project views are simultaneously current:
    - Snapshot unknowns are the complete typed current unanswered `GapQuestion` set, with answered rows excluded.
    - §17 renders those unknowns plus every claim's typed bundle-grounded counterevidence.
    - That rendering creates no independent prose input, alters no §16.11 aggregation, and bypasses no Stage 10 gate.
30. The owner-controlled local workspace is the only canonical persistence domain:
    - SQLite is authoritative for raw and derived records, and managed `out/` files remain local projections.
    - Private-by-default operation performs no outbound telemetry, background sync, auto-push, implicit cloud persistence, or deferred model call.
    - Only a foreground user-initiated pipeline run may send the exact typed inputs of the seven active §15 contracts (§15.2, §15.4–§15.9; §15.3 is retired) to the explicitly selected provider under its disclosed retention terms; no other LLM or network path exists.
    - Prompt composition excludes credentials, tokens, ignored or non-selected material, and ambient command, environment, and filesystem content.
    - Instruction-like source text cannot alter contract behavior, output shape, or requirement matching.
    - §13.13 remains the managed-data deletion guarantee.
31. Stage 11 rejects a writer-generated bullet that renders an independent project, competition, or learning experience as employment, leaves the candidate prose unchanged, and prevents it from passing verified-bullet-pack export.
32. Stage 7 rejects a writer-generated permanent-identity claim such as "You are fundamentally..." without applying a rewrite or permitting assessment export.
33. Stage 7 can mark an uncomfortable, non-flattering, evidence-grounded writer-generated assessment claim `supported` while preserving its prose byte-for-byte and invoking no writer or repair pass.
34. Two conforming implementations, given the same evidence graph, compute the same §9.4 ceilings and caps:
    - Candidates above a cap fail as invalid structured output, without silent rewriting.
    - No evidence strength authorizes ownership, metric, production, temporal, or employment content.
35. For any current self-claim, Stage 7 assembles exactly the displacement-aware §15.5 provenance closure:
    - The closure holds the claim's closure facts, their linked evidence items projected under §13.3 rule 10, and only the non-displaced retained `RawLog` objects reached through them.
    - The bundle adds the view's complete `scope_facts` context.
    - Members are duplicate-free and ID-ordered, with raw-log objects reached only through the closure.
    - A missing, superseded, duplicate, or extra post-projection member fails verification closed before any provider call.
    - §29.3's §15.5 row names that bundle.
36. Assessment scope selection is deterministic and service-owned:
    - `global` supplies every current fact.
    - `project` supplies subject facts by case-folded canonical target match, with no other out-of-subject fact context.
    - Complete gap and contradiction sets are never scope-filtered.
    - Writer citations outside the supplied objects fail as invalid structured output.
    - Subject facts carry their governing record's copied project provenance (§13.3 rule 13).
    - An empty project subject fails before the provider call.
37. Managed export directories are keyed only by opaque service-assigned entity IDs in §13.14's bounded lowercase-ASCII single-component form:
    - Assessment sets live under `out/assessment/<snapshot-id>/` and resume sets under `out/branch/<branch-id>/`.
    - Each valid matching manifest carries the assessment-view identity or branch display name and job-description ID.
    - No user-controlled scope target or branch name is encoded into a path component.
    - A directory without a valid matching manifest and a render-input hash equal to the current coherent source state is never current output.
    - A lifecycle-owned status or gap-answer transition invalidates that hash even when IDs and generation provenance do not change.
    - Publishing a replacement snapshot uses its new ID and removes or reports prior manifest-valid sets naming the same assessment view, without touching other views.
    - Branch replacement and selection remain NFC case-folded by display name, but `assessment`, separators, and dot/whitespace edge forms have no path-specific prohibition or reservation.
    - Names cannot alias output paths, because IDs, not names, derive them.
38. Every table derived from a top-level §11 entity has a non-empty `TEXT PRIMARY KEY` `id` that is service-assigned, opaque, immutable, unique per table, and never reused in that table within a workspace:
    - A retained-row collision retries locally or fails the producing run atomically without another LLM call.
    - Imported upstream identifiers remain `RawLog.external_ref`/metadata provenance rather than local entity IDs.
39. Every command establishes §12.14 compatibility before any business I/O:
    - Schema migration runs only through `exp2res db migrate`, after a verified WAL-inclusive managed backup.
    - Migration applies all pending migrations plus foreign-key and one-current-generation validation in one transaction.
    - Migration failure leaves the prior-version workspace usable with `schema_meta` unchanged and the backup retained.
    - Newer or unrecognized schemas fail closed without business reads or writes.
    - `exp2res init` is idempotent and non-destructive on an existing current-version workspace.
40. One workspace permits multiple concurrent readers and exactly one business writer through the held OS advisory lock on `.exp2res/lock`:
    - Every writer holds that lock across all of its `BEGIN IMMEDIATE` transactions and coupled managed-output work.
    - The workspace persistently uses WAL, writer connections use `synchronous = FULL`, and all connections use a bounded `busy_timeout` and follow §12's per-connection foreign-key rule.
    - Every read-only or historical-inspection command reads business state from one coherent snapshot, and each writer transaction has the same property.
    - Contention fails after the same bounded wait with the stable one-line `workspace_busy` diagnostic rather than a stack trace.
    - Process death releases the lock, and WAL recovery restores database consistency without manual repair.
    - Process races cannot violate current/superseded, privacy, provenance, or managed-output invariants.
41. Every row in the seven recomputable tables resolves to exactly one producing `processing_runs` row and one per-swap `generation_id`:
    - A multi-lineage extract uses a different generation per lineage.
    - The Stage 4 set or sets replaced in one swap, each Stage 6 view, and Stage 10 branch use one shared generation.
    - A §13.4-retained set keeps its prior generation, and a Stage 4 run that retains both prior sets allocates none.
    - Each correction, deletion, or recompute flow groups its invoked stage runs under one non-stage `13.13` orchestration run through `parent_run_id`.
    - Run-level provider/model/prompt-policy identity and stable failure codes remain in `processing_runs`.
    - Per-invocation input/output hashes, request identifiers, token counts, cost, and retries live in §12.15 `llm_calls` rows — one per planned invocation, exactly one for single-invocation LLM stages except a §13.4 short-circuited rerun, which owns none, and none for non-LLM runs.
    - Any failed run persists telemetry only and owns no business or finding row.
    - Every completed Stage 7/11 pass atomically appends one immutable `VerificationFinding` per target while updating only the target's latest denormalized operational state.
    - A failed verifier leaves every target field and prior finding untouched.
    - Repeated verification keeps every finding without changing candidate prose.
    - Advisory rewrites never re-enter prompts or exports — §13.6 repair adoption creates a new `unverified` claim instead, per criterion 60.
    - Raw-log owner deletion purges all findings and recomputable rows; JD deletion and workspace purge follow their §13.13 rule 10/§14.16 scopes in criterion 47.
    - In that same purge transaction, every retained `llm_calls` content hash is redacted to `NULL`, so retained telemetry can neither reproduce nor confirm-by-hash purged or deleted content; retained opaque IDs may stop resolving.
42. Every accepted persisted or transport object has one closed deterministic interpretation:
    - Extra-forbid strict validation permits only ISO-8601-string-to-`datetime` coercion in JSON-boundary mode and admits only offset-aware `datetime` values.
    - Assignment is validated, and models are frozen except for their named lifecycle-owned transitions.
    - Entity metadata is a bounded inert service/importer channel that an LLM never authors.
    - The normative size, count, depth, NUL, and control-character limits are enforced identically before provider calls, at acquisition, on model responses, and at hydration.
43. For every retained correction lineage, before any model call the service deterministically computes whole-record target displacement, the exact effective-record set, and the governing record from retained rows alone, with no resolution LLM, persisted resolution artifact, or ambiguity state:
    - A retained record is displaced if and only if another retained lineage member names its ID in `corrects_log_id`, and effective records are exactly the non-displaced members.
    - Each fact's governing record — the latest by (`recorded_at`, ID) among the effective records it selects — governs that fact's source placement and project provenance.
    - Stage 3 supplies only effective records and their evidence items as content, plus displaced-record non-`manual_claim` items as prose-free `displaced_support_items` descriptors.
    - Displaced raw text and copied question context never become current content.
    - Displaced `manual_claim` items can never become current support.
    - No displaced record or linked item can become a Stage 4 target.
    - Displaced-record non-manual descriptors remain lineage-scoped selectable support and preserve §9.4's corrected-import `high` ceiling.
    - Stage 3 commit rejects atomically every selected item outside the effective-item and displaced-record-support classes, and every candidate fact that selects no effective-record item.
    - The §13.3 rule 10 displaced-record support descriptor projection binds every §15 serialization of an `EvidenceItem` linked to a displaced record, and no displaced `RawLog` is ever serialized into any prompt.
    - §15.2 makes in-scope descriptor selection required rather than merely permitted.
    - When a lineage's call supplied a non-empty descriptor array that no committed replacement fact selects, the extract result carries §13.3 rule 14's one deterministic `displaced_support_unselected` warning for that lineage — a bounded root-and-count message, presentation only, gating nothing and persisting nowhere, its type service-reserved against model-authored duplicates.
44. Every §14 command obeys one deterministic §14.14 runtime contract:
    - Non-`init` discovery walks canonical physical parents to the nearest `.exp2res/`, stops and fails on a partial nearest marker rather than selecting an enclosing workspace, permits a no-fallback explicit `--workspace` root, and fails outside a workspace.
    - `init` targets only the canonical current directory, may create a nested workspace, is an idempotent no-op on the current schema, fails closed on partial state, and never touches content beneath a pre-existing non-empty `out/`, including on first creation, while restoring the §29.2 owner-only mode on the `out/` directory entry itself or failing closed.
    - Declared settings resolve explicit flag over documented `EXP2RES_*` variable over selected-workspace config over a built-in default when one exists; unresolved required settings fail closed; and invocation consent/control flags and §29 credentials remain outside that chain.
    - Non-TTY stdin or `--no-input` never prompts or blocks: missing prompt input fails, and every destructive or cost-bearing action requires `--yes`; TTY execution confirms only the named deletion, purge, migration, and cost-bearing call surfaces.
    - Exit codes are stable and configuration-independent:
      - 0 success/no-op, 1 internal, 2 usage/input, 3 workspace, 4 schema, 5 `workspace_busy`, 6 provider/transport, 7 validation/integrity, 8 incomplete cleanup/deletion, 9 cancellation, and 10 a completed semantic-negative verifier or §16.11 gate result.
      - `CLIResultStatus` maps 0 to `ok`, 1–8 to `failed`, 9 to `cancelled`, and 10 to `blocked`.
      - Code 9 takes precedence when cancellation also leaves incomplete cleanup.
      - Code 10 retains complete findings and a completed verifier run rather than manufacturing an operational failure.
    - Under `--json`, stdout is exactly one strict extra-forbid version-1 envelope whose process-matching exit code, stable diagnostic, nullable canonical command/workspace, typed affected IDs, run/generation IDs, complete structured invalidated views/branches and findings, residual paths, warnings, documented retry, and closed command-discriminated primary result are deterministic.
    - List/show, detection, deletion, and export results use only the §14.14 projections and never include `RawLog.raw_text`, `JobDescription.raw_text`, or a free-form object.
    - Primary human results alone may otherwise use stdout, diagnostics/progress use stderr in both modes, and no public or retained diagnostic exposes secrets, prompt text, raw source text, or undeclared source content.
    - Interruption rolls back the in-flight unit, releases locks, and exposes no partial current generation without reversing a §13.13 boundary already committed.
    - The operable inspection surface is exactly `db status`, `logs list`, `facts list/show`, `gaps list`, `contradictions list/show`, `assess list/show`, `jd list/show`, and `runs list/show`; evidence-item, branch/bullet, deeper historical-generation, and parsed-requirement-dump inspection remains explicitly deferred.
45. Temporal, language, Unicode, and local-path behavior has one deterministic V1 interpretation:
    - Every datetime accepted at a §11, §15, §19, hydration, or construction boundary is offset-aware, and a naive value fails there; §14.14 alone may resolve naive owner CLI input before model validation from the explicit IANA workspace timezone, never ambient timezone or locale; and §12 storage preserves the supplied or resolved offset.
    - Distinct stored offsets for one instant survive unchanged, every equality, ordering, duration, and sort key uses the UTC instant, and §11 canonical hashing remains identical for equal instants.
    - A naive daylight-saving gap or fold fails closed with explicit-offset guidance, and ISO calendar normalization gives Monday-start weeks, Q1 January–March, and implementation-identical month, quarter, and week anchors.
    - Except for §16.13's source-faithful mixed-language job-description fields, V1 generated and verified prose is English:
      - A required Russian-language `RawLog` case produces meaning-preserving English facts while the source and every typed quoted segment remain byte-exact Russian.
      - An English paraphrase remains generated voice.
      - A non-English generated fact fails §16.13 voice validation and does not persist.
      - §16.6 rejects an unsupported English production claim from Russian evidence exactly as it would from English evidence.
    - Source-named entities keep their source-script spelling inside generated prose, and §15.1 response validation applies §16.13's deterministic mixed-script tripwire: a mixed Latin/Cyrillic token in any model-authored response string that does not equal, complete and under NFC, a mixed-script token carried by that call's serialized input fails as invalid structured output with a content-free diagnostic naming the field location and a stable code but never the token bytes, while §12 hydration and stored content are never evaluated by the tripwire.
    - NFC plus locale-independent Unicode Default Case Folding collapses case or normalization variants only at the named project scope/match, assessment-view, and branch identities, independent of process locale; the same strings remain distinct everywhere no owning rule names normalization or folding.
    - V1 local locators use POSIX Linux/macOS semantics: Windows drive-letter, UNC, backslash-separated, and non-POSIX `file:` forms fail without reinterpretation at acquisition and at the pre-serialization re-check, while on a case-insensitive volume every case-variant spelling of one file receives one deterministic mandatory-deny and user-ignore decision — the comparisons additionally apply under the locale-independent case fold there, so a case variant of a denied name is denied even when canonicalization preserves the supplied spelling.
46. Each planned LLM invocation owns exactly one `llm_calls` row, and within each initial or §15.1 validation-retry request round only §15.10's enumerated retryable transport failures may retry:
    - Every permitted retry increments `transport_retries` on that row before the next attempt, remains synchronous inside the same foreground §14 action with bounded jittered backoff, and observes that round's configured total-transport-attempt cap including its initial attempt.
    - One configured wall-clock deadline applies to a whole provider invocation — every round, attempt, and backoff — rather than to the stage, the run, or each attempt, so a fast failure retries within the remaining budget while a deadline-consuming attempt ends the invocation; nothing is queued, deferred, or resumed in the background.
    - Exhausting an attempt cap records the applicable stable transport code on the call and run, while a no-response attempt timeout or invocation-deadline exhaustion records `transport_timeout`; each exits §14.14's provider/transport class 6, whereas a non-retryable transport failure fails immediately without a retry or retry-counter increment.
    - An ambiguous lost response remains on that same call row for any permitted retry, cannot create a second `llm_calls` row or duplicate business rows, and any response arriving after terminal failure is discarded.
    - An adapter/model missing any required declared capability is rejected before any transport of the run, and deterministic preflight rejects a configured input/output-token, run-call, or applicable invocation/run-cost excess as `budget_exceeded` or a declared model-context excess as `context_overflow` before every provider attempt.
    - No §15 payload is truncated, sampled, elided, summarized, partitioned, merged, or otherwise narrowed, so the preflight-measured complete-set serialization is transported byte-for-byte or no request is sent.
    - A handled owner interrupt before transport sends nothing, one during transport aborts or abandons the attempt and never adopts its response, and one after validation but before business commit rolls back that transaction; each leaves either the prior current generation or the already-committed §13.13 invalidated state, never a partial new generation, and records `cancelled` on the run and any applicable nonterminal call. Only such a handled owner interrupt exits §14.14's class 9.
    - A hard crash emits no exit envelope, but OS-lock release, WAL recovery, and transaction rollback preserve that same business state; after acquiring the §8.1 workspace lock and before its business operation, the next compatible writer marks the abandoned run and every nonterminal call `status = "failed"` and `failure_code = "cancelled"`, supplies missing finish times, and leaves terminal call rows unchanged. Attempt and deadline timeouts are never this cancellation class.
    - If any planned invocation in Stage 3, 7, 10, or 11 fails, the failed run may retain telemetry but commits no partial business row, verification finding, or verifier update; a later run invokes the whole stage with fresh calls and reuses none of the failed run's validated responses.
    - The `[llm]` attempt-cap, backoff, deadline, token, call-count, and cost-budget values come from `.exp2res/config.toml` with conservative service defaults, and tuning their numeric values requires no SDD change, while the shipped template and built-in deadline defaults agree, cover §15.10 rule 9 documented 130-second expected upper bound for the default selection at high effort, and a service-default retry-count change requires observed failure distributions rather than an unmeasured provider-SDK default.
    - Progress and cost output is stderr-only, may contain only stage, call index/count, token counts, and reported cost, and never exposes prompt or response content or credentials.
    - Terminal transport failures classify into §15.10 rule 10's append-only stable vocabulary by outcome class rather than by provider message:
      - A provider-rejected request shape records `transport_request_rejected` and an unserviceable selected model records `transport_model_unavailable`, both non-retryable and in class 6.
      - Any rejection an adapter cannot classify by its own deterministic markers stays `transport_provider_error` rather than being guessed into a narrower code.
      - A rejection class is named only from a typed field the adapter parsed out of its own runtime's envelope, never from anything read out of the error channel — no phrase, no exact error-code token, no well-formed error body — and an adapter whose runtime reports no such field names no rejection class at all.
      - Classification runs only after that adapter's retryable-outage markers, so an outage whose channel echoes rejection wording keeps its retry.
    - Human mode additionally prints one stderr diagnostic naming the failing stage, contract identifier, and stable code — for an operational failure the stage orchestrator raises after a call validated as much as for a transport failure inside one — service-owned constants only, with no byte of the provider's error channel echoed, quoted, summarized, or persisted anywhere, and the §14.14 result envelope is unchanged by it.
47. Job-description inspection and deletion and whole-workspace purge are complete, fail-closed privacy lifecycles:
    - `jd list` and `jd show` are read-only, provider-free, raw-text-free projections.
    - Confirmed `jd delete` first attempts every managed migration backup and every dependent ID-keyed `out/branch/<branch-id>/` output: no surviving branch can own or spare a captured directory because IDs never collide or are reused, while a later branch for another job description keeps its distinct ID-keyed output even when its name is byte-equal or fold-equal and regardless of filesystem case behavior.
    - Confirmed `jd delete` then, in one referentially ordered transaction, hard-deletes the selected JD, every dependent branch and bullet, and every bullet finding without FK blocking; reports the selected raw-text-free JD projection, every purged branch by ID and name, and every removed or residual managed path through the closed §14.14 fields; globally sets `input_hash` and `output_hash` to `NULL` on every call row committed before that transaction; leaves current assessment views current and all current or historical snapshots, claims, and their findings untouched; creates only its content-free §13.13 orchestration telemetry; performs no recompute; checkpoints the committed deletion; and never runs `VACUUM`.
    - A path failure or any symlink leaves database deletion committed, never traverses the symlink, preserves its target, and reports every residual as `deletion_incomplete` with exit code 8.
    - Confirmed `workspace purge` attempts every managed export, backup, and temporary-output removal, clears every business and telemetry row, and replaces prior `schema_meta` history with exactly one fresh current-version/application-version row while retaining secret-free `config.toml`, `.exp2res/`, the empty current-version database, and empty managed roots.
    - `workspace purge` records no run of its own, performs no rebuild, and after commit executes checkpoint, `VACUUM`, and final checkpoint so successful application-controlled live state has no purged sentinel in the main database, free pages, or any extant WAL/SHM sidecar, without requiring unsafe sidecar unlinking or claiming physical erasure outside §29.6.
    - Every connection sets and verifies `secure_delete = ON`; every point deletion checkpoints but never vacuums; and any purge path or erasure-step failure leaves database deletion committed and reports the affected path instead of success.
    - A literal credential recognized in workspace configuration fails closed at config load before business I/O, without echoing or copying the literal into telemetry.
    - Column-level inspection after each point deletion finds no source text, derived prose, source path, account or user identifier, email address, or other person-stable or content-derived retained identifier: every pre-transaction call hash is `NULL`, opaque internal/provider-request IDs and non-content-derived `prompt_policy_hash` may remain, a raw-log rebuild may add fresh hashes over surviving content only, JD deletion adds none, and workspace purge leaves no telemetry.
48. Every §19-backed import accepts only the closed, source-versioned §19.4 envelope and keys idempotency by the exact (`source_system`, `source_record_id`) identity plus the SHA-256 hash of §11 canonical `body` bytes:
    - A first import creates one atomic RawLog/EvidenceItem pair and records the named identity/hash metadata, exact replay is a counted no-op, the same identity with a different hash conflicts without mutating retained evidence, and the same hash under a different identity creates an independent pair.
    - Unsupported future or retired contract versions, hash mismatches, and other invalid records fail at acquisition without selecting a §12.14 migration.
    - The §19.3 GitHub specialization requires exact `source_record_id = <repo>@<commit_sha>`, requires `commit_sha` to contain exactly 40 lowercase hexadecimal characters, and rejects an abbreviated, overlong, uppercase, or non-hexadecimal SHA before duplicate classification.
    - Required closed author and committer objects validate when their optional name, email, and login members are null, and those inert identity strings never influence attribution.
    - Omitted §10 `OwnerAttribution` materializes as `unknown` before canonical body serialization and hash verification, and only `owner` creates `commit_or_pr` evidence while `not_owner` and explicit or default `unknown` create `artifact_reference` evidence.
    - An exact GitHub replay is a counted no-op, while materially changed body content under the same identity and a different, correctly recomputed hash conflicts without rewriting existing raw or evidence rows.
    - Every multi-record payload stays within §11's total-object bound, is processed in file order in one §8.1 writer transaction, and persists all accepted pairs or none: retained and intra-batch exact duplicates remain counted no-ops, any conflict or invalid record aborts every candidate row, and rerunning the same file after interruption or a lost result converges without a cursor, per-record commit, or background continuation.
    - Every completed §14.14 import result carries complete, untruncated `accepted`, `duplicate`, `conflict`, and `rejected` counts and input-ordered `{record_number, source_record_id, raw_log_id}` lists that partition all established records, report a created ID only for a committed acceptance, and retain the full result on atomic failure.
    - A supplied referenced-artifact `content_digest` is recorded as inert EvidenceItem provenance; a later authorized dereference reports a missing file or digest mismatch, fails a required read closed, and never substitutes, fetches, refreshes, or silently omits content.
49. Domain-routed imports and deferred local views preserve their boundaries:
    - The closed §19.1 ephemeris body accepts only source-tagged activity evidence with its required `occurred`, `project`, and `text` values; a directly routed learning knowledge-state, trail, or evidence-reference payload is rejected, while learning mentioned in admitted activity text retains only the §10 `imported_activity_event` scope and cannot establish Atlas-scale knowledge state.
    - A valid §19.2 Atlas snapshot persists only its atomic `RawLog`/`EvidenceItem` pair with the §10 `atlas_snapshot` entry type and `knowledge_state_snapshot` strength, creates no `ExperienceFact` or `SelfClaim` at import, and any later Stage 3 fact remains within §9.4's knowledge-attribution scope and confidence ceiling without implying implementation, built or production use, outcome, ownership, mastery, or automatic `Confidence = "high"`.
    - V1 remains CLI-first: the §30 mirror and gap-question views are served only by the explicit §14.17 command, and the JD-to-bullet-pack view stays deferred.
    - Every §30 view renders read-only over an explicitly selected current derived state, is served and embedded only by a loopback local URL configured outside Exp2Res, and delegates every action only to an existing §14 flow with no additional consumer coupling, LLM, egress, background, or consent authority.
    - The deferred JD-to-bullet-pack view renders a refusal required by §18 or §16.11 as a first-class completed §14.14 `blocked` outcome with its reason and findings, never as an error page or operational failure.
    - The sole question-of-the-day handoff is §30's gap-question view over the explicitly selected current snapshot's manifest-backed §13.12–§13.14 `out/assessment/<snapshot-id>/self_claims.json`, where Exp2Res itself performs complete current-output revalidation and closed-schema validation and presents only the `question` values of `unknowns` entries with `answered = false` selected by §17, exposing no other companion field and requiring the shell to read no companion file, manifest, or workspace path.
    - An answer can return only through ordinary diary/activity capture or import as a new `RawLog`, with no gap ID, link-back token, callback, or relink under §14.7.
50. Every assessment or resume managed-output set is published and consumed only through §13.14's manifest-backed writer:
    - Its path is exactly `out/assessment/<snapshot-id>/` or `out/branch/<branch-id>/` using the bounded opaque service-assigned entity ID, so no scope target, branch display name, or other user-controlled string becomes a path component, and a hostile name such as `../../outside` remains only manifest identity data within the managed set.
    - Candidate construction occurs in an owner-private same-filesystem sibling, writes and flushes the complete fixed member set before writing the closed manifest last, and reaches current visibility only by the complete-set publication protocol.
    - Every newly created managed parent, candidate, rollback, or final-set directory is `0700` and every member and manifest is `0600` without relying on umask, and a mode-setting failure aborts before current visibility.
    - Interruption after any member write leaves no current-looking partial set, because an absent, invalid, mismatched, member-hash-inconsistent, or render-input-hash-inconsistent manifest fails export-read validation closed and is never indexed or returned as current output.
    - A disk-full, validation, or pre-move atomic-rename failure publishes nothing and retains any prior current set; fallback failure after moving the prior set performs §13.14's one restoration attempt, retaining the prior set on success and otherwise reporting the rollback residual with no current final set rather than claiming success.
    - Every managed preamble, write, rename, read, enumeration, stale-set removal, and deletion revalidates canonical containment beneath the workspace `out/` root and uses no-follow semantics for every component and final entry; a planted symlink or path change is skipped and reported, its inside- or outside-workspace target remains unchanged, no manifest or user value grants path authority, and stale cleanup can never escape the workspace.
    - An unresolved stale-set residual aborts replacement publication without touching another assessment view's ID-keyed set.
    - Failed publication leaves database state unchanged, and every abandoned candidate or rollback sibling is deterministically reconciled or reported before a later publication.
51. Stage 12 assessment and verified-bullet-pack exports satisfy one closed deterministic contract:
    - Repeated export of the same coherent snapshot or branch reproduces golden-file-identical fixed-member bytes and identical manifest member hashes for `report.md`, `self_claims.json`, assessment `evidence_map.json`, `bullet_pack.md`, bullet-pack `evidence_map.json`, `verification_report.json`, `gaps.json`, and `contradictions.json`.
    - Every JSON companion validates as the exact extra-forbid version-2 §13.12 document, and any missing, extra, mistyped, unsupported-version, duplicate-ID, unresolved-reference, inexact verification row, or incomplete or unused evidence-link closure fails before publication.
    - Every factual bullet sentence or logical line in `bullet_pack.md` resolves from its exact same-order `rendered_bullets` row through that row's complete claim links and then through the corresponding fact and evidence links — or, for a facts-only bullet, through its exact direct-fact path — to current §11 rows, while the renderer adds only §18 structural syntax and no factual bridge, summary, transition, filler, or inferred coherence prose.
    - §17–§18 deterministically render fixed headings, partial or empty sections without filler, temporal uncertainty without precision inflation under §5.5, §11.1, §16.7, and §17, hostile Markdown metacharacters without structural injection, NFC/LF generated output with the byte-exact source-voice exception, and exactly one final LF.
    - §13.10 keeps the first exact duplicate by its canonical order and drops later byte-equal candidates, while semantic near-duplicate detection remains post-V1 and suppression uses neither a second LLM coherence pass nor sibling-bullet context.
    - Under `--json`, `bullets generate`, `bullets verify`, and `bullets export` report only through their canonical closed §14.14 envelope projections: generation and verification use `result = null`, export uses the complete manifest-path result, and a completed non-passing verification or export-gate outcome remains class-10 `blocked` with complete findings when applicable.
52. The system passes the §21.49 injection matrix: no ingress path — raw capture, import payloads, JD text, or evidence context — can, through embedded instructions, alter service-owned fields, suppress gaps, contradictions, or findings, widen requirement matching or employment claims, trigger undeclared authority, or mutate source content.
53. Every agent-backed adapter executes §15 calls only through the versioned §15.12 agent-runner protocol:
    - A fresh empty per-invocation contract workspace.
    - Wrapper-provided structural read confinement proven by the canary probe rather than by runtime read-only modes.
    - Ambient rules/configuration and parent-environment isolation.
    - Ephemeral foreground execution.
    - The native schema-constrained final-message file as the sole result channel.
    - The two-half fail-closed preflight with a declared protocol version.
    - Identical isolation evidence for every shipped agent-backed adapter, with non-agent-backed adapters exempt only through declarations that rule out every agent affordance.
54. `[llm]` workspace configuration alone switches among the three closed §15.13 adapters — the default agent-backed Codex CLI runner on `gpt-5.6-sol`, the agent-backed Claude Agent SDK adapter on `claude-opus-4-8`, and the required non-agent-backed OpenAI-compatible direct transport — with no code edit:
    - Every selection passes §15.10 rule 4 validation declaring exactly one §29.2 credential form.
    - A fresh §14.1 initialization writes the default Codex selection, which transmits nothing until the owner's external session exists.
    - A missing or unauthenticated external session, exactly like a missing or ambiguous credential reference, fails the outward call closed with no fallback to another adapter, model, or credential form.
    - Each externally-managed-session adapter additionally accepts one optional `[llm]` key naming its local executable as a plain absolute POSIX path and never a credential slot: an absent key keeps that adapter's own `PATH` discovery, a supplied path is the executable capability preflight resolves and the runner binds without any `PATH` lookup, and a missing or non-executable path fails `capability_mismatch` before any transport and independently of the session check, with a resolved executable still subject to the complete §15.10 rule 4 declaration check.
55. Every owner-authored `log today`, `log retro`, `correction add`, and `gaps answer` capture accepts at most 16 repeatable artifact locators and atomically persists its manual claim first plus one inert `artifact_reference` item per distinct stored locator in a canonical order computed from stored values alone:
    - Local POSIX paths and `file:` URIs round-trip only as authorized canonical real paths, while every other scheme must be a complete absolute URI with no forbidden character or malformed percent escape and round-trips byte-for-byte only through `uri` with no normalization, re-encoding, or case folding.
    - No locator is ever opened, fetched, probed, or handled.
    - Unresolvable, mandatory-denied, privacy-ignored, Windows-form — including a slash-prefixed drive decoded from a `file:` URI — structurally invalid, oversized, duplicate, or over-count input fails in class 2 before any row is written.
    - Immediately before any §15 prompt serialization, the common invocation boundary applies §29.4's current local-locator reauthorization to owner-captured and imported paths alike: a newly ignored, unresolvable, mandatory-denied, or unsupported persisted local locator fails the complete stage before transport in class 7 as `locator_reauthorization_failed`, without omission or persisted-row mutation, while a non-local scheme remains byte-exact inert provenance and is never filesystem-resolved.
    - A same-record artifact item leaves §9.4's fact ceiling at `medium`, while an owner correction can select the displaced root's artifact item plus effective correction evidence across two raw logs and thereby reach the unchanged `high` ceiling.
56. Every non-prompt owner record entering through `log today --file`, `log retro --file`, `correction add --file`, or `gaps answer --file` requires an explicit command-local `--owner-authored` affirmation before source acquisition or persistence, with no configuration or environment representation and no implication in either direction between affirmation and `--yes` consent:
    - Path sources retain §29.4 acquisition and persist in `external_ref` the symlink-resolved canonical real path that acquisition authorized rather than the supplied spelling, so a record captured through a relative path and a record captured through the equivalent absolute path are indistinguishable afterwards and §29.4's pre-serialization re-check reaches the same verdict whatever directory a later stage runs in.
    - `--file -` reads bounded valid UTF-8 from standard input, records no `external_ref`, and is not filesystem acquisition.
    - Every accepted multiline source round-trips byte-identically with unchanged `manual_daily`/`manual_entry`, `manual_retro`/`user_memory`, `correction`/`manual_entry`, or `gap_answer`/`manual_entry` classification, and composes with repeatable artifact locators.
    - Non-prompt correction capture makes §14.4's copy-unless-replaced mechanics explicit flags: omitting them copies the target's `OccurredAt` and `project` exactly and resolves no local time, so no workspace timezone is required; the temporal flags replace the placement only as a whole typed set under §14.3's rules; `--project` and `--clear-project` replace or clear the label and are mutually exclusive; any of the five supplied without `--file` is invalid usage in class 2; and the affirmation still never implies the §13.13 rebuild consent.
    - Interactive retrospective capture asks precision first and, for `unknown`, neither prompts for nor stores a period; non-prompt `unknown` plus `--period`, a missing required typed value, or a range without `start/end` fails in class 2 without prompting or discarding owner input.
57. Ongoing activity has exactly one honest representation end to end:
    - `OccurredAt` records it as an open-ended `date_range` or `approximate_range` whose `end` is null — never a capture-date end that silently decays, never an `unknown` precision that discards the known start, and never a marker field, extra precision member, or derived "as of capture" reading.
    - Openness is always typed explicitly, so `start/..` is the only accepted open input form, while an empty end segment, a bare `..`, an open form at a non-range precision, or a separator-free range fails in class 2 without writing a row, and no absent end is ever filled from the clock, the workspace timezone, or `recorded_at`.
    - Storage carries openness in the existing nullable `occurred_end` alone and hydrates the identical shape back.
    - Under §16.7 an open-ended range compares as unbounded width — the weakest range form — while as a containing interval it clips to the attested window `[start, recorded_at)` of the record carrying it or, for a derived row, of its §13.3 rule 10 governing record, and as a contained interval it stays `[start, ∞)` and entails no bounded placement.
    - Stage 3 copies an open governing placement by default, admits a narrowing only inside that attested window and only with explicit selected support stating the bound, and rejects both a placement at or after the governing `recorded_at` and any conversion of a closed governing end into `end: null`.
    - An owner correction is the only state change available: restating the period re-attests it as of the correction's own `recorded_at` and supplying an `end` closes it, with every retained row unchanged.
    - §17 renders start, an explicit open-period label, the approximate flavor when applicable, and a labeled as-of anchor from the attesting `recorded_at` in one canonical offset-preserving ISO 8601 form, with no end bound, no present-tense continuation, and no export wall-clock value, so the same snapshot renders byte-identically however long after capture it is exported.
58. The §17 mirror publishes as two members of one assessment set:
    - `report.md` and `report.html` render from the same section model in the same export transaction and carry the same claims, statuses, uncertainty, source trails, facts, gaps, contradictions, and counterevidence in the same order under the same fixed headings, with empty sections unfilled in both and neither member derived from the other's bytes.
    - Repeated export of unchanged state reproduces golden-identical `report.html` bytes pinned by its `manifest.json` member hash under `manifest_version = 5`, with no wall-clock value, nonce, workspace path, or host name in the file.
    - The page is inert and self-contained: no script, form, framed or embedded object, image, event-handler attribute, inline style attribute, or absolute or relative URL; one inline stylesheet admitted only by its own SHA-256 hash under a `default-src 'none'` policy; and one total escaping function that renders hostile markup, quotation marks, and embedded line breaks as text no element or attribute can be opened from.
    - Opening the file is local presentation under §29, and §30's mirror view serves those revalidated bytes rather than a second rendering.
    - A set published under a superseded `manifest_version` is never matching, never current, never overwritten in place, and reported as a residual path.
59. `view serve` serves §30's mirror and gap-question views over loopback and nothing else:
    - It binds one literal loopback address, refuses a wildcard, routable, or name-resolved bind and an ephemeral port in exit class 2 before a socket exists, tries no other port or interface after a refused bind, opens no outbound connection, and answers only requests carrying its own bound authority and no other declared origin, so a rebound external name or a page elsewhere reads nothing.
    - Every response declares `Cache-Control: no-store`, so a revisited URL that reaches the network is re-resolved rather than replayed from a cache, and carries its literal outcome class in `Exp2Res-View-Outcome`, which a bodyless `HEAD` and a `--quiet` run keep; the served mirror body carries no outcome metadata at all.
    - It is read-only under §8.1: no writer lock, no §13.14 preamble, no business row, managed output, telemetry row, or §15 call, and each request performs its own §12.14 compatibility read and state reads inside one read transaction with nothing cached between requests.
    - Every request resolves one explicit URL selector — an assessment-view identity or one snapshot ID, never a default, an omitted-selector fallback, or a latest-of-several rule, and never a project-scoped view through either form — then performs §13.14's complete current-output revalidation and the §16.11 gate before any content is emitted.
    - The mirror body is byte-identical to the revalidated `report.html` member, and the gap-question page presents only the `question` values of that same set's `self_claims.json` `unknowns` entries with `answered = false`, rendered under §17's escaping and inertness rules.
    - Only `GET` and `HEAD` are answered — `HEAD` after the same resolution and revalidation, with the `GET` status and headers and an empty body — while every other method wins before the declared-body check, an otherwise accepted request declaring a body is malformed, and every route outside the closed set is refused before any state is read.
    - A missing, superseded, ambiguous, stale, invalidated, or unsupported-`manifest_version` state, an incompatible schema, SQLite contention beyond §8.1's `busy_timeout`, and a §16.11-blocked assessment each produce their own owner-visible fail-closed outcome from §30 rule 7's closed table, under that row's stable literal class, naming the §14 command that resolves it where one exists — the §14.9 generation only for an identity selector, and §14.1's migration only where §12.14 offers one — and inventing none where it does not, with the completed semantic refusal distinguishable from every operational failure; the ordered checks give a request matching several rows exactly one outcome, so a §16.11-blocked snapshot whose set is also stale completes as that refusal.
    - No response body, header, page, or diagnostic line reflects request bytes or exposes a gap ID, another companion field, a workspace or managed path, `raw_text`, or any other unprojected value; a remedy command may name only a snapshot ID resolution proved current.
    - Loopback binding is the whole trust boundary — no local peer is authenticated — and §29.6 records that stated limit rather than a session, credential, or capability check.
    - The raw request envelope is capped before parsing at an 8192-octet request line including CRLF, a 32768-octet header section including its terminating empty line, and 64 field lines, cumulatively across fragmented reads; any overflow is `malformed_request` before state access.
    - The startup §12.14 gate fails before bind, and a schema change after bind is caught by the repeated per-request gate before business state.
    - A matching-digest `self_claims.json` that fails its closed schema produces `question_companion_invalid` and names re-export rather than falling through to `internal_error`.
    - On first interruption one absolute §8.1-timeout drain deadline covers receipt, resolution, filesystem revalidation, composition, and emission without restart; work completed inside it keeps its closed outcome, while unfinished work closes without retry at the deadline and cannot delay the class-9 envelope, even though a begun emission may leave the peer a partial response.
    - Header parsing accepts only rule 9's CRLF-terminated ASCII field-line grammar: no bare newline, obsolete fold, whitespace before the colon, non-token field name, forbidden control, DEL, or non-ASCII value byte is tolerated or normalized. `OPTIONS` follows the ordinary method refusal rather than a framework automatic response.
    - Only an HTTP/1.1 origin-form target with exactly one syntactically valid `Host` reaches authority and route matching; another target form, version, or Host cardinality is `malformed_request` without parser normalization.
    - At most 32 connections enter parsing or request handling, with no application queue; excess sockets close unread and unanswered until a slot is released, and every response declares `Connection: close` after that connection's sole request.
    - Both routes' `HEAD` requests run the corresponding GET validation and return its status and headers with no body.
    - An ordinary emit timeout may truncate delivery only after the outcome is composed and the read transaction closes, and never changes that outcome or starts another response.
    - A duplicated current view remains `assessment_inconsistent` but names no generation remedy, because §13.6 is not a corruption-repair surface.
    - Successful startup reports exactly the usable global-identity mirror and question URLs under the selected literal host and port, on standard error in both human and JSON modes; interruption exits 9 in both.
    - A decoded snapshot selector must match §13.14 rule 1's 1–128-byte lowercase ASCII ID grammar before lookup.
    - After complete receipt, one absolute processing deadline of three times §8.1's timeout spans interpretation through response composition; the first SQLite read starts only with two full contention timeouts remaining, so its complete wait leaves one full timeout of slack to compose per-request `workspace_busy` while the server continues, and other expiry releases unfinished work and produces `processing_timeout`, leaving no request resource behind its admission slot.
60. `assess repair` regenerates a current, fully verified snapshot deterministically:
    - Fail-closed preconditions with stable class-2 diagnostics (`snapshot_not_verified`, `nothing_to_repair`, `rewrite_unavailable`, beside the shared selector classes) change nothing.
    - One ordinary §13.6 swap produces a complete new `unverified` claim generation in which each `rejected` or `unsupported` claim adopts exactly its latest finding's non-null `suggested_rewrite` while every other claim's text copies byte-for-byte, counterevidence resets, `gap_question_ids` and `contradiction_ids` recompute from current rows, and `summary` copies the possibly repaired `narrative_summary` member.
    - `repaired_from_snapshot_id` and `adopted_rewrite_of_claim_id` are inert provenance metadata.
    - One non-LLM stage-`13.6` run row with NULL execution identity owns zero `llm_calls` rows and no provider transit.
    - Export is reachable again only through a complete ordinary Stage 7 re-verification of the new snapshot.

---
