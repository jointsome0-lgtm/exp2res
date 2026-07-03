## §12. SQLite Schema

The SQLite schema is derived from the Pydantic models in §11; §11 is the normative source for all mirrored entities and fields. Derivation rules:

1. Tables mirror the Pydantic models 1:1: RawLog → raw_logs, EvidenceItem → evidence_items, ExperienceFact → experience_facts, SelfSignal → self_signals, SelfClaim → self_claims, AssessmentSnapshot → assessment_snapshots, ResumeBullet → resume_bullets. Column names match field names; required fields are NOT NULL.
2. list/dict fields are stored as JSON TEXT columns named `<field>_json`, NOT NULL with DEFAULT '[]' / '{}'.
3. datetime fields are stored as ISO 8601 TEXT.
4. An embedded OccurredAt is flattened into occurred_kind, occurred_start, occurred_end, temporal_precision, temporal_confidence columns.
5. References to other entities become FOREIGN KEY columns (e.g. evidence_items.raw_log_id → raw_logs.id, resume_bullets.branch_id → resume_branches.id).
6. Exceptions: ExperienceFact.source_log_ids and evidence_item_ids are not stored as columns — fact provenance lives in the fact_sources join table (§12.4); resume_bullets additionally carries a created_at TEXT NOT NULL column.

The tables below have no Pydantic counterpart; their DDL is normative here. Subsection numbers of the removed derivable tables (§12.1–§12.3, §12.5, §12.6, §12.9, §12.12) are retired, not reused.

## §12.4 fact_sources

```sql
CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id TEXT NOT NULL,
    raw_log_id TEXT NOT NULL,
    evidence_item_id TEXT,
    support_type TEXT NOT NULL,

    PRIMARY KEY (fact_id, raw_log_id, support_type),

    FOREIGN KEY (fact_id) REFERENCES experience_facts(id),
    FOREIGN KEY (raw_log_id) REFERENCES raw_logs(id),
    FOREIGN KEY (evidence_item_id) REFERENCES evidence_items(id)
);
```

## §12.7 contradictions

```sql
CREATE TABLE IF NOT EXISTS contradictions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,

    left_ref_type TEXT NOT NULL,
    left_ref_id TEXT NOT NULL,
    right_ref_type TEXT NOT NULL,
    right_ref_id TEXT NOT NULL,

    status TEXT NOT NULL,
    resolution_note TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.8 gap_questions

```sql
CREATE TABLE IF NOT EXISTS gap_questions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,

    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,

    question TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority TEXT NOT NULL,

    answered INTEGER NOT NULL DEFAULT 0,
    answer_log_id TEXT,

    FOREIGN KEY (answer_log_id) REFERENCES raw_logs(id)
);
```

## §12.10 job_descriptions

```sql
CREATE TABLE IF NOT EXISTS job_descriptions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,

    title TEXT,
    company TEXT,
    raw_text TEXT NOT NULL,
    parsed_json TEXT NOT NULL DEFAULT '{}'
);
```

## §12.11 resume_branches

```sql
CREATE TABLE IF NOT EXISTS resume_branches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    job_description_id TEXT,
    assessment_snapshot_id TEXT,

    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',

    FOREIGN KEY (job_description_id) REFERENCES job_descriptions(id),
    FOREIGN KEY (assessment_snapshot_id) REFERENCES assessment_snapshots(id)
);
```

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

