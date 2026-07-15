"""Strict persistence and hydration for the Phase 0 entity subset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import json
import sqlite3

from pydantic import ValidationError

from exp2res.domain.models import EvidenceItem, RawLog
from exp2res.errors import HydrationFailureError, IdCollisionError, IntegrityFailureError


@dataclass(frozen=True)
class RawLogBundle:
    raw_log: RawLog
    evidence_items: tuple[EvidenceItem, ...]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat()  # type: ignore[union-attr]


def insert_raw_log(connection: sqlite3.Connection, raw_log: RawLog) -> None:
    try:
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, raw_text, project, external_ref,
                corrects_log_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_log.id,
                _iso(raw_log.recorded_at),
                raw_log.entry_type,
                raw_log.source_type,
                _iso(raw_log.occurred.start),
                _iso(raw_log.occurred.end),
                raw_log.occurred.precision,
                raw_log.occurred.confidence,
                raw_log.raw_text,
                raw_log.project,
                raw_log.external_ref,
                raw_log.corrects_log_id,
                _json(raw_log.metadata),
            ),
        )
    except sqlite3.IntegrityError as error:
        if "raw_logs.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError() from error


def insert_evidence_item(connection: sqlite3.Connection, item: EvidenceItem) -> None:
    try:
        connection.execute(
            """
            INSERT INTO evidence_items(
                id, created_at, raw_log_id, title, summary, uri, path,
                strength, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                _iso(item.created_at),
                item.raw_log_id,
                item.title,
                item.summary,
                item.uri,
                item.path,
                item.strength,
                _json(item.metadata),
            ),
        )
    except sqlite3.IntegrityError as error:
        if "evidence_items.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError() from error


def _hydrate(model: type[RawLog] | type[EvidenceItem], payload: dict[str, object]):
    try:
        return model.model_validate_json(_json(payload))
    except (ValidationError, ValueError, TypeError, UnicodeError) as error:
        raise HydrationFailureError() from error


def hydrate_raw_log(row: sqlite3.Row) -> RawLog:
    try:
        metadata = json.loads(row["metadata_json"])
    except (json.JSONDecodeError, TypeError) as error:
        raise HydrationFailureError() from error
    payload = {
        "id": row["id"],
        "recorded_at": row["recorded_at"],
        "entry_type": row["entry_type"],
        "source_type": row["source_type"],
        "occurred": {
            "start": row["occurred_start"],
            "end": row["occurred_end"],
            "precision": row["temporal_precision"],
            "confidence": row["temporal_confidence"],
        },
        "raw_text": row["raw_text"],
        "project": row["project"],
        "external_ref": row["external_ref"],
        "corrects_log_id": row["corrects_log_id"],
        "metadata": metadata,
    }
    return _hydrate(RawLog, payload)


def hydrate_evidence_item(row: sqlite3.Row) -> EvidenceItem:
    try:
        metadata = json.loads(row["metadata_json"])
    except (json.JSONDecodeError, TypeError) as error:
        raise HydrationFailureError() from error
    payload = {
        "id": row["id"],
        "created_at": row["created_at"],
        "raw_log_id": row["raw_log_id"],
        "title": row["title"],
        "summary": row["summary"],
        "uri": row["uri"],
        "path": row["path"],
        "strength": row["strength"],
        "metadata": metadata,
    }
    return _hydrate(EvidenceItem, payload)


def get_raw_log(connection: sqlite3.Connection, log_id: str) -> RawLog | None:
    row = connection.execute("SELECT * FROM raw_logs WHERE id = ?", (log_id,)).fetchone()
    return None if row is None else hydrate_raw_log(row)


def get_evidence_for_log(
    connection: sqlite3.Connection, log_id: str
) -> tuple[EvidenceItem, ...]:
    rows = connection.execute(
        "SELECT * FROM evidence_items WHERE raw_log_id = ?", (log_id,)
    ).fetchall()
    items = [hydrate_evidence_item(row) for row in rows]
    items.sort(key=lambda item: (item.created_at.astimezone(timezone.utc), item.id))
    return tuple(items)


def get_bundle(connection: sqlite3.Connection, log_id: str) -> RawLogBundle | None:
    raw_log = get_raw_log(connection, log_id)
    if raw_log is None:
        return None
    return RawLogBundle(raw_log, get_evidence_for_log(connection, log_id))


def list_raw_logs(connection: sqlite3.Connection) -> tuple[RawLog, ...]:
    rows = connection.execute("SELECT * FROM raw_logs").fetchall()
    logs = [hydrate_raw_log(row) for row in rows]
    logs.sort(key=lambda item: (item.recorded_at.astimezone(timezone.utc), item.id))
    return tuple(logs)
