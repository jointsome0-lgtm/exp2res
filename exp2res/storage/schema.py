"""Versioned §12 SQLite DDL and registered schema transformations."""

from __future__ import annotations

from sqlite3 import Connection

from exp2res.domain.models import canonical_project_key


# These historical full-schema strings remain public test fixtures for building
# recognized older workspaces. Do not rewrite them to the current schema.
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

SCHEMA_V2_SQL = SCHEMA_V1_SQL + "\n" + PROCESSING_RUNS_SQL + "\n" + LLM_CALLS_SQL + "\n"


# SQLite stores the renamed table name quoted after the registered 12-step
# rebuild. Fresh v3 uses that same spelling so sqlite_master is shape-identical.
RAW_LOGS_V3_SQL = """
CREATE TABLE "raw_logs" (
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
    project_key TEXT,
    external_ref TEXT,
    corrects_log_id TEXT REFERENCES raw_logs(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK ((project IS NULL) = (project_key IS NULL)),
    CHECK (project_key IS NULL OR project_key <> ''),
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
"""

RAW_LOGS_V3_NEW_SQL = RAW_LOGS_V3_SQL.replace(
    'CREATE TABLE "raw_logs"', "CREATE TABLE raw_logs_new", 1
)

EVIDENCE_ITEMS_SQL = """
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
"""

RAW_LOGS_INDEX_SQL = "CREATE INDEX raw_logs_recorded_at_idx ON raw_logs(recorded_at);"
EVIDENCE_ITEMS_INDEX_SQL = (
    "CREATE INDEX evidence_items_raw_log_id_idx ON evidence_items(raw_log_id);"
)

RAW_LOGS_UPDATE_GUARD_SQL = """
CREATE TRIGGER raw_logs_automation_update_guard
BEFORE UPDATE ON raw_logs
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'raw_log_immutable');
END;
"""

RAW_LOGS_DELETE_GUARD_SQL = """
CREATE TRIGGER raw_logs_automation_delete_guard
BEFORE DELETE ON raw_logs
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'raw_log_owner_delete_required');
END;
"""

EVIDENCE_ITEMS_UPDATE_GUARD_SQL = """
CREATE TRIGGER evidence_items_automation_update_guard
BEFORE UPDATE ON evidence_items
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'evidence_item_immutable');
END;
"""

EVIDENCE_ITEMS_DELETE_GUARD_SQL = """
CREATE TRIGGER evidence_items_automation_delete_guard
BEFORE DELETE ON evidence_items
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'evidence_item_owner_delete_required');
END;
"""

EXPERIENCE_FACTS_SQL = """
CREATE TABLE experience_facts (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    claim TEXT NOT NULL CHECK (claim <> ''),
    -- §15.2: only these two ClaimKind members are valid fact-extractor
    -- outputs; the remaining §10 members belong to SelfClaim producers.
    claim_kind TEXT NOT NULL CHECK (claim_kind IN ('observed_fact', 'inferred_fact')),
    project TEXT,
    project_key TEXT,
    role TEXT,
    company TEXT,
    context TEXT NOT NULL CHECK (context IN (
        'employment', 'contract', 'freelance', 'independent_project',
        'open_source', 'competition', 'research', 'learning',
        'personal_system', 'unknown'
    )),
    ownership_level TEXT NOT NULL CHECK (ownership_level IN (
        'unknown', 'observed', 'studied', 'participated', 'experimented',
        'contributed', 'implemented', 'built', 'designed', 'owned', 'led'
    )),
    action TEXT,
    object TEXT,
    outcome TEXT,
    skills_json TEXT NOT NULL DEFAULT '[]',
    technologies_json TEXT NOT NULL DEFAULT '[]',
    themes_json TEXT NOT NULL DEFAULT '[]',
    occurred_start TEXT,
    occurred_end TEXT,
    temporal_precision TEXT NOT NULL CHECK (temporal_precision IN (
        'exact_datetime', 'exact_day', 'week', 'month', 'quarter', 'year',
        'date_range', 'approximate_range', 'unknown'
    )),
    temporal_confidence TEXT NOT NULL CHECK (
        temporal_confidence IN ('low', 'medium', 'high', 'unknown')
    ),
    confidence TEXT NOT NULL CHECK (
        confidence IN ('low', 'medium', 'high', 'unknown')
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    generation_id TEXT NOT NULL CHECK (generation_id <> ''),
    CHECK ((project IS NULL) = (project_key IS NULL)),
    CHECK (project_key IS NULL OR project_key <> ''),
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
"""

# §12.4 normative DDL, including the required IF NOT EXISTS spelling.
FACT_SOURCES_SQL = """
CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id TEXT NOT NULL,
    evidence_item_id TEXT NOT NULL,
    support_type TEXT NOT NULL CHECK (support_type IN ('direct', 'corroborating')),

    PRIMARY KEY (fact_id, evidence_item_id),

    FOREIGN KEY (fact_id) REFERENCES experience_facts(id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_item_id) REFERENCES evidence_items(id) ON DELETE CASCADE
);
"""

FACT_SOURCES_INDEX_SQL = (
    "CREATE INDEX fact_sources_evidence_item_id_idx ON fact_sources(evidence_item_id);"
)

