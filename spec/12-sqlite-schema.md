## §12. SQLite Schema

The SQLite schema is derived from the Pydantic models in §11; §11 is the normative source for all mirrored entities and fields. Every database connection must execute `PRAGMA foreign_keys = ON` and verify that it took effect before reading or writing lifecycle-managed data. The §12.14 migration connection is the sole exception: it may disable enforcement before its transaction for a registered table-rebuild migration, but it must pass `PRAGMA foreign_key_check` before commit. Derivation rules:

1. Tables mirror the Pydantic models 1:1: RawLog → raw_logs, EvidenceItem → evidence_items, ExperienceFact → experience_facts, SelfSignal → self_signals, SelfClaim → self_claims, Contradiction → contradictions, GapQuestion → gap_questions, AssessmentSnapshot → assessment_snapshots, JobDescription → job_descriptions, ResumeBranch → resume_branches, ResumeBullet → resume_bullets, VerificationFinding → verification_findings. Column names match field names; required fields are NOT NULL.
2. List/dict fields are stored as JSON TEXT columns named `<field>_json`, NOT NULL with DEFAULT '[]' / '{}'. Embedded Pydantic fields use the same convention: `JobDescription.parsed` derives to a required `parsed_json` JSON TEXT column whose decoded value must validate as `ParsedJD` (§11.13). JSON storage does not waive typed-model or typed-reference validation in rule 10; hydration enforces the same §11 model policy and boundary limits, and stored JSON is not grandfathered around validation or limits.
3. Every datetime field is stored as the validated offset-aware value's ISO 8601 TEXT with its original UTC offset, encoded as `Z` or numeric `±hh:mm`; the stored text preserves both the UTC instant and the offset supplied by the owner, source, or service. Hydration re-validates offset-awareness under §11. Every datetime equality, ordering, duration, or comparison uses the UTC instant, never the stored TEXT bytes — including §13.3 correction-lineage and governing-record order, §16.7 interval arithmetic, and any non-null `superseded_at` cutoff comparison. Two texts with different offsets may denote one equal instant, and byte order is not instant order. §11's canonical serialization remains the sole hash-byte rule.
4. bool fields are stored as INTEGER 0/1, NOT NULL; a model default becomes the column DEFAULT (GapQuestion.answered → answered INTEGER NOT NULL DEFAULT 0).
5. An embedded OccurredAt is flattened into occurred_start, occurred_end, temporal_precision, temporal_confidence columns; `temporal_precision` is the sole shape discriminator under §11.1.
6. Scalar references to other entities become FOREIGN KEY columns. `evidence_items.raw_log_id` references `raw_logs.id ON DELETE CASCADE`; `raw_logs.corrects_log_id` is a self-reference with `ON DELETE SET NULL`; and `gap_questions.answer_log_id` references `raw_logs.id ON DELETE SET NULL`. The required `SelfClaim.snapshot_id` derives as `TEXT NOT NULL REFERENCES assessment_snapshots(id)`: one row has one owning snapshot, so claim sharing between snapshots and unowned claims are unrepresentable (§11.6), and the Stage 6 checks below replace any reverse-list validation. The required `ResumeBranch.assessment_snapshot_id` derives as `TEXT NOT NULL REFERENCES assessment_snapshots(id)`; its stronger current-anchor check is rule 10. The required `ResumeBranch.job_description_id` derives as `TEXT NOT NULL REFERENCES job_descriptions(id)`; its stronger exact-selection check is rule 10. `VerificationFinding.produced_by_run_id` derives as `TEXT NOT NULL REFERENCES processing_runs(id)`. Raw-log owner deletion retains job descriptions. `jd delete` (§14.15) removes every dependent finding, bullet, and branch in its §13.13 transaction before deleting the selected job description, so this foreign key blocks neither privacy operation. Other scalar references use the default restrictive action. No foreign key may block an owner-deletion operation in §13.13.
7. A polymorphic reference — an (`*_type`, `*_id`) field pair typed by `DetectionRefType` or `VerificationTargetRefType`, such as `Contradiction.left_ref_*` / `right_ref_*`, `GapQuestion.target_*`, or `VerificationFinding.target_*` — becomes two plain TEXT NOT NULL columns with no FOREIGN KEY because the target table varies per row; rule 10 supplies its write-time integrity check.
8. Exception: `ExperienceFact.source_log_ids` and `evidence_item_ids` are not stored as columns. They are non-empty, duplicate-free views hydrated from the `fact_sources → evidence_items` relation in §12.4.
9. Queries that feed processing, verification, generation, or export must filter every recomputable table to `superseded_at IS NULL`. Historical inspection is the only normal read path that may include superseded rows.
10. Before a domain-entity batch commits, every typed ID below must resolve to the required target, either in current pre-existing state or among rows inserted earlier in the same transaction. For a Stage 3 candidate, the same pre-commit check also enforces §13.3 rule 6's evidence-selectability predicate; reference resolution alone is insufficient. A missing ID, wrong target type, superseded target, duplicate ID, or unselectable Stage 3 evidence item fails the producing operation atomically. The same rule validates `Contradiction.left_ref_*` / `right_ref_*` and `GapQuestion.target_*` against the table selected by their `DetectionRefType`, `VerificationFinding.target_*` against the table selected by its `VerificationTargetRefType`, and each `SelfClaim.counterevidence` or `VerificationFinding.counterevidence` entry against the table selected by its `CounterevidenceRefType` — additionally requiring verifier counterevidence membership in the supplied bundle that produced it and rejecting duplicate (`source_ref_type`, `source_ref_id`) pairs. On `JobDescription` persistence it also rejects an empty `JDRequirement.id` or an ID duplicated within the candidate or any retained `JobDescription`; on Stage 10 persistence it requires the candidate branch's `job_description_id` to equal the exact §14.10 selection and resolves every matched requirement through that persisted job description's `ParsedJD`, not merely against any job description.
11. Every top-level §11 entity `id` derives to `TEXT PRIMARY KEY`, the unique parent key used by rule 6 foreign keys and rule 10 resolution; `fact_sources` keeps its composite primary key, `processing_runs` its §12.13 primary key, and `llm_calls` its §12.15 composite primary key. The service assigns each non-empty, opaque value — after a valid model response for an LLM-backed producer — and the value is immutable for the workspace lifetime, unique forever within its entity table (including current, superseded, and historical rows), and never reassigned in that table after supersession or a §13.13 purge. Cross-table uniqueness is not required because each scalar foreign key is table-scoped and each polymorphic reference's closed `DetectionRefType`, `CounterevidenceRefType`, or `VerificationTargetRefType` selects exactly one table for rule 10; §12.13 `input_ids_json`/`output_ids_json` remain opaque telemetry rather than references, and `JDRequirement.id` retains its stronger §11.13/rule 10 global-uniqueness rule. A collision with any retained row in that entity table follows §15.1 deterministic enrichment: retry locally with a fresh ID when safe or fail the producing run atomically, never with another LLM call; because retained processing telemetry outlives §13.13 point-deletion purges and reuse would make an opaque historical ID silently appear to name an unrelated entity, allocation must be collision-resistant, include a random component, and prevent reuse of purged IDs, while row-count, `MAX + 1`, or other surviving-row-derived IDs are non-conforming. Natural replacement-key uniqueness is separate from entity identity and remains governed by the Stage 6 and Stage 10 transaction checks below; locking is governed by §8.1, and this rule defines no natural-key index.
12. With all writers serialized under §8.1, rule 10 and the Stage 6 and Stage 10 transaction checks are race-free and remain the enforcement for one-current-per-identity invariants. Because `ResumeBranch.name` is stored directly, the schema also derives this database-enforced exact-name backstop for current named branches:

    ```sql
    CREATE UNIQUE INDEX resume_branches_current_name_unique
    ON resume_branches(name)
    WHERE superseded_at IS NULL;
    ```

    Assessment-view identity depends on the locale-independent case-folded canonical `scope_target` (§11.7), which SQLite cannot fold deterministically in an index; one-current-per-view enforcement therefore remains the Stage 6 transaction check under the workspace writer lock. Branch replacement identity is likewise canonical — the NFC case-folded `name` (§14.10), which this raw-name index cannot express — so folded one-current-per-branch enforcement is the Stage 10 transaction check under the same lock, and the index remains the coarser exact-spelling backstop.

