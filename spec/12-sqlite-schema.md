## §12. SQLite Schema

The SQLite schema is derived from the Pydantic models in §11; §11 is the normative source for all mirrored entities and fields. Every database connection must execute `PRAGMA foreign_keys = ON` and verify that it took effect before reading or writing lifecycle-managed data. The §12.14 migration connection is the sole exception: it may disable enforcement before its transaction for a registered table-rebuild migration, but it must pass `PRAGMA foreign_key_check` before commit. Derivation rules:

1. Tables mirror the Pydantic models 1:1: RawLog → raw_logs, EvidenceItem → evidence_items, ExperienceFact → experience_facts, SelfSignal → self_signals, SelfClaim → self_claims, Contradiction → contradictions, GapQuestion → gap_questions, AssessmentSnapshot → assessment_snapshots, JobDescription → job_descriptions, ResumeBranch → resume_branches, ResumeBullet → resume_bullets. Column names match field names; required fields are NOT NULL.
2. List/dict fields are stored as JSON TEXT columns named `<field>_json`, NOT NULL with DEFAULT '[]' / '{}'. Embedded Pydantic fields use the same convention: `JobDescription.parsed` derives to a required `parsed_json` JSON TEXT column whose decoded value must validate as `ParsedJD` (§11.13). JSON storage does not waive typed-model or typed-reference validation in rule 10.
3. datetime fields are stored as ISO 8601 TEXT.
4. bool fields are stored as INTEGER 0/1, NOT NULL; a model default becomes the column DEFAULT (GapQuestion.answered → answered INTEGER NOT NULL DEFAULT 0).
5. An embedded OccurredAt is flattened into occurred_start, occurred_end, temporal_precision, temporal_confidence columns; `temporal_precision` is the sole shape discriminator under §11.1.
6. Scalar references to other entities become FOREIGN KEY columns. `evidence_items.raw_log_id` references `raw_logs.id ON DELETE CASCADE`; `raw_logs.corrects_log_id` is a self-reference with `ON DELETE SET NULL`; and `gap_questions.answer_log_id` references `raw_logs.id ON DELETE SET NULL`. The required `ResumeBranch.assessment_snapshot_id` derives as `TEXT NOT NULL REFERENCES assessment_snapshots(id)`; its stronger current-anchor check is rule 10. The required `ResumeBranch.job_description_id` derives as `TEXT NOT NULL REFERENCES job_descriptions(id)`; its stronger exact-selection check is rule 10. Job descriptions are retained context, not lifecycle-managed data, and §13.13 owner deletion does not delete them, so this foreign key cannot block owner deletion. Other scalar references use the default restrictive action. No foreign key may block the owner-deletion operation in §13.13.
7. A polymorphic reference — an (`*_type: DetectionRefType`, `*_id`) field pair such as Contradiction.left_ref_* / right_ref_* or GapQuestion.target_* — becomes two plain TEXT NOT NULL columns with no FOREIGN KEY because the target table varies per row; rule 10 supplies its write-time integrity check.
8. Exception: `ExperienceFact.source_log_ids` and `evidence_item_ids` are not stored as columns. They are non-empty, duplicate-free views hydrated from the `fact_sources → evidence_items` relation in §12.4.
9. Queries that feed processing, verification, generation, or export must filter every recomputable table to `superseded_at IS NULL`. Historical inspection is the only normal read path that may include superseded rows.
10. Before a domain-entity batch commits, every typed ID below must resolve to the required target, either in current pre-existing state or among rows inserted earlier in the same transaction. A missing ID, wrong target type, superseded target, or duplicate ID fails the producing operation atomically. The same rule validates `Contradiction.left_ref_*` / `right_ref_*` and `GapQuestion.target_*` against the table selected by their `DetectionRefType`, and each `SelfClaim.counterevidence` entry against the table selected by its `CounterevidenceRefType` — additionally requiring membership in that claim's supplied §15.5 bundle and rejecting duplicate (`source_ref_type`, `source_ref_id`) pairs. On `JobDescription` persistence it also rejects an empty `JDRequirement.id` or an ID duplicated within the candidate or any retained `JobDescription`; on Stage 10 persistence it requires the candidate branch's `job_description_id` to equal the exact §14.10 selection and resolves every matched requirement through that persisted job description's `ParsedJD`, not merely against any job description.
11. Every top-level §11 entity `id` derives to `TEXT PRIMARY KEY`, the unique parent key used by rule 6 foreign keys and rule 10 resolution; `fact_sources` keeps its composite primary key and `processing_runs` its §12.13 primary key. The service assigns each non-empty, opaque value — after a valid model response for an LLM-backed producer — and the value is immutable for the workspace lifetime, unique forever within its entity table (including current, superseded, and historical rows), and never reassigned in that table after supersession or a §13.13 purge. Cross-table uniqueness is not required because each scalar foreign key is table-scoped and each polymorphic reference's closed `DetectionRefType` or `CounterevidenceRefType` selects exactly one table for rule 10; §12.13 `input_ids_json`/`output_ids_json` remain opaque telemetry rather than references, and `JDRequirement.id` retains its stronger §11.13/rule 10 global-uniqueness rule. A collision with any retained row in that entity table follows §15.1 deterministic enrichment: retry locally with a fresh ID when safe or fail the producing run atomically, never with another LLM call; because retained processing telemetry outlives owner-deletion purges and reuse would make an opaque historical ID silently appear to name an unrelated entity, allocation must be collision-resistant, include a random component, and prevent reuse of purged IDs, while row-count, `MAX + 1`, or other surviving-row-derived IDs are non-conforming. Natural replacement-key uniqueness is separate from entity identity and remains governed by the Stage 6 and Stage 10 transaction checks below; locking is governed by §8.1, and this rule defines no natural-key index.
12. With all writers serialized under §8.1, rule 10 and the Stage 6 and Stage 10 transaction checks are race-free and remain the enforcement for one-current-per-identity invariants. Because `ResumeBranch.name` is stored directly, the schema also derives this database-enforced backstop for current named branches:

    ```sql
    CREATE UNIQUE INDEX resume_branches_current_name_unique
    ON resume_branches(name)
    WHERE superseded_at IS NULL;
    ```

    Assessment-view identity depends on the locale-independent case-folded canonical `scope_target` (§11.7), which SQLite cannot fold deterministically in an index; one-current-per-view enforcement therefore remains the Stage 6 transaction check under the workspace writer lock.