EXPERIENCE_FACTS_UPDATE_GUARD_SQL = """
CREATE TRIGGER experience_facts_lifecycle_update_guard
BEFORE UPDATE ON experience_facts
WHEN NOT (
    OLD.superseded_at IS NULL
    AND NEW.superseded_at IS NOT NULL
    AND NEW.id IS OLD.id
    AND NEW.created_at IS OLD.created_at
    AND NEW.claim IS OLD.claim
    AND NEW.claim_kind IS OLD.claim_kind
    AND NEW.project IS OLD.project
    AND NEW.project_key IS OLD.project_key
    AND NEW.role IS OLD.role
    AND NEW.company IS OLD.company
    AND NEW.context IS OLD.context
    AND NEW.ownership_level IS OLD.ownership_level
    AND NEW.action IS OLD.action
    AND NEW.object IS OLD.object
    AND NEW.outcome IS OLD.outcome
    AND NEW.skills_json IS OLD.skills_json
    AND NEW.technologies_json IS OLD.technologies_json
    AND NEW.themes_json IS OLD.themes_json
    AND NEW.occurred_start IS OLD.occurred_start
    AND NEW.occurred_end IS OLD.occurred_end
    AND NEW.temporal_precision IS OLD.temporal_precision
    AND NEW.temporal_confidence IS OLD.temporal_confidence
    AND NEW.confidence IS OLD.confidence
    AND NEW.metadata_json IS OLD.metadata_json
    AND NEW.produced_by_run_id IS OLD.produced_by_run_id
    AND NEW.generation_id IS OLD.generation_id
)
BEGIN
    SELECT RAISE(ABORT, 'experience_fact_lifecycle_only');
END;
"""

EXPERIENCE_FACTS_DELETE_GUARD_SQL = """
CREATE TRIGGER experience_facts_owner_delete_guard
BEFORE DELETE ON experience_facts
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'experience_fact_owner_purge_required');
END;
"""

FACT_SOURCES_UPDATE_GUARD_SQL = """
CREATE TRIGGER fact_sources_update_guard
BEFORE UPDATE ON fact_sources
BEGIN
    SELECT RAISE(ABORT, 'fact_source_immutable');
END;
"""

FACT_SOURCES_DELETE_GUARD_SQL = """
CREATE TRIGGER fact_sources_owner_delete_guard
BEFORE DELETE ON fact_sources
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'fact_source_owner_purge_required');
END;
"""

GAP_QUESTIONS_SQL = """
CREATE TABLE gap_questions (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    target_type TEXT NOT NULL CHECK (target_type IN (
        'raw_log', 'evidence_item', 'experience_fact'
    )),
    target_id TEXT NOT NULL CHECK (target_id <> ''),
    question TEXT NOT NULL CHECK (question <> ''),
    reason TEXT NOT NULL CHECK (reason IN (
        'missing_metric', 'missing_scale', 'missing_ownership',
        'missing_context', 'ambiguous_time', 'ambiguous_claim',
        'weak_evidence', 'unsupported_skill_claim',
        'unclear_artifact_status'
    )),
    priority TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high')),
    answered INTEGER NOT NULL DEFAULT 0 CHECK (answered IN (0, 1)),
    answer_log_id TEXT REFERENCES raw_logs(id) ON DELETE SET NULL,
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    generation_id TEXT NOT NULL CHECK (generation_id <> ''),
    CHECK (
        (answered = 0 AND answer_log_id IS NULL)
        OR (answered = 1 AND answer_log_id IS NOT NULL)
    )
);
"""

CONTRADICTIONS_SQL = """
CREATE TABLE contradictions (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    title TEXT NOT NULL CHECK (title <> ''),
    description TEXT NOT NULL CHECK (description <> ''),
    left_ref_type TEXT NOT NULL CHECK (left_ref_type IN (
        'raw_log', 'evidence_item', 'experience_fact'
    )),
    left_ref_id TEXT NOT NULL CHECK (left_ref_id <> ''),
    right_ref_type TEXT NOT NULL CHECK (right_ref_type IN (
        'raw_log', 'evidence_item', 'experience_fact'
    )),
    right_ref_id TEXT NOT NULL CHECK (right_ref_id <> ''),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    generation_id TEXT NOT NULL CHECK (generation_id <> '')
);
"""

GAP_QUESTIONS_UPDATE_GUARD_SQL = """
CREATE TRIGGER gap_questions_lifecycle_update_guard
BEFORE UPDATE ON gap_questions
WHEN exp2res_owner_delete() <> 1 AND NOT (
    OLD.superseded_at IS NULL
    AND (
        (
            NEW.superseded_at IS NOT NULL
            AND NEW.answered IS OLD.answered
            AND NEW.answer_log_id IS OLD.answer_log_id
        )
        OR
        (
            NEW.superseded_at IS OLD.superseded_at
            AND OLD.answered = 0
            AND NEW.answered = 1
            AND OLD.answer_log_id IS NULL
            AND NEW.answer_log_id IS NOT NULL
        )
    )
    AND NEW.id IS OLD.id
    AND NEW.created_at IS OLD.created_at
    AND NEW.target_type IS OLD.target_type
    AND NEW.target_id IS OLD.target_id
    AND NEW.question IS OLD.question
    AND NEW.reason IS OLD.reason
    AND NEW.priority IS OLD.priority
    AND NEW.produced_by_run_id IS OLD.produced_by_run_id
    AND NEW.generation_id IS OLD.generation_id
)
BEGIN
    SELECT RAISE(ABORT, 'gap_question_lifecycle_only');
END;
"""

GAP_QUESTIONS_DELETE_GUARD_SQL = """
CREATE TRIGGER gap_questions_owner_delete_guard
BEFORE DELETE ON gap_questions
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'gap_question_owner_purge_required');
END;
"""