13. Every row in the eight recomputable tables — `experience_facts`, `self_signals`, `self_claims`, `assessment_snapshots`, `resume_bullets`, `contradictions`, `gap_questions`, and `resume_branches` — additionally derives these service-set storage columns:

    ```sql
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    generation_id TEXT NOT NULL
    ```

    `produced_by_run_id` names the §13-stage run whose validated output the row belongs to. One fresh opaque `generation_id` is allocated per atomic business replacement batch and shared by every row committed in that swap: Stage 3 allocates one per correction lineage, so a full extraction over N lineages allocates N generation IDs; Stage 4 uses one shared ID for its jointly replaced gap and contradiction sets; Stage 5 uses one for the signal generation; Stage 6 uses one for the jointly swapped claims and snapshot of one assessment view; and Stage 10 uses one for the jointly swapped branch and bullets. A Stage 4 run that retains the prior generation allocates no generation ID because retention produces no rows. Generation IDs follow rule 11's allocation contract: they are opaque, collision-resistant, and never reused, including after purge. Supersession, verification, or any other lifecycle transition never changes either column; they record production, not lifecycle.

    These fields have no §11 model counterpart because neither value may cross the LLM boundary: every §15 input is a §11 shape or a contract-declared projection of one (§11), and adding model fields would widen every §29.3 transmission row for no semantic gain. They are service-owned inspection/provenance state, like `fact_sources`, and are hydrated only by inspection surfaces.

