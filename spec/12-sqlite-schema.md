## §12. SQLite Schema

The SQLite schema is derived from the Pydantic models in §11; §11 is the normative source for all mirrored entities and fields. Derivation rules:

1. Tables mirror the Pydantic models 1:1: RawLog → raw_logs, EvidenceItem → evidence_items, ExperienceFact → experience_facts, SelfSignal → self_signals, SelfClaim → self_claims, Contradiction → contradictions, GapQuestion → gap_questions, AssessmentSnapshot → assessment_snapshots, JobDescription → job_descriptions, ResumeBranch → resume_branches, ResumeBullet → resume_bullets. Column names match field names; required fields are NOT NULL.
2. list/dict fields are stored as JSON TEXT columns named `<field>_json`, NOT NULL with DEFAULT '[]' / '{}'.
3. datetime fields are stored as ISO 8601 TEXT.
4. bool fields are stored as INTEGER 0/1, NOT NULL; a model default becomes the column DEFAULT (GapQuestion.answered → answered INTEGER NOT NULL DEFAULT 0).
5. An embedded OccurredAt is flattened into occurred_start, occurred_end, temporal_precision, temporal_confidence columns; `temporal_precision` is the sole shape discriminator under §11.1.
6. References to other entities become FOREIGN KEY columns (e.g. evidence_items.raw_log_id → raw_logs.id, resume_bullets.branch_id → resume_branches.id, gap_questions.answer_log_id → raw_logs.id).
7. A polymorphic reference — an (`*_type: EntityRefType`, `*_id`) field pair such as Contradiction.left_ref_* / right_ref_* or GapQuestion.target_* — becomes two plain TEXT NOT NULL columns with no FOREIGN KEY: the target table varies per row.
8. Exception: ExperienceFact.source_log_ids and evidence_item_ids are not stored as columns — fact provenance lives in the fact_sources join table (§12.4).

Only two tables have no Pydantic counterpart; both are storage artifacts rather than §9.1 ontology entities, and their DDL is normative here: fact_sources (§12.4) — the relational representation of fact provenance — and processing_runs (§12.13) — pipeline execution telemetry, not entity creation provenance. Retired subsection numbers (§12.1–§12.3, §12.5–§12.12) are never reused; the dated registry lives in the map's § Index.

## §12.4 fact_sources

```sql
CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id TEXT NOT NULL,
    raw_log_id TEXT NOT NULL,
    evidence_item_id TEXT,
    support_type TEXT NOT NULL CHECK (support_type IN ('direct', 'corroborating')),

    PRIMARY KEY (fact_id, raw_log_id, support_type),

    FOREIGN KEY (fact_id) REFERENCES experience_facts(id),
    FOREIGN KEY (raw_log_id) REFERENCES raw_logs(id),
    FOREIGN KEY (evidence_item_id) REFERENCES evidence_items(id)
);
```

`support_type` describes the fact-to-source relationship, not evidence strength. `direct` means the source row is one from which the fact was extracted; `corroborating` means an additional source independently supports the same fact. Every fact must have at least one `direct` source row; a `corroborating` row cannot establish a fact by itself.

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

---
