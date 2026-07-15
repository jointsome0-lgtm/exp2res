"""Phase 0 schema derived from §11 RawLog and EvidenceItem."""

from __future__ import annotations

from sqlite3 import Connection


SCHEMA_SQL = """
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


def create_schema(
    connection: Connection, *, version: int, applied_at: str, app_version: str
) -> None:
    connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL)
    connection.execute(
        "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
        (version, applied_at, app_version),
    )