CONTRADICTIONS_UPDATE_GUARD_SQL = """
CREATE TRIGGER contradictions_lifecycle_update_guard
BEFORE UPDATE ON contradictions
WHEN exp2res_owner_delete() <> 1 AND NOT (
    OLD.superseded_at IS NULL
    AND NEW.superseded_at IS NOT NULL
    AND NEW.id IS OLD.id
    AND NEW.created_at IS OLD.created_at
    AND NEW.title IS OLD.title
    AND NEW.description IS OLD.description
    AND NEW.left_ref_type IS OLD.left_ref_type
    AND NEW.left_ref_id IS OLD.left_ref_id
    AND NEW.right_ref_type IS OLD.right_ref_type
    AND NEW.right_ref_id IS OLD.right_ref_id
    AND NEW.metadata_json IS OLD.metadata_json
    AND NEW.produced_by_run_id IS OLD.produced_by_run_id
    AND NEW.generation_id IS OLD.generation_id
)
BEGIN
    SELECT RAISE(ABORT, 'contradiction_lifecycle_only');
END;
"""

CONTRADICTIONS_DELETE_GUARD_SQL = """
CREATE TRIGGER contradictions_owner_delete_guard
BEFORE DELETE ON contradictions
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'contradiction_owner_purge_required');
END;
"""

SELF_SIGNALS_SQL = """
CREATE TABLE self_signals (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    signal_type TEXT NOT NULL CHECK (signal_type IN (
        'skill_signal', 'interest_signal', 'direction_signal',
        'execution_pattern', 'avoidance_pattern', 'constraint_signal',
        'capacity_signal', 'contradiction_signal'
    )),
    statement TEXT NOT NULL CHECK (statement <> ''),
    supporting_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    counter_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL CHECK (
        confidence IN ('low', 'medium', 'high', 'unknown')
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    generation_id TEXT NOT NULL CHECK (generation_id <> '')
);
"""

SELF_SIGNALS_UPDATE_GUARD_SQL = """
CREATE TRIGGER self_signals_lifecycle_update_guard
BEFORE UPDATE ON self_signals
WHEN exp2res_owner_delete() <> 1 AND NOT (
    OLD.superseded_at IS NULL
    AND NEW.superseded_at IS NOT NULL
    AND NEW.id IS OLD.id
    AND NEW.created_at IS OLD.created_at
    AND NEW.signal_type IS OLD.signal_type
    AND NEW.statement IS OLD.statement
    AND NEW.supporting_fact_ids_json IS OLD.supporting_fact_ids_json
    AND NEW.counter_fact_ids_json IS OLD.counter_fact_ids_json
    AND NEW.confidence IS OLD.confidence
    AND NEW.metadata_json IS OLD.metadata_json
    AND NEW.produced_by_run_id IS OLD.produced_by_run_id
    AND NEW.generation_id IS OLD.generation_id
)
BEGIN
    SELECT RAISE(ABORT, 'self_signal_lifecycle_only');
END;
"""

SELF_SIGNALS_DELETE_GUARD_SQL = """
CREATE TRIGGER self_signals_owner_delete_guard
BEFORE DELETE ON self_signals
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'self_signal_owner_purge_required');
END;
"""

ASSESSMENT_SNAPSHOTS_SQL = """
CREATE TABLE assessment_snapshots (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    scope TEXT NOT NULL CHECK (scope IN ('global', 'project')),
    scope_target TEXT,
    title TEXT NOT NULL CHECK (title <> ''),
    summary TEXT NOT NULL CHECK (summary <> ''),
    gap_question_ids_json TEXT NOT NULL DEFAULT '[]',
    contradiction_ids_json TEXT NOT NULL DEFAULT '[]',
    verification_status TEXT NOT NULL CHECK (verification_status IN (
        'unverified', 'supported', 'partially_supported',
        'inferred_but_acceptable', 'needs_clarification', 'contradicted',
        'unsupported', 'rejected'
    )),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    generation_id TEXT NOT NULL CHECK (generation_id <> ''),
    CHECK ((scope = 'global') = (scope_target IS NULL)),
    CHECK (scope_target IS NULL OR scope_target <> '')
);
"""

SELF_CLAIMS_SQL = """
CREATE TABLE self_claims (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    snapshot_id TEXT NOT NULL REFERENCES assessment_snapshots(id),
    claim TEXT NOT NULL CHECK (claim <> ''),
    claim_kind TEXT NOT NULL CHECK (claim_kind IN (
        'pattern_signal', 'hypothesis', 'narrative_summary'
    )),
    dimension TEXT NOT NULL CHECK (dimension IN (
        'technical_skill', 'domain_interest', 'working_style',
        'execution_capacity', 'constraint', 'risk', 'gap', 'trajectory',
        'identity_hypothesis'
    )),
    source_signal_ids_json TEXT NOT NULL DEFAULT '[]',
    source_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL CHECK (
        confidence IN ('low', 'medium', 'high', 'unknown')
    ),
    verification_status TEXT NOT NULL CHECK (verification_status IN (
        'unverified', 'supported', 'partially_supported',
        'inferred_but_acceptable', 'needs_clarification', 'contradicted',
        'unsupported', 'rejected'
    )),
    counterevidence_json TEXT NOT NULL DEFAULT '[]',
    uncertainty TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    generation_id TEXT NOT NULL CHECK (generation_id <> '')
);
"""