14. `raw_logs` and `experience_facts` — the tables storing copied project provenance — additionally derive one service-set storage column:

    ```sql
    project_key TEXT
    ```

    `project_key` is `NULL` if and only if the row's `project` is `NULL`. The value is computed by the one service-owned canonical project-key function — Unicode NFC normalization, then leading/trailing Unicode-whitespace trim, then locale-independent Unicode Default Case Folding (§11's comparison identity, §14.9's canonicalization) — applied to the row's exact persisted `project` value, which itself remains untransformed provenance. Capture and import compute the key when the row is persisted; Stage 3 copies `project` and `project_key` together from each fact's governing record (§13.3 rule 13). A non-null `project` that canonicalizes to blank is invalid under §11's Model validation policy, so every stored key is non-empty. Hydration of a row carrying the column re-validates that the stored key equals that function applied to the stored label and fails closed on disagreement (rule 2). Project-view subject selection and every other project-identity comparison consume the stored key against the case-folded canonical selector (§13.6); no comparison site re-implements normalization over labels. Like rule 13's columns, `project_key` has no §11 model counterpart and never crosses the LLM boundary (§11, §15.11): it is service-owned comparison identity, not provenance, display, or export content — owner-facing output renders `project`, and the key may appear only in diagnostics. V1 has no project-label registry and no Project entity.

