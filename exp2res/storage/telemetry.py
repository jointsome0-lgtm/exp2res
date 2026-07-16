"""Telemetry-only persistence for §12.13 processing runs and §12.15 calls."""

from __future__ import annotations

from datetime import datetime
import json
import sqlite3
from typing import Iterable

from exp2res.errors import IntegrityFailureError


TERMINAL_STATUSES = frozenset({"completed", "failed"})


def _is_hash(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("telemetry timestamps must carry an offset")
    return value.isoformat()


def _ids(values: Iterable[str]) -> str:
    result = list(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError("telemetry IDs must be non-empty strings")
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _metadata(value: dict[str, str] | None) -> str:
    payload = {} if value is None else value
    if any(
        not isinstance(key, str)
        or not key
        or not isinstance(item, str)
        or not item
        for key, item in payload.items()
    ):
        raise ValueError("telemetry metadata must contain non-empty string pairs")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_processing_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    stage: str,
    started_at: datetime,
    provider: str | None,
    model: str | None,
    prompt_policy_hash: str | None,
    parent_run_id: str | None = None,
    input_ids: Iterable[str] = (),
    metadata: dict[str, str] | None = None,
) -> None:
    """Create one running execution record without content-bearing values."""

    if not run_id or not stage:
        raise ValueError("run identity must be non-empty")
    llm_identity = (provider, model, prompt_policy_hash)
    if any(value is None for value in llm_identity) != all(
        value is None for value in llm_identity
    ):
        raise ValueError("LLM execution identity must be complete or absent")
    try:
        connection.execute(
            """
            INSERT INTO processing_runs(
                id, stage, parent_run_id, started_at, status, provider, model,
                prompt_policy_hash, input_ids_json, output_ids_json, metadata_json
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, '[]', ?)
            """,
            (
                run_id,
                stage,
                parent_run_id,
                _iso(started_at),
                provider,
                model,
                prompt_policy_hash,
                _ids(input_ids),
                _metadata(metadata),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError() from error


def create_llm_call(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    call_index: int,
    started_at: datetime,
    input_hash: str,
    provider_request_id: str,
) -> None:
    """Create the single row for one planned logical invocation."""

    if call_index < 1 or not provider_request_id or not _is_hash(input_hash):
        raise ValueError("invalid call telemetry identity")
    existing = connection.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE run_id = ?", (run_id,)
    ).fetchone()
    if existing is None or call_index != existing[0] + 1:
        raise IntegrityFailureError()
    try:
        connection.execute(
            """
            INSERT INTO llm_calls(
                run_id, call_index, started_at, status, input_hash,
                provider_request_id, transport_retries, schema_retries
            ) VALUES (?, ?, ?, 'running', ?, ?, 0, 0)
            """,
            (
                run_id,
                call_index,
                _iso(started_at),
                input_hash,
                provider_request_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError() from error


def increment_call_retry(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    call_index: int,
    retry_kind: str,
) -> None:
    """Increment a retry counter only before the retry that will execute."""

    column = {
        "transport": "transport_retries",
        "schema": "schema_retries",
    }.get(retry_kind)
    if column is None:
        raise ValueError("unknown retry kind")
    cursor = connection.execute(
        f"""
        UPDATE llm_calls
        SET {column} = COALESCE({column}, 0) + 1
        WHERE run_id = ? AND call_index = ? AND status NOT IN ('completed', 'failed')
        """,
        (run_id, call_index),
    )
    if cursor.rowcount != 1:
        raise IntegrityFailureError()


def require_running_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    provider: str,
    model: str,
    prompt_policy_hash: str,
) -> None:
    """Gate a later call index on the run's single execution configuration."""

    row = connection.execute(
        """
        SELECT status, provider, model, prompt_policy_hash
        FROM processing_runs WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None or row[0] != "running":
        raise IntegrityFailureError()
    if (row[1], row[2], row[3]) != (provider, model, prompt_policy_hash):
        raise IntegrityFailureError()


def _stored_metadata(connection: sqlite3.Connection, run_id: str) -> dict[str, str]:
    row = connection.execute(
        "SELECT metadata_json FROM processing_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise IntegrityFailureError()
    try:
        stored = json.loads(row[0])
    except (TypeError, ValueError) as error:
        raise IntegrityFailureError() from error
    if not isinstance(stored, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in stored.items()
    ):
        raise IntegrityFailureError()
    return stored


def merge_run_metadata(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    metadata: dict[str, str],
) -> None:
    """Fold per-call keys into a still-running run without finishing it."""

    merged = {**_stored_metadata(connection, run_id), **metadata}
    cursor = connection.execute(
        """
        UPDATE processing_runs SET metadata_json = ?
        WHERE id = ? AND status NOT IN ('completed', 'failed')
        """,
        (_metadata(merged), run_id),
    )
    if cursor.rowcount != 1:
        raise IntegrityFailureError()


def finish_llm_call(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    call_index: int,
    finished_at: datetime,
    status: str,
    output_hash: str | None = None,
    failure_code: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    reported_cost: str | None = None,
) -> None:
    """Finish a call with hashes/counts/codes only, never response content."""

    if status not in TERMINAL_STATUSES:
        raise ValueError("call status must be terminal")
    if (status == "completed") != (failure_code is None):
        raise ValueError("call failure code disagrees with status")
    if status == "completed" and output_hash is None:
        raise ValueError("completed call requires validated output hash")
    if output_hash is not None and not _is_hash(output_hash):
        raise ValueError("output hash must be lowercase SHA-256 hexadecimal")
    cursor = connection.execute(
        """
        UPDATE llm_calls
        SET finished_at = ?, status = ?, output_hash = ?, failure_code = ?,
            prompt_tokens = ?, completion_tokens = ?, reported_cost = ?
        WHERE run_id = ? AND call_index = ? AND status NOT IN ('completed', 'failed')
        """,
        (
            _iso(finished_at),
            status,
            output_hash,
            failure_code,
            prompt_tokens,
            completion_tokens,
            reported_cost,
            run_id,
            call_index,
        ),
    )
    if cursor.rowcount != 1:
        raise IntegrityFailureError()


def finish_processing_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    finished_at: datetime,
    status: str,
    failure_code: str | None = None,
    output_ids: Iterable[str] = (),
    metadata: dict[str, str] | None = None,
) -> None:
    """Finish a run after all candidate validation/business work resolves."""

    if status not in TERMINAL_STATUSES:
        raise ValueError("run status must be terminal")
    if (status == "completed") != (failure_code is None):
        raise ValueError("run failure code disagrees with status")
    ids_json = _ids(output_ids) if status == "completed" else "[]"
    merged = {**_stored_metadata(connection, run_id), **(metadata or {})}
    metadata = merged
    cursor = connection.execute(
        """
        UPDATE processing_runs
        SET finished_at = ?, status = ?, failure_code = ?,
            output_ids_json = ?, metadata_json = ?
        WHERE id = ? AND status NOT IN ('completed', 'failed')
        """,
        (
            _iso(finished_at),
            status,
            failure_code,
            ids_json,
            _metadata(metadata),
            run_id,
        ),
    )
    if cursor.rowcount != 1:
        raise IntegrityFailureError()


def reconcile_abandoned_telemetry(
    connection: sqlite3.Connection, *, finished_at: datetime
) -> tuple[int, int]:
    """Apply §15.10 rule 8 to every abandoned nonterminal run and call."""

    timestamp = _iso(finished_at)
    unknown_call = connection.execute(
        """
        SELECT 1 FROM llm_calls
        WHERE status NOT IN ('planned', 'running', 'completed', 'failed')
        LIMIT 1
        """
    ).fetchone()
    unknown_run = connection.execute(
        """
        SELECT 1 FROM processing_runs
        WHERE status NOT IN ('planned', 'running', 'completed', 'failed')
        LIMIT 1
        """
    ).fetchone()
    if unknown_call is not None or unknown_run is not None:
        raise IntegrityFailureError()
    calls = connection.execute(
        """
        UPDATE llm_calls
        SET status = 'failed', failure_code = 'cancelled',
            finished_at = COALESCE(finished_at, ?)
        WHERE status IN ('planned', 'running')
        """,
        (timestamp,),
    ).rowcount
    runs = connection.execute(
        """
        UPDATE processing_runs
        SET status = 'failed', failure_code = 'cancelled',
            finished_at = COALESCE(finished_at, ?)
        WHERE status IN ('planned', 'running')
        """,
        (timestamp,),
    ).rowcount
    return runs, calls