SELF_CLAIMS_SNAPSHOT_INDEX_SQL = (
    "CREATE INDEX self_claims_snapshot_id_idx ON self_claims(snapshot_id);"
)

SELF_CLAIMS_UPDATE_GUARD_SQL = """
CREATE TRIGGER self_claims_lifecycle_update_guard
BEFORE UPDATE ON self_claims
WHEN exp2res_owner_delete() <> 1 AND NOT (
    OLD.superseded_at IS NULL
    AND (
        (
            NEW.superseded_at IS NOT NULL
            AND NEW.verification_status IS OLD.verification_status
            AND NEW.counterevidence_json IS OLD.counterevidence_json
        )
        OR
        (
            NEW.superseded_at IS OLD.superseded_at
        )
    )
    AND NEW.id IS OLD.id
    AND NEW.created_at IS OLD.created_at
    AND NEW.snapshot_id IS OLD.snapshot_id
    AND NEW.claim IS OLD.claim
    AND NEW.claim_kind IS OLD.claim_kind
    AND NEW.dimension IS OLD.dimension
    AND NEW.source_signal_ids_json IS OLD.source_signal_ids_json
    AND NEW.source_fact_ids_json IS OLD.source_fact_ids_json
    AND NEW.confidence IS OLD.confidence
    AND NEW.uncertainty IS OLD.uncertainty
    AND NEW.metadata_json IS OLD.metadata_json
    AND NEW.produced_by_run_id IS OLD.produced_by_run_id
    AND NEW.generation_id IS OLD.generation_id
)
BEGIN
    SELECT RAISE(ABORT, 'self_claim_lifecycle_only');
END;
"""

ASSESSMENT_SNAPSHOTS_UPDATE_GUARD_SQL = """
CREATE TRIGGER assessment_snapshots_lifecycle_update_guard
BEFORE UPDATE ON assessment_snapshots
WHEN exp2res_owner_delete() <> 1 AND NOT (
    OLD.superseded_at IS NULL
    AND (
        (
            NEW.superseded_at IS NOT NULL
            AND NEW.verification_status IS OLD.verification_status
        )
        OR
        (
            NEW.superseded_at IS OLD.superseded_at
        )
    )
    AND NEW.id IS OLD.id
    AND NEW.created_at IS OLD.created_at
    AND NEW.scope IS OLD.scope
    AND NEW.scope_target IS OLD.scope_target
    AND NEW.title IS OLD.title
    AND NEW.summary IS OLD.summary
    AND NEW.gap_question_ids_json IS OLD.gap_question_ids_json
    AND NEW.contradiction_ids_json IS OLD.contradiction_ids_json
    AND NEW.metadata_json IS OLD.metadata_json
    AND NEW.produced_by_run_id IS OLD.produced_by_run_id
    AND NEW.generation_id IS OLD.generation_id
)
BEGIN
    SELECT RAISE(ABORT, 'assessment_snapshot_lifecycle_only');
END;
"""

SELF_CLAIMS_DELETE_GUARD_SQL = """
CREATE TRIGGER self_claims_owner_delete_guard
BEFORE DELETE ON self_claims
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'self_claim_owner_purge_required');
END;
"""

ASSESSMENT_SNAPSHOTS_DELETE_GUARD_SQL = """
CREATE TRIGGER assessment_snapshots_owner_delete_guard
BEFORE DELETE ON assessment_snapshots
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'assessment_snapshot_owner_purge_required');
END;
"""

VERIFICATION_FINDINGS_SQL = """
CREATE TABLE verification_findings (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    produced_by_run_id TEXT NOT NULL REFERENCES processing_runs(id),
    target_type TEXT NOT NULL CHECK (target_type IN (
        'self_claim', 'resume_bullet'
    )),
    target_id TEXT NOT NULL CHECK (target_id <> ''),
    status TEXT NOT NULL CHECK (status IN (
        'supported', 'partially_supported', 'inferred_but_acceptable',
        'needs_clarification', 'contradicted', 'unsupported', 'rejected'
    )),
    reason TEXT NOT NULL CHECK (reason <> ''),
    unsupported_phrases_json TEXT NOT NULL DEFAULT '[]',
    suggested_rewrite TEXT,
    counterevidence_json TEXT NOT NULL DEFAULT '[]'
);
"""

VERIFICATION_FINDINGS_TARGET_INDEX_SQL = (
    "CREATE INDEX verification_findings_target_id_idx "
    "ON verification_findings(target_id);"
)

VERIFICATION_FINDINGS_UPDATE_GUARD_SQL = """
CREATE TRIGGER verification_findings_update_guard
BEFORE UPDATE ON verification_findings
BEGIN
    SELECT RAISE(ABORT, 'verification_finding_immutable');
END;
"""

VERIFICATION_FINDINGS_DELETE_GUARD_SQL = """
CREATE TRIGGER verification_findings_owner_delete_guard
BEFORE DELETE ON verification_findings
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'verification_finding_owner_purge_required');
END;
"""

JOB_DESCRIPTIONS_SQL = """
CREATE TABLE job_descriptions (
    id TEXT NOT NULL PRIMARY KEY CHECK (id <> ''),
    created_at TEXT NOT NULL,
    title TEXT,
    company TEXT,
    raw_text TEXT NOT NULL CHECK (raw_text <> ''),
    parsed_json TEXT NOT NULL DEFAULT '{}'
);
"""