| Typed reference fields | Required current target |
|---|---|
| `SelfSignal.supporting_fact_ids`, `counter_fact_ids` | `experience_facts` |
| `SelfClaim.source_signal_ids` | `self_signals` |
| `SelfClaim.source_fact_ids` | `experience_facts` |
| `SelfClaim.counterevidence[].source_ref_type` / `source_ref_id` | the table selected by `CounterevidenceRefType`; the target must belong to that claim's supplied §15.5 bundle |
| `VerificationFinding.produced_by_run_id` | `processing_runs` |
| `VerificationFinding.target_type` / `target_id` | the current `self_claims` or `resume_bullets` row selected by `VerificationTargetRefType` at finding commit |
| `VerificationFinding.counterevidence[].source_ref_type` / `source_ref_id` | the table selected by `CounterevidenceRefType`; the target must belong to the verifier bundle that produced the finding |
| `SelfClaim.snapshot_id` | `assessment_snapshots` (rule 6 FK; the claim's one owning snapshot) |
| `AssessmentSnapshot.gap_question_ids` | `gap_questions` |
| `AssessmentSnapshot.contradiction_ids` | `contradictions` |
| `ResumeBranch.assessment_snapshot_id` | `assessment_snapshots` |
| `ResumeBranch.job_description_id` | retained `job_descriptions` row |
| `ResumeBullet.branch_id` | `resume_branches` |
| `ResumeBullet.source_fact_ids` | `experience_facts` |
| `ResumeBullet.source_log_ids` | retained `raw_logs` |
| `ResumeBullet.source_self_claim_ids` | `self_claims` |
| `ResumeBullet.matched_jd_requirements` | `JDRequirement` members reached through its Stage 10 branch's exact `job_description_id` |

Stage 6 adds complete-state checks at the same transaction boundary: after candidate inserts and supersession transitions are staged, every current `SelfClaim` must belong to a current `AssessmentSnapshot` through its `snapshot_id` — the rule 6 FK already makes sharing and unowned claims unrepresentable, so the transaction verifies only that every claim owned by a snapshot superseded in this swap was superseded with it and that each candidate claim names the new snapshot; no reverse-list scan over prior snapshots exists. Each snapshot must contain exactly one current `narrative_summary` member claim whose text equals `AssessmentSnapshot.summary`. Its `gap_question_ids` must equal the complete duplicate-free current unanswered Stage 4 gap set Stage 6 supplied to the writer (§15.11); at most one current snapshot exists per assessment view (§11.7), and a project-scoped snapshot must carry the canonical non-blank §14.9 project selector in `scope_target`. A candidate claim naming any snapshot other than the new one, a claim of the superseded snapshot left current, unmatched summary prose, including an answered gap, omitting an unanswered gap, storing a free-form unknown, or changing the scope target fails the batch before commit.

Stage 10 adds one branch-anchor consistency check at that boundary: the candidate `ResumeBranch.assessment_snapshot_id` must equal the exact snapshot selected under §18, its `job_description_id` must equal the exact §14.10 `--jd` selection, every candidate bullet must reference that branch, and every `ResumeBullet.source_self_claim_ids` value must occur in the selected snapshot's `self_claim_ids`. The claim list is duplicate-free under rule 10 and must equal the service-selected claim IDs passed to the writer under §13.10/§15.6. Every `matched_jd_requirements` value must occur exactly once in the `ParsedJD.requirements` reached through that branch job-description ID. A branch that merely names one snapshot while consuming a claim owned by another, loses its selected job description, or names a missing, duplicate, or wrong-job requirement fails before any branch or bullet becomes current.

`processing_runs.input_ids_json` and `output_ids_json` are the explicit exception: they are opaque historical telemetry, not typed domain references, are not subject to rule 10, and are never substitutes for rule 13's live production provenance.

Only four tables have no Pydantic counterpart; all four are storage artifacts rather than §9.1 ontology entities, and their DDL is normative here: fact_sources (§12.4) — the relational representation of fact provenance; processing_runs (§12.13) — execution telemetry and the production-provenance anchor; schema_meta (§12.14) — schema-version history and compatibility metadata; and llm_calls (§12.15) — per-provider-call execution telemetry. Retired subsection numbers (§12.1–§12.3, §12.5–§12.12) are never reused; the dated registry lives in the map's § Index.

## §12.4 fact_sources

```sql
CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id TEXT NOT NULL,
    evidence_item_id TEXT NOT NULL,
    support_type TEXT NOT NULL CHECK (support_type IN ('direct', 'corroborating')),

    PRIMARY KEY (fact_id, evidence_item_id),

    FOREIGN KEY (fact_id) REFERENCES experience_facts(id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_item_id) REFERENCES evidence_items(id) ON DELETE CASCADE
);
```

`raw_log_id` is not duplicated here: a fact's raw sources are the distinct `EvidenceItem.raw_log_id` values reached through its rows. One row represents one selected evidence item, so multiple evidence items from the same raw log produce multiple rows and one evidence item cannot carry two support types for the same fact. Every fact must have at least one `direct` row. `direct` means Stage 3 selected the item under §13.3 rule 6; for displaced-record support the label records selection, not prose or content origination. `corroborating` means an additional item independently supports the same fact and cannot establish it alone. Stage 3 creates the direct rows; V1 defines no separate corroboration command, so automation may not silently add corroborating links. `ExperienceFact.evidence_item_ids` is exactly the row set, and `source_log_ids` is exactly the distinct reached raw-log set.

At Stage 3 commit, rule 10 enforces §13.3 rule 6's exact selectability predicate; an unselectable item fails the complete lineage replacement atomically.

This non-null evidence link supplies every selected item's scoped `EvidenceStrength` to §9.4 confidence calibration without making same-log rows independent, and prevents a mismatched raw-log/evidence-item pair. The cascades `raw_logs → evidence_items → fact_sources` support the privacy reset; §13.13 removes every dependent JSON-linked derived row before the selected raw record is deleted.

## §12.13 processing_runs

```sql
CREATE TABLE IF NOT EXISTS processing_runs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    parent_run_id TEXT REFERENCES processing_runs(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_policy_hash TEXT,
    failure_code TEXT,
    input_ids_json TEXT NOT NULL DEFAULT '[]',
    output_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

`stage` records the stable subsection identifier of an active §13 stage, such as `13.3`, or `13.13` for the lifecycle orchestration row defined there. Retired identifiers may remain in historical rows, but no new processing run may use them. Legal telemetry identifier `13.13` does not make §13.13 a pipeline stage: processing telemetry alone still does not make an operation a stage. `parent_run_id` links each stage run invoked by a §13.13 lifecycle flow to that flow's one orchestration row; a direct single-stage command leaves it `NULL`.

The run-level execution-identity columns are privacy-safe metadata. `provider` and `model` identify the selected adapter and model, and `prompt_policy_hash` hashes the fixed contract instructions together with the structured-output schema revision. Every LLM-backed run must populate `provider`, `model`, and `prompt_policy_hash` before its first transport; its run-level `status` and `failure_code` form the durable attempt record. Per-call identity, input/output hashes, provider request IDs, token counts, cost, and transport/schema retry counts live only in the run's §12.15 rows. `failure_code` is a stable machine code analogous to `ContractWarning.type`, intentionally not a §10 enum; §15.10 owns the minimum LLM transport, capability, budget, context-overflow, and cancellation vocabulary. Non-LLM runs leave `provider`, `model`, and `prompt_policy_hash` `NULL`.

Invocation distinguishability and exact-recomputation identity are defined in §12.15. A `processing_runs` row carries identifiers, timestamps, statuses, provider/model/policy identity, entity-ID lists, and diagnostic codes only — never raw text, prompts, responses, evidence summaries, derived prose, export content, or credentials. `input_ids_json` and `output_ids_json` remain historical telemetry rather than live provenance: §13.13 point deletion retains the run rows, and their opaque IDs may therefore stop resolving after a privacy reset, while it redacts `input_hash` and `output_hash` on every retained §12.15 call row committed before the purge transaction. Whole-workspace purge removes the rows under §14.16. `metadata_json` may contain stage/config identifiers, statuses, and diagnostic codes under the same content prohibition.

Rule 10 and §15.1 validation occur before candidate business outputs commit. On failure, the candidate transaction is rolled back, the run finishes with `status = "failed"`, `output_ids_json = []`, and a stable `failure_code`; `metadata_json` may record the offending field, ID, and expected target type without copying source or derived content. A failed run owns no business rows or `VerificationFinding` rows. A failed verifier run also leaves every target update and all prior findings untouched; its failed run row is the durable attempt record. §13.13 determines whether an earlier current generation remains available after a source-changing lifecycle operation.

## §12.14 schema_meta

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    app_version TEXT NOT NULL
);
```

`schema_meta` is append-only schema history. A fresh §14.1 initialization inserts the row for the schema version it creates, and every applied migration appends one row for its target version with the application time and running application version; neither path updates or deletes a prior row. Whole-workspace purge (§14.16) is the sole lifecycle exception: it discards the purged database's history and inserts exactly one fresh row for the running build's current version, after which append-only history resumes. The authoritative current workspace schema version is `MAX(schema_meta.version)`. `PRAGMA user_version` is not read or written as a version authority and may not become a second source. Each application build declares one supported current schema version and an ordered registry of migrations between versions.

Every command's first connection to an existing workspace must establish compatibility from `schema_meta` before any business-table read or any database write. The minimal metadata read needed to classify the workspace is not business I/O. Let `S` be the authoritative stored version and `C` the build's supported current version:

| Workspace state | Required behavior |
|---|---|
| `schema_meta` is missing, empty, malformed, or unreadable | Fail closed as an unrecognized workspace. No business table is read and no write occurs. |
| `S = C` | The compatibility gate passes; the command may proceed under its own contract. |
| `S < C` and the registry contains a complete ordered path from `S` to `C` | Every business command fails closed before business I/O and points at the §14.1 migration command. Only the §14.1 status and migration surfaces may open the older workspace; status reports without writing. |
| `S < C` and no complete registered path exists | Status may report the stored version and missing path without writing; every other operation fails closed before business I/O with guidance to use an application build that supplies the path or restore a recognized backup. No migration statement runs. |
| `S > C` | Every command fails closed as a workspace from a newer Exp2Res and directs the owner to upgrade the application. Beyond the compatibility metadata read, no business table is read and no write occurs. |

Migration is explicit and runs only through the §14.1 migration command. No other command may apply a migration or rewrite the workspace as an on-open side effect. Before the first migration statement, the migration command creates a local managed backup inside the workspace, for example `.exp2res/backup/exp2res-v<from>-<UTC timestamp>.sqlite`, using the SQLite backup API or an equivalent procedure that incorporates committed WAL content, checkpointing the WAL before copying when the equivalent requires it. It then verifies that `PRAGMA integrity_check` on the backup passes and that the backup's authoritative `schema_meta` version equals the pre-migration version. Creation or verification failure aborts before any migration statement and leaves the pre-migration workspace unchanged. Once verified, the backup remains managed workspace state after success or failure and is governed by §13.13 point deletion and §14.16 workspace purge.

One migration-command invocation applies every pending registered migration, appends every corresponding `schema_meta` row, and performs final validation inside one transaction. After the last migration and still inside that transaction, it validates every retained model-backed row against the target §11 models and §10 enum aliases, runs `PRAGMA foreign_key_check`, and re-verifies every one-current-generation invariant defined by §11, this section, and the producing-stage and lifecycle rules in §13. Any migration or final-validation failure rolls back the whole transaction, so no partial business schema or version history becomes visible.

Every migration must transform retained rows so that they validate under the target §11 models and the canonical §10 enum aliases. A row that cannot be transformed deterministically fails the migration; it is never dropped or silently defaulted. An enum rename is a deterministic value rewrite; an enum member may be removed only when no retained row carries it or a deterministic rewrite is defined. A new required field is legal only with a deterministic backfill. A migration may change storage representation but must never change the owner-visible content of a raw record: `raw_text` remains byte-for-byte identical, and metadata, timestamps, and every other hydrated `RawLog` value are value-preserved. §5.3's automation-immutability rule applies to migrations. Migrations preserve all provenance links, current and superseded state, and the one-current-generation invariants.

On failure, `schema_meta` and the database remain at the original version, the original database remains usable through the compatibility and recovery surfaces, and the verified backup is retained. The diagnostic names the failing migration or final validation and the backup path. Business reads and writes remain blocked until a later migration succeeds. Recovery is an explicit retry after upgrading the application or a manual restore of the reported backup.

## §12.15 llm_calls

```sql
CREATE TABLE IF NOT EXISTS llm_calls (
    run_id TEXT NOT NULL REFERENCES processing_runs(id),
    call_index INTEGER NOT NULL CHECK (call_index >= 1),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    input_hash TEXT,
    output_hash TEXT,
    provider_request_id TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    reported_cost TEXT,
    transport_retries INTEGER,
    schema_retries INTEGER,
    failure_code TEXT,

    PRIMARY KEY (run_id, call_index)
);
```

Each LLM-backed `processing_runs` row owns one `llm_calls` row per planned provider invocation of the run's single §15 contract, with `call_index` a contiguous ordinal starting at 1 in invocation order. Granularity follows the owning stage's definition: Stage 3 owns one call per correction lineage, Stage 7 one per current claim, Stage 10 one per planned bullet, and Stage 11 one per current bullet; each single-invocation LLM stage — Stages 4, 5, 6, and 8 — owns exactly one row. Non-LLM runs and `13.13` orchestration rows own none.

Transport and schema retries of the same planned invocation update that call row's retry counters and never create another row; one call row is one logical invocation, not one HTTP attempt. Every call in a run executes under the run's single `provider`, `model`, and `prompt_policy_hash`; changing any of that configuration mid-run is non-conforming and requires a new run.

`input_hash` hashes the canonical serialization of that invocation's typed input payload and must be populated before that call's transport. `output_hash` must be populated once that call has validated output; a failed call leaves it `NULL` only when no validated output exists. Matching run execution configuration — `provider`, `model`, and `prompt_policy_hash` — plus matching call `input_hash` identifies exact recomputation of that invocation, without collapsing rows that remain distinct under (`run_id`, `call_index`). Any later-specified execution-configuration parameter, for example a generation parameter, joins this equivalence key when it is added to the run's configuration. An `llm_calls` row contains telemetry only: identifiers, timestamps, statuses, hashes, counts, accounting values, and stable failure codes. No column may contain raw text, prompts, responses, evidence summaries, derived prose, export content, credentials, or any other content payload.

Call rows are telemetry, never business rows. Their durability mirrors `processing_runs`: they persist for completed and failed calls even when the run's candidate business transaction rolls back. §13.13 point deletion retains the rows but sets `input_hash` and `output_hash` to `NULL` on every retained call row committed before its purge transaction; §14.16 workspace purge removes every call row.

---
