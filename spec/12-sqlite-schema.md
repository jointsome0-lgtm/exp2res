## §12. SQLite Schema

The SQLite schema is derived from the Pydantic models in §11; §11 is the normative source for all mirrored entities and fields. Every database connection must execute `PRAGMA foreign_keys = ON` and verify that it took effect before reading or writing lifecycle-managed data. Derivation rules:

1. Tables mirror the Pydantic models 1:1: RawLog → raw_logs, EvidenceItem → evidence_items, ExperienceFact → experience_facts, SelfSignal → self_signals, SelfClaim → self_claims, Contradiction → contradictions, GapQuestion → gap_questions, AssessmentSnapshot → assessment_snapshots, JobDescription → job_descriptions, ResumeBranch → resume_branches, ResumeBullet → resume_bullets. Column names match field names; required fields are NOT NULL.
2. List/dict fields are stored as JSON TEXT columns named `<field>_json`, NOT NULL with DEFAULT '[]' / '{}'. Embedded Pydantic fields use the same convention: `JobDescription.parsed` derives to a required `parsed_json` JSON TEXT column whose decoded value must validate as `ParsedJD` (§11.13). JSON storage does not waive typed-model or typed-reference validation in rule 10.
3. datetime fields are stored as ISO 8601 TEXT.
4. bool fields are stored as INTEGER 0/1, NOT NULL; a model default becomes the column DEFAULT (GapQuestion.answered → answered INTEGER NOT NULL DEFAULT 0).
5. An embedded OccurredAt is flattened into occurred_start, occurred_end, temporal_precision, temporal_confidence columns; `temporal_precision` is the sole shape discriminator under §11.1.
6. Scalar references to other entities become FOREIGN KEY columns. `evidence_items.raw_log_id` references `raw_logs.id ON DELETE CASCADE`; `raw_logs.corrects_log_id` is a self-reference with `ON DELETE SET NULL`; and `gap_questions.answer_log_id` references `raw_logs.id ON DELETE SET NULL`. The required `ResumeBranch.assessment_snapshot_id` derives as `TEXT NOT NULL REFERENCES assessment_snapshots(id)`; its stronger current-anchor check is rule 10. The required `ResumeBranch.job_description_id` derives as `TEXT NOT NULL REFERENCES job_descriptions(id)`; its stronger exact-selection check is rule 10. Job descriptions are retained context, not lifecycle-managed data, and §13.13 owner deletion does not delete them, so this foreign key cannot block owner deletion. Other scalar references use the default restrictive action. No foreign key may block the owner-deletion operation in §13.13.
7. A polymorphic reference — an (`*_type: DetectionRefType`, `*_id`) field pair such as Contradiction.left_ref_* / right_ref_* or GapQuestion.target_* — becomes two plain TEXT NOT NULL columns with no FOREIGN KEY because the target table varies per row; rule 10 supplies its write-time integrity check.
8. Exception: `ExperienceFact.source_log_ids` and `evidence_item_ids` are not stored as columns. They are non-empty, duplicate-free views hydrated from the `fact_sources → evidence_items` relation in §12.4.
9. Queries that feed processing, verification, generation, or export must filter every recomputable table to `superseded_at IS NULL`. Historical inspection is the only normal read path that may include superseded rows.
10. Before a domain-entity batch commits, every typed ID below must resolve to the required target, either in current pre-existing state or among rows inserted earlier in the same transaction. A missing ID, wrong target type, superseded target, or duplicate ID fails the producing operation atomically. The same rule validates `Contradiction.left_ref_*` / `right_ref_*` and `GapQuestion.target_*` against the table selected by their `DetectionRefType`. On `JobDescription` persistence it also rejects an empty `JDRequirement.id` or an ID duplicated within the candidate or any retained `JobDescription`; on Stage 10 persistence it requires the candidate branch's `job_description_id` to equal the exact §14.10 selection and resolves every matched requirement through that persisted job description's `ParsedJD`, not merely against any job description.

| Typed reference fields | Required current target |
|---|---|
| `SelfSignal.supporting_fact_ids`, `counter_fact_ids` | `experience_facts` |
| `SelfClaim.source_signal_ids` | `self_signals` |
| `SelfClaim.source_fact_ids` | `experience_facts` |
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

Stage 6 adds complete-state checks at the same transaction boundary: after candidate inserts and supersession transitions are staged, every current `SelfClaim.id` must occur in exactly one current `AssessmentSnapshot.self_claim_ids`, and every listed claim must be current. Each snapshot must contain exactly one `narrative_summary` claim whose text equals `AssessmentSnapshot.summary`. Its `gap_question_ids` must equal both the complete duplicate-free current unanswered Stage 4 gap set and the IDs in the validated §15.4 `unknowns` output, and a project-scoped snapshot must carry the exact non-blank §14.9 project selector in `scope_target`. Sharing a current claim between snapshots, leaving one unowned, exposing unmatched summary prose, including an answered gap, omitting an unanswered gap, storing a free-form unknown, or changing the scope target fails the batch before commit.

Stage 10 adds one branch-anchor consistency check at that boundary: the candidate `ResumeBranch.assessment_snapshot_id` must equal the exact snapshot selected under §18, its `job_description_id` must equal the exact §14.10 `--jd` selection, every candidate bullet must reference that branch, and every `ResumeBullet.source_self_claim_ids` value must occur in the selected snapshot's `self_claim_ids`. The claim list is duplicate-free under rule 10 and must equal the service-selected claim IDs passed to the writer under §13.10/§15.6. Every `matched_jd_requirements` value must occur exactly once in the `ParsedJD.requirements` reached through that branch job-description ID. A branch that merely names one snapshot while consuming a claim owned by another, loses its selected job description, or names a missing, duplicate, or wrong-job requirement fails before any branch or bullet becomes current.

`processing_runs.input_ids_json` and `output_ids_json` are the explicit exception: they are opaque historical telemetry, not typed domain references, and are not subject to rule 10.

Only two tables have no Pydantic counterpart; both are storage artifacts rather than §9.1 ontology entities, and their DDL is normative here: fact_sources (§12.4) — the relational representation of fact provenance — and processing_runs (§12.13) — pipeline execution telemetry, not entity creation provenance. Retired subsection numbers (§12.1–§12.3, §12.5–§12.12) are never reused; the dated registry lives in the map's § Index.

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

---