# §11.11: a job description is retained context, not a recomputable
# interpretation — it carries no `superseded_at` and no lifecycle transition,
# so every update is a violation the way a `verification_findings` update is.
JOB_DESCRIPTIONS_UPDATE_GUARD_SQL = """
CREATE TRIGGER job_descriptions_update_guard
BEFORE UPDATE ON job_descriptions
BEGIN
    SELECT RAISE(ABORT, 'job_description_immutable');
END;
"""

JOB_DESCRIPTIONS_DELETE_GUARD_SQL = """
CREATE TRIGGER job_descriptions_owner_delete_guard
BEFORE DELETE ON job_descriptions
WHEN exp2res_owner_delete() <> 1
BEGIN
    SELECT RAISE(ABORT, 'job_description_owner_purge_required');
END;
"""

SCHEMA_META_SQL = """
CREATE TABLE schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    app_version TEXT NOT NULL
);
"""

SCHEMA_V3_SQL = "\n".join(
    (
        SCHEMA_META_SQL,
        RAW_LOGS_V3_SQL,
        EVIDENCE_ITEMS_SQL,
        EVIDENCE_ITEMS_INDEX_SQL,
        RAW_LOGS_INDEX_SQL,
        RAW_LOGS_UPDATE_GUARD_SQL,
        RAW_LOGS_DELETE_GUARD_SQL,
        EVIDENCE_ITEMS_UPDATE_GUARD_SQL,
        EVIDENCE_ITEMS_DELETE_GUARD_SQL,
        PROCESSING_RUNS_SQL,
        LLM_CALLS_SQL,
        EXPERIENCE_FACTS_SQL,
        FACT_SOURCES_SQL,
        FACT_SOURCES_INDEX_SQL,
        EXPERIENCE_FACTS_UPDATE_GUARD_SQL,
        EXPERIENCE_FACTS_DELETE_GUARD_SQL,
        FACT_SOURCES_UPDATE_GUARD_SQL,
        FACT_SOURCES_DELETE_GUARD_SQL,
    )
)

SCHEMA_V4_SQL = "\n".join(
    (
        SCHEMA_V3_SQL,
        GAP_QUESTIONS_SQL,
        CONTRADICTIONS_SQL,
        GAP_QUESTIONS_UPDATE_GUARD_SQL,
        GAP_QUESTIONS_DELETE_GUARD_SQL,
        CONTRADICTIONS_UPDATE_GUARD_SQL,
        CONTRADICTIONS_DELETE_GUARD_SQL,
    )
)

SCHEMA_V5_SQL = "\n".join(
    (
        SCHEMA_V4_SQL,
        SELF_SIGNALS_SQL,
        SELF_SIGNALS_UPDATE_GUARD_SQL,
        SELF_SIGNALS_DELETE_GUARD_SQL,
    )
)

SCHEMA_V6_SQL = "\n".join(
    (
        SCHEMA_V5_SQL,
        ASSESSMENT_SNAPSHOTS_SQL,
        SELF_CLAIMS_SQL,
        SELF_CLAIMS_SNAPSHOT_INDEX_SQL,
        SELF_CLAIMS_UPDATE_GUARD_SQL,
        ASSESSMENT_SNAPSHOTS_UPDATE_GUARD_SQL,
        SELF_CLAIMS_DELETE_GUARD_SQL,
        ASSESSMENT_SNAPSHOTS_DELETE_GUARD_SQL,
    )
)

SCHEMA_V7_SQL = "\n".join(
    (
        SCHEMA_V6_SQL,
        VERIFICATION_FINDINGS_SQL,
        VERIFICATION_FINDINGS_TARGET_INDEX_SQL,
        VERIFICATION_FINDINGS_UPDATE_GUARD_SQL,
        VERIFICATION_FINDINGS_DELETE_GUARD_SQL,
    )
)

# §11.1/§12 rule 5: schema v8 changes only the two flattened OccurredAt
# range-shape checks. Historical schema strings remain closed-range fixtures.
RAW_LOGS_V8_SQL = RAW_LOGS_V3_SQL.replace(
    "AND occurred_start IS NOT NULL AND occurred_end IS NOT NULL",
    "AND occurred_start IS NOT NULL",
    1,
)
EXPERIENCE_FACTS_V8_SQL = EXPERIENCE_FACTS_SQL.replace(
    "AND occurred_start IS NOT NULL AND occurred_end IS NOT NULL",
    "AND occurred_start IS NOT NULL",
    1,
).replace(
    "CREATE TABLE experience_facts",
    'CREATE TABLE "experience_facts"',
    1,
)
RAW_LOGS_V8_NEW_SQL = RAW_LOGS_V8_SQL.replace(
    'CREATE TABLE "raw_logs"', "CREATE TABLE raw_logs_new", 1
)
EXPERIENCE_FACTS_V8_NEW_SQL = EXPERIENCE_FACTS_V8_SQL.replace(
    'CREATE TABLE "experience_facts"', "CREATE TABLE experience_facts_new", 1
)
SCHEMA_V8_SQL = SCHEMA_V7_SQL.replace(
    RAW_LOGS_V3_SQL, RAW_LOGS_V8_SQL, 1
).replace(
    EXPERIENCE_FACTS_SQL, EXPERIENCE_FACTS_V8_SQL, 1
)

# §11.6/§13.5-removal (issue #76): schema v9 retires the whole `self_signals`
# table and replaces the claim's signal citation with the service-derived
# contrary-role marking §15.4's discarded patterns leave behind.
SELF_CLAIMS_V9_SQL = SELF_CLAIMS_SQL.replace(
    "    source_signal_ids_json TEXT NOT NULL DEFAULT '[]',\n"
    "    source_fact_ids_json TEXT NOT NULL DEFAULT '[]',",
    "    source_fact_ids_json TEXT NOT NULL DEFAULT '[]',\n"
    "    counter_fact_ids_json TEXT NOT NULL DEFAULT '[]',",
    1,
)

