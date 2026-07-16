"""Phase 0 schema plus the §12.13/§12.15 execution telemetry."""

from __future__ import annotations

from sqlite3 import Connection


SCHEMA_V1_SQL = """
CREATE TABLE schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    app_version TEXT NOT NULL
);

CREATE TABLE raw_logs (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    recorded_at TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN (
        'manual_daily', 'manual_retro', 'gap_answer', 'correction',
        'ephemeris_event', 'atlas_snapshot', 'github_commit', 'design_doc'
    )),
    source_type TEXT NOT NULL CHECK (source_type IN (
        'manual_entry', 'user_memory', 'imported_artifact', 'imported_event'
    )),
    occurred_start TEXT,
    occurred_end TEXT,
    temporal_precision TEXT NOT NULL CHECK (temporal_precision IN (
        'exact_datetime', 'exact_day', 'week', 'month', 'quarter', 'year',
        'date_range', 'approximate_range', 'unknown'
    )),
    temporal_confidence TEXT NOT NULL CHECK (
        temporal_confidence IN ('low', 'medium', 'high', 'unknown')
    ),
    raw_text TEXT NOT NULL CHECK (raw_text <> ''),
    project TEXT,
    external_ref TEXT,
    corrects_log_id TEXT REFERENCES raw_logs(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK (
        (temporal_precision = 'unknown' AND occurred_start IS NULL AND occurred_end IS NULL)
        OR
        (temporal_precision IN ('date_range', 'approximate_range')
            AND occurred_start IS NOT NULL AND occurred_end IS NOT NULL)
        OR
        (temporal_precision IN (
            'exact_datetime', 'exact_day', 'week', 'month', 'quarter', 'year'
        ) AND occurred_start IS NOT NULL AND occurred_end IS NULL)
    )
);

CREATE TABLE evidence_items (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    raw_log_id TEXT NOT NULL REFERENCES raw_logs(id) ON DELETE CASCADE,
    title TEXT,
    summary TEXT NOT NULL,
    uri TEXT,
    path TEXT,
    strength TEXT NOT NULL CHECK (strength IN (
        'manual_claim', 'imported_activity_event', 'knowledge_state_snapshot',
        'artifact_reference', 'commit_or_pr', 'design_doc'
    )),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX evidence_items_raw_log_id_idx ON evidence_items(raw_log_id);
CREATE INDEX raw_logs_recorded_at_idx ON raw_logs(recorded_at);

CREATE TRIGGER raw_logs_automation_update_guard
BEFORE UPDATE ON raw_logs
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'raw_log_immutable');
END;

CREATE TRIGGER raw_logs_automation_delete_guard
BEFORE DELETE ON raw_logs
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'raw_log_owner_delete_required');
END;

CREATE TRIGGER evidence_items_automation_update_guard
BEFORE UPDATE ON evidence_items
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'evidence_item_immutable');
END;

CREATE TRIGGER evidence_items_automation_delete_guard
BEFORE DELETE ON evidence_items
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'evidence_item_owner_delete_required');
END;
"""


# §12.13 and §12.15 are normative DDL. Keep these strings in lockstep with
# the specification instead of adding implementation-only columns or defaults.
PROCESSING_RUNS_SQL = """
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
"""

LLM_CALLS_SQL = """
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
"""

SCHEMA_V2_SQL = (
    SCHEMA_V1_SQL
    + "\n"
    + PROCESSING_RUNS_SQL
    + "\n"
    + LLM_CALLS_SQL
    + "\n"
)


def create_schema(
    connection: Connection, *, version: int, applied_at: str, app_version: str
) -> None:
    if version != 2:
        raise ValueError("fresh workspaces must use schema version 2")
    connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_V2_SQL)
    connection.execute(
        "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
        (version, applied_at, app_version),
    )


def apply_migration_1_to_2(connection: Connection) -> None:
    """Apply only the additive DDL owned by schema migration 1→2."""

    connection.execute(PROCESSING_RUNS_SQL)
    connection.execute(LLM_CALLS_SQL)