| Typed reference fields | Required current target |
|---|---|
| `SelfSignal.supporting_fact_ids`, `counter_fact_ids` | `experience_facts` |
| `SelfClaim.source_signal_ids` | `self_signals` |
| `SelfClaim.source_fact_ids` | `experience_facts` |
| `SelfClaim.counterevidence[].source_ref_type` / `source_ref_id` | the table selected by `CounterevidenceRefType`; the target must belong to that claim's supplied §15.5 bundle |
| `AssessmentSnapshot.self_claim_ids` | `self_claims` |
| `AssessmentSnapshot.gap_question_ids` | `gap_questions` |
| `AssessmentSnapshot.contradiction_ids` | `contradictions` |
| `ResumeBranch.assessment_snapshot_id` | `assessment_snapshots` |
| `ResumeBranch.job_description_id` | retained `job_descriptions` row |
| `ResumeBullet.branch_id` | `resume_branches` |
| `ResumeBullet.source_fact_ids` | `experience_facts` |
| `ResumeBullet.source_log_ids` | retained `raw_logs` |
| `ResumeBullet.source_self_claim_ids` | `self_claims` |
| `ResumeBullet.matched_jd_requirements` | `JDRequirement` members reached through its Stage 10 branch's exact `job_description_id` |

Stage 6 adds complete-state checks at the same transaction boundary: after candidate inserts and supersession transitions are staged, every current `SelfClaim.id` must occur in exactly one current `AssessmentSnapshot.self_claim_ids`, and every listed claim must be current. Each snapshot must contain exactly one `narrative_summary` claim whose text equals `AssessmentSnapshot.summary`. Its `gap_question_ids` must equal both the complete duplicate-free current unanswered Stage 4 gap set and the IDs in the validated §15.4 `unknowns` output; at most one current snapshot exists per assessment view (§11.7), and a project-scoped snapshot must carry the canonical non-blank §14.9 project selector in `scope_target`. Sharing a current claim between snapshots, leaving one unowned, exposing unmatched summary prose, including an answered gap, omitting an unanswered gap, storing a free-form unknown, or changing the scope target fails the batch before commit.