SELF_CLAIMS_V9_UPDATE_GUARD_SQL = SELF_CLAIMS_UPDATE_GUARD_SQL.replace(
    "    AND NEW.source_signal_ids_json IS OLD.source_signal_ids_json\n"
    "    AND NEW.source_fact_ids_json IS OLD.source_fact_ids_json",
    "    AND NEW.source_fact_ids_json IS OLD.source_fact_ids_json\n"
    "    AND NEW.counter_fact_ids_json IS OLD.counter_fact_ids_json",
    1,
)

SCHEMA_V9_SQL = (
    SCHEMA_V8_SQL.replace(SELF_SIGNALS_SQL + "\n", "", 1)
    .replace(SELF_SIGNALS_UPDATE_GUARD_SQL + "\n", "", 1)
    .replace(SELF_SIGNALS_DELETE_GUARD_SQL + "\n", "", 1)
    .replace(SELF_CLAIMS_SQL, SELF_CLAIMS_V9_SQL, 1)
    .replace(SELF_CLAIMS_UPDATE_GUARD_SQL, SELF_CLAIMS_V9_UPDATE_GUARD_SQL, 1)
)

# §10/§11.7 (A4): schema v10 leaves `AssessmentScope` one member, so the
# snapshot keeps `scope` as its single-valued view identity and loses
# `scope_target` and the two shape checks that paired the two columns.
# The 9→10 rebuild renames `assessment_snapshots_new` into place, and SQLite
# stores the renamed table's DDL with the name quoted. The fresh schema carries
# the same quoting so a migrated workspace and a fresh one keep one shape.
ASSESSMENT_SNAPSHOTS_V10_SQL = (
    ASSESSMENT_SNAPSHOTS_SQL.replace(
        "CREATE TABLE assessment_snapshots", 'CREATE TABLE "assessment_snapshots"', 1
    ).replace(
        "    scope TEXT NOT NULL CHECK (scope IN ('global', 'project')),\n"
        "    scope_target TEXT,\n",
        "    scope TEXT NOT NULL CHECK (scope = 'global'),\n",
        1,
    )
    .replace(
        ",\n    CHECK ((scope = 'global') = (scope_target IS NULL)),\n"
        "    CHECK (scope_target IS NULL OR scope_target <> '')\n",
        "\n",
        1,
    )
)

ASSESSMENT_SNAPSHOTS_V10_NEW_SQL = ASSESSMENT_SNAPSHOTS_V10_SQL.replace(
    'CREATE TABLE "assessment_snapshots"', "CREATE TABLE assessment_snapshots_new", 1
)

ASSESSMENT_SNAPSHOTS_V10_UPDATE_GUARD_SQL = (
    ASSESSMENT_SNAPSHOTS_UPDATE_GUARD_SQL.replace(
        "    AND NEW.scope_target IS OLD.scope_target\n", "", 1
    )
)

SCHEMA_V10_SQL = SCHEMA_V9_SQL.replace(
    ASSESSMENT_SNAPSHOTS_SQL, ASSESSMENT_SNAPSHOTS_V10_SQL, 1
).replace(
    ASSESSMENT_SNAPSHOTS_UPDATE_GUARD_SQL,
    ASSESSMENT_SNAPSHOTS_V10_UPDATE_GUARD_SQL,
    1,
)


# §22 Phase 4: schema v11 adds the §13.8 job-description layer. The table is
# created fresh, so no rebuild and no pre-quoted table name is involved.
SCHEMA_V11_SQL = "\n".join(
    (
        SCHEMA_V10_SQL,
        JOB_DESCRIPTIONS_SQL,
        JOB_DESCRIPTIONS_UPDATE_GUARD_SQL,
        JOB_DESCRIPTIONS_DELETE_GUARD_SQL,
    )
)

# §14.16 owns one complete, referentially ordered whole-workspace purge.
# Keeping the inventory beside the current schema makes a newly added table a
# deliberate compile-time/test-time lifecycle decision instead of a silent
# omission in service code.
PURGE_TABLE_ORDER = (
    "verification_findings",
    "self_claims",
    "assessment_snapshots",
    "gap_questions",
    "contradictions",
    "job_descriptions",
    "fact_sources",
    "experience_facts",
    "evidence_items",
    "raw_logs",
    "llm_calls",
    "processing_runs",
)

# Tables with one entity `id` contribute typed §14.14 affected-ID groups.
# `fact_sources` is a link table and `llm_calls` is identified by its run.
PURGE_ENTITY_TABLES = (
    ("raw_logs", "raw_log"),
    ("evidence_items", "evidence_item"),
    ("experience_facts", "experience_fact"),
    ("gap_questions", "gap_question"),
    ("contradictions", "contradiction"),
    ("assessment_snapshots", "assessment_snapshot"),
    ("self_claims", "self_claim"),
    ("verification_findings", "verification_finding"),
    ("job_descriptions", "job_description"),
    ("processing_runs", "processing_run"),
)


def create_schema(
    connection: Connection, *, version: int, applied_at: str, app_version: str
) -> None:
    if version != 11:
        raise ValueError("fresh workspaces must use schema version 11")
    connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_V11_SQL)
    connection.execute(
        "INSERT INTO schema_meta(version, applied_at, app_version) VALUES (?, ?, ?)",
        (version, applied_at, app_version),
    )