Stage 10 adds one branch-anchor consistency check at that boundary: the candidate `ResumeBranch.assessment_snapshot_id` must equal the exact snapshot selected under §18, its `job_description_id` must equal the exact §14.10 `--jd` selection, every candidate bullet must reference that branch, and every `ResumeBullet.source_self_claim_ids` value must occur in the selected snapshot's `self_claim_ids`. The claim list is duplicate-free under rule 10 and must equal the service-selected claim IDs passed to the writer under §13.10/§15.6. Every `matched_jd_requirements` value must occur exactly once in the `ParsedJD.requirements` reached through that branch job-description ID. A branch that merely names one snapshot while consuming a claim owned by another, loses its selected job description, or names a missing, duplicate, or wrong-job requirement fails before any branch or bullet becomes current.

`processing_runs.input_ids_json` and `output_ids_json` are the explicit exception: they are opaque historical telemetry, not typed domain references, and are not subject to rule 10.

Only three tables have no Pydantic counterpart; all three are storage artifacts rather than §9.1 ontology entities, and their DDL is normative here: fact_sources (§12.4) — the relational representation of fact provenance; processing_runs (§12.13) — pipeline execution telemetry, not entity creation provenance; and schema_meta (§12.14) — schema-version history and compatibility metadata. Retired subsection numbers (§12.1–§12.3, §12.5–§12.12) are never reused; the dated registry lives in the map's § Index.

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

`raw_log_id` is not duplicated here: a fact's raw sources are the distinct `EvidenceItem.raw_log_id` values reached through its rows. One row represents one selected evidence item, so multiple evidence items from the same raw log produce multiple rows and one evidence item cannot carry two support types for the same fact. Every fact must have at least one `direct` row. `direct` means the fact was extracted from that item; `corroborating` means an additional item independently supports the same fact and cannot establish it alone. Stage 3 creates the direct rows; V1 defines no separate corroboration command, so automation may not silently add corroborating links. `ExperienceFact.evidence_item_ids` is exactly the row set, and `source_log_ids` is exactly the distinct reached raw-log set.

This non-null evidence link supplies every selected item's scoped `EvidenceStrength` to §9.4 confidence calibration without making same-log rows independent, and prevents a mismatched raw-log/evidence-item pair. The cascades `raw_logs → evidence_items → fact_sources` support the privacy reset; §13.13 removes every dependent JSON-linked derived row before the selected raw record is deleted.

## §12.13 processing_runs

```sql
CREATE TABLE IF NOT EXISTS processing_runs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    input_ids_json TEXT NOT NULL DEFAULT '[]',
    output_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

`stage` records the stable subsection identifier of an active §13 stage, such as `13.3`. Retired identifiers may remain in historical rows, but no new processing run may use them. Processing telemetry alone does not make an operation a pipeline stage. `input_ids_json` and `output_ids_json` are historical telemetry rather than live provenance: owner deletion retains these rows, and their opaque IDs may therefore stop resolving after a privacy reset. `metadata_json` may contain stage/config identifiers, statuses, and diagnostic codes, but must not duplicate raw text, evidence summaries, derived prose, or export content that owner deletion is required to purge.

Rule 10 validation occurs before candidate business outputs commit. On failure, the candidate transaction is rolled back, the run finishes with `status = "failed"` and `output_ids_json = []`, and `metadata_json` records the offending field, ID, and expected target type without copying source or derived content. §13.13 determines whether an earlier current generation remains available after a source-changing lifecycle operation.

## §12.14 schema_meta

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    app_version TEXT NOT NULL
);
```

`schema_meta` is append-only schema history. A fresh §14.1 initialization inserts the row for the schema version it creates, and every applied migration appends one row for its target version with the application time and running application version; neither path updates or deletes a prior row. The authoritative current workspace schema version is `MAX(schema_meta.version)`. `PRAGMA user_version` is not read or written as a version authority and may not become a second source. Each application build declares one supported current schema version and an ordered registry of migrations between versions.

Every command's first connection to an existing workspace must establish compatibility from `schema_meta` before any business-table read or any database write. The minimal metadata read needed to classify the workspace is not business I/O. Let `S` be the authoritative stored version and `C` the build's supported current version:

| Workspace state | Required behavior |
|---|---|
| `schema_meta` is missing, empty, malformed, or unreadable | Fail closed as an unrecognized workspace. No business table is read and no write occurs. |
| `S = C` | The compatibility gate passes; the command may proceed under its own contract. |
| `S < C` and the registry contains a complete ordered path from `S` to `C` | Every business command fails closed before business I/O and points at the §14.1 migration command. Only the §14.1 status and migration surfaces may open the older workspace; status reports without writing. |
| `S < C` and no complete registered path exists | Status may report the stored version and missing path without writing; every other operation fails closed before business I/O with guidance to use an application build that supplies the path or restore a recognized backup. No migration statement runs. |
| `S > C` | Every command fails closed as a workspace from a newer Exp2Res and directs the owner to upgrade the application. Beyond the compatibility metadata read, no business table is read and no write occurs. |

Migration is explicit and runs only through the §14.1 migration command. No other command may apply a migration or rewrite the workspace as an on-open side effect. Before the first migration statement, the migration command creates a local managed backup inside the workspace, for example `.exp2res/backup/exp2res-v<from>-<UTC timestamp>.sqlite`, using the SQLite backup API or an equivalent procedure that incorporates committed WAL content, checkpointing the WAL before copying when the equivalent requires it. It then verifies that `PRAGMA integrity_check` on the backup passes and that the backup's authoritative `schema_meta` version equals the pre-migration version. Creation or verification failure aborts before any migration statement and leaves the pre-migration workspace unchanged. Once verified, the backup remains managed workspace state after success or failure and is governed by §13.13 owner deletion.

One migration-command invocation applies every pending registered migration, appends every corresponding `schema_meta` row, and performs final validation inside one transaction. After the last migration and still inside that transaction, it validates every retained model-backed row against the target §11 models and §10 enum aliases, runs `PRAGMA foreign_key_check`, and re-verifies every one-current-generation invariant defined by §11, this section, and the producing-stage and lifecycle rules in §13. Any migration or final-validation failure rolls back the whole transaction, so no partial business schema or version history becomes visible.

Every migration must transform retained rows so that they validate under the target §11 models and the canonical §10 enum aliases. A row that cannot be transformed deterministically fails the migration; it is never dropped or silently defaulted. An enum rename is a deterministic value rewrite; an enum member may be removed only when no retained row carries it or a deterministic rewrite is defined. A new required field is legal only with a deterministic backfill. A migration may change storage representation but must never change the owner-visible content of a raw record: `raw_text` remains byte-for-byte identical, and metadata, timestamps, and every other hydrated `RawLog` value are value-preserved. §5.3's automation-immutability rule applies to migrations. Migrations preserve all provenance links, current and superseded state, and the one-current-generation invariants.

On failure, `schema_meta` and the database remain at the original version, the original database remains usable through the compatibility and recovery surfaces, and the verified backup is retained. The diagnostic names the failing migration or final validation and the backup path. Business reads and writes remain blocked until a later migration succeeds. Recovery is an explicit retry after upgrading the application or a manual restore of the reported backup.

---