def apply_migration_1_to_2(connection: Connection) -> None:
    """Apply only the additive DDL owned by schema migration 1→2."""

    connection.execute(PROCESSING_RUNS_SQL)
    connection.execute(LLM_CALLS_SQL)


def apply_migration_2_to_3(connection: Connection) -> None:
    """Create fact storage and rebuild raw_logs with canonical project keys."""

    for statement in (
        EXPERIENCE_FACTS_SQL,
        FACT_SOURCES_SQL,
        FACT_SOURCES_INDEX_SQL,
        EXPERIENCE_FACTS_UPDATE_GUARD_SQL,
        EXPERIENCE_FACTS_DELETE_GUARD_SQL,
        FACT_SOURCES_UPDATE_GUARD_SQL,
        FACT_SOURCES_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)

    old_rows = connection.execute(
        """
        SELECT id, recorded_at, entry_type, source_type, occurred_start,
               occurred_end, temporal_precision, temporal_confidence, raw_text,
               project, external_ref, corrects_log_id, metadata_json
        FROM raw_logs
        """
    ).fetchall()
    transformed: list[tuple[object, ...]] = []
    for row in old_rows:
        project = row[9]
        project_key = None
        if project is not None:
            if not isinstance(project, str):
                raise ValueError("raw_log_project_label_type")
            project_key = canonical_project_key(project)
            if not project_key:
                raise ValueError("raw_log_project_label_blank")
        transformed.append((*row[:10], project_key, *row[10:]))

    connection.execute(RAW_LOGS_V3_NEW_SQL)
    connection.executemany(
        """
        INSERT INTO raw_logs_new(
            id, recorded_at, entry_type, source_type, occurred_start,
            occurred_end, temporal_precision, temporal_confidence, raw_text,
            project, project_key, external_ref, corrects_log_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        transformed,
    )
    connection.execute("DROP TABLE raw_logs")
    connection.execute("ALTER TABLE raw_logs_new RENAME TO raw_logs")
    for statement in (
        RAW_LOGS_INDEX_SQL,
        RAW_LOGS_UPDATE_GUARD_SQL,
        RAW_LOGS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_3_to_4(connection: Connection) -> None:
    """Add the Stage 4 detection substrate without rewriting retained rows."""

    for statement in (
        GAP_QUESTIONS_SQL,
        CONTRADICTIONS_SQL,
        GAP_QUESTIONS_UPDATE_GUARD_SQL,
        GAP_QUESTIONS_DELETE_GUARD_SQL,
        CONTRADICTIONS_UPDATE_GUARD_SQL,
        CONTRADICTIONS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_4_to_5(connection: Connection) -> None:
    """Add the Stage 5 self-signal substrate without rewriting retained rows."""

    for statement in (
        SELF_SIGNALS_SQL,
        SELF_SIGNALS_UPDATE_GUARD_SQL,
        SELF_SIGNALS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_5_to_6(connection: Connection) -> None:
    """Add the Stage 6 assessment substrate without rewriting retained rows."""

    for statement in (
        ASSESSMENT_SNAPSHOTS_SQL,
        SELF_CLAIMS_SQL,
        SELF_CLAIMS_SNAPSHOT_INDEX_SQL,
        SELF_CLAIMS_UPDATE_GUARD_SQL,
        ASSESSMENT_SNAPSHOTS_UPDATE_GUARD_SQL,
        SELF_CLAIMS_DELETE_GUARD_SQL,
        ASSESSMENT_SNAPSHOTS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_6_to_7(connection: Connection) -> None:
    """Add the Stage 7 verification-finding substrate without rewriting rows."""

    for statement in (
        VERIFICATION_FINDINGS_SQL,
        VERIFICATION_FINDINGS_TARGET_INDEX_SQL,
        VERIFICATION_FINDINGS_UPDATE_GUARD_SQL,
        VERIFICATION_FINDINGS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_7_to_8(connection: Connection) -> None:
    """Rebuild OccurredAt-bearing tables for §11.1 open-ended ranges."""

    connection.execute(RAW_LOGS_V8_NEW_SQL)
    connection.execute(
        """
        INSERT INTO raw_logs_new(
            id, recorded_at, entry_type, source_type, occurred_start,
            occurred_end, temporal_precision, temporal_confidence, raw_text,
            project, project_key, external_ref, corrects_log_id, metadata_json
        )
        SELECT id, recorded_at, entry_type, source_type, occurred_start,
               occurred_end, temporal_precision, temporal_confidence, raw_text,
               project, project_key, external_ref, corrects_log_id, metadata_json
        FROM raw_logs
        """
    )
    connection.execute("DROP TABLE raw_logs")
    connection.execute("ALTER TABLE raw_logs_new RENAME TO raw_logs")
    for statement in (
        RAW_LOGS_INDEX_SQL,
        RAW_LOGS_UPDATE_GUARD_SQL,
        RAW_LOGS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)

    connection.execute(EXPERIENCE_FACTS_V8_NEW_SQL)
    connection.execute(
        """
        INSERT INTO experience_facts_new(
            id, created_at, superseded_at, claim, claim_kind, project,
            project_key, role, company, context, ownership_level, action,
            object, outcome, skills_json, technologies_json, themes_json,
            occurred_start, occurred_end, temporal_precision,
            temporal_confidence, confidence, metadata_json,
            produced_by_run_id, generation_id
        )
        SELECT id, created_at, superseded_at, claim, claim_kind, project,
               project_key, role, company, context, ownership_level, action,
               object, outcome, skills_json, technologies_json, themes_json,
               occurred_start, occurred_end, temporal_precision,
               temporal_confidence, confidence, metadata_json,
               produced_by_run_id, generation_id
        FROM experience_facts
        """
    )
    connection.execute("DROP TABLE experience_facts")
    connection.execute(
        "ALTER TABLE experience_facts_new RENAME TO experience_facts"
    )
    for statement in (
        EXPERIENCE_FACTS_UPDATE_GUARD_SQL,
        EXPERIENCE_FACTS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_8_to_9(connection: Connection) -> None:
    """Delete the signal layer and every row derived from it (§12.14).

    This is the registered whole-layer deletion §12.14 defines as issue #76's
    deterministic transform: every retained row of those layers was produced
    under the retired signal-consuming contracts, so no per-row rewrite
    exists. Dropping the three derived tables rather than deleting from them
    both discards the rows and rebuilds `self_claims` in its v9 shape in one
    step, and it retires the lifecycle guards with their tables instead of
    fighting the delete guards this connection cannot satisfy. Raw logs,
    evidence items, experience facts, detection sets, and run telemetry are
    untouched.
    """

    # Foreign keys are suspended for this rebuild step, but the drop order
    # still follows the references so `PRAGMA foreign_key_check` has nothing
    # transient to see: findings and claims before the snapshots they name.
    for table in (
        "verification_findings",
        "self_claims",
        "assessment_snapshots",
        "self_signals",
    ):
        connection.execute(f"DROP TABLE {table}")
    for statement in (
        ASSESSMENT_SNAPSHOTS_SQL,
        SELF_CLAIMS_V9_SQL,
        SELF_CLAIMS_SNAPSHOT_INDEX_SQL,
        SELF_CLAIMS_V9_UPDATE_GUARD_SQL,
        ASSESSMENT_SNAPSHOTS_UPDATE_GUARD_SQL,
        SELF_CLAIMS_DELETE_GUARD_SQL,
        ASSESSMENT_SNAPSHOTS_DELETE_GUARD_SQL,
        VERIFICATION_FINDINGS_SQL,
        VERIFICATION_FINDINGS_TARGET_INDEX_SQL,
        VERIFICATION_FINDINGS_UPDATE_GUARD_SQL,
        VERIFICATION_FINDINGS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_9_to_10(connection: Connection) -> None:
    """Delete the project-scoped assessment views and drop `scope_target`.

    §12.14 registers this as A4's deterministic transform. Unlike the 8→9
    whole-layer deletion it is selective: every `assessment_snapshots` row
    whose stored scope is `project` goes, current and superseded alike, with
    its self-claims and their verification findings, while every global row is
    retained with every remaining value unchanged. The resume rows anchored to
    a deleted snapshot and the `out/branch/<branch-id>/` half of the cleanup
    have no source in this build — §22 Phase 4 has not created
    `resume_branches` — and join here with that table.

    The two derived tables lose rows rather than shape, so their delete guards
    stand aside for the transform and are back before it ends. `scope_target`
    is a column drop, which SQLite takes as a table rebuild, and that rebuild
    carries the snapshot table's own guards with it.
    """

    for trigger in (
        "verification_findings_owner_delete_guard",
        "self_claims_owner_delete_guard",
    ):
        connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute(
        """
        DELETE FROM verification_findings
        WHERE target_type = 'self_claim' AND target_id IN (
            SELECT id FROM self_claims WHERE snapshot_id IN (
                SELECT id FROM assessment_snapshots WHERE scope = 'project'
            )
        )
        """
    )
    connection.execute(
        """
        DELETE FROM self_claims WHERE snapshot_id IN (
            SELECT id FROM assessment_snapshots WHERE scope = 'project'
        )
        """
    )
    for statement in (
        VERIFICATION_FINDINGS_DELETE_GUARD_SQL,
        SELF_CLAIMS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)

    connection.execute(ASSESSMENT_SNAPSHOTS_V10_NEW_SQL)
    connection.execute(
        """
        INSERT INTO assessment_snapshots_new(
            id, created_at, superseded_at, scope, title, summary,
            gap_question_ids_json, contradiction_ids_json, verification_status,
            metadata_json, produced_by_run_id, generation_id
        )
        SELECT id, created_at, superseded_at, scope, title, summary,
               gap_question_ids_json, contradiction_ids_json,
               verification_status, metadata_json, produced_by_run_id,
               generation_id
        FROM assessment_snapshots
        WHERE scope <> 'project'
        """
    )
    connection.execute("DROP TABLE assessment_snapshots")
    connection.execute(
        "ALTER TABLE assessment_snapshots_new RENAME TO assessment_snapshots"
    )
    for statement in (
        ASSESSMENT_SNAPSHOTS_V10_UPDATE_GUARD_SQL,
        ASSESSMENT_SNAPSHOTS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)


def apply_migration_10_to_11(connection: Connection) -> None:
    """Create the §13.8 job-description layer (§12.14).

    Purely additive: a v10 workspace holds no job description, so there is no
    row to transform and no existing table to rebuild.
    """

    for statement in (
        JOB_DESCRIPTIONS_SQL,
        JOB_DESCRIPTIONS_UPDATE_GUARD_SQL,
        JOB_DESCRIPTIONS_DELETE_GUARD_SQL,
    ):
        connection.execute(statement)
