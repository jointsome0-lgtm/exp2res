"""Strict persistence and hydration for the implemented §11 entity subset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterable

from pydantic import ValidationError

from exp2res.domain.models import (
    EvidenceItem,
    ExperienceFact,
    RawLog,
    canonical_project_key,
)
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
    project_key = (
        None if raw_log.project is None else canonical_project_key(raw_log.project)
    )
    try:
        connection.execute(
            """
            INSERT INTO raw_logs(
                id, recorded_at, entry_type, source_type,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, raw_text, project, project_key,
                external_ref, corrects_log_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                project_key,
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


def _hydrate(
    model: type[RawLog] | type[EvidenceItem] | type[ExperienceFact],
    payload: dict[str, object],
):
    try:
        return model.model_validate_json(_json(payload))
    except (ValidationError, ValueError, TypeError, UnicodeError) as error:
        raise HydrationFailureError() from error


def hydrate_raw_log(row: sqlite3.Row) -> RawLog:
    try:
        metadata = json.loads(row["metadata_json"])
        project = row["project"]
        project_key = row["project_key"]
        expected_key = None if project is None else canonical_project_key(project)
        if project_key != expected_key or (project is not None and not expected_key):
            raise ValueError("stored project key disagrees with project label")
    except (json.JSONDecodeError, IndexError, TypeError, ValueError, UnicodeError) as error:
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


def insert_experience_fact(
    connection: sqlite3.Connection,
    fact: ExperienceFact,
    *,
    project_key: str | None,
    produced_by_run_id: str,
    generation_id: str,
) -> None:
    expected_key = (
        None if fact.project is None else canonical_project_key(fact.project)
    )
    if (
        project_key != expected_key
        or (fact.project is not None and not expected_key)
        or not produced_by_run_id
        or not generation_id
    ):
        raise IntegrityFailureError()
    if len(fact.evidence_item_ids) != len(set(fact.evidence_item_ids)):
        raise IntegrityFailureError()
    derived_source_ids: set[str] = set()
    for evidence_id in fact.evidence_item_ids:
        source = connection.execute(
            "SELECT raw_log_id FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        if source is None:
            raise IntegrityFailureError()
        derived_source_ids.add(source[0])
    if derived_source_ids != set(fact.source_log_ids):
        raise IntegrityFailureError()
    try:
        connection.execute(
            """
            INSERT INTO experience_facts(
                id, created_at, superseded_at, claim, claim_kind, project,
                project_key, role, company, context, ownership_level, action,
                object, outcome, skills_json, technologies_json, themes_json,
                occurred_start, occurred_end, temporal_precision,
                temporal_confidence, confidence, metadata_json,
                produced_by_run_id, generation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.id,
                _iso(fact.created_at),
                _iso(fact.superseded_at),
                fact.claim,
                fact.claim_kind,
                fact.project,
                project_key,
                fact.role,
                fact.company,
                fact.context,
                fact.ownership_level,
                fact.action,
                fact.object,
                fact.outcome,
                _json(fact.skills),
                _json(fact.technologies),
                _json(fact.themes),
                _iso(fact.occurred.start),
                _iso(fact.occurred.end),
                fact.occurred.precision,
                fact.occurred.confidence,
                fact.confidence,
                _json(fact.metadata),
                produced_by_run_id,
                generation_id,
            ),
        )
        connection.executemany(
            """
            INSERT INTO fact_sources(fact_id, evidence_item_id, support_type)
            VALUES (?, ?, 'direct')
            """,
            ((fact.id, evidence_id) for evidence_id in fact.evidence_item_ids),
        )
    except sqlite3.IntegrityError as error:
        if "experience_facts.id" in str(error):
            raise IdCollisionError() from error
        raise IntegrityFailureError() from error


def hydrate_experience_fact(
    row: sqlite3.Row, source_rows: Iterable[sqlite3.Row]
) -> ExperienceFact:
    try:
        metadata = json.loads(row["metadata_json"])
        skills = json.loads(row["skills_json"])
        technologies = json.loads(row["technologies_json"])
        themes = json.loads(row["themes_json"])
        project = row["project"]
        project_key = row["project_key"]
        expected_key = None if project is None else canonical_project_key(project)
        if project_key != expected_key or (project is not None and not expected_key):
            raise ValueError("stored project key disagrees with project label")

        sources = list(source_rows)
        if not sources:
            raise ValueError("experience fact has no fact_sources rows")
        evidence_ids: list[str] = []
        raw_log_ids: set[str] = set()
        direct = False
        for source in sources:
            if source["fact_id"] != row["id"]:
                raise ValueError("fact source belongs to a different fact")
            support_type = source["support_type"]
            if support_type not in {"direct", "corroborating"}:
                raise ValueError("invalid fact source support type")
            direct = direct or support_type == "direct"
            evidence_ids.append(source["evidence_item_id"])
            raw_log_ids.add(source["raw_log_id"])
        if not direct or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("invalid fact source set")
        evidence_ids.sort(key=lambda value: value.encode("utf-8"))
        source_log_ids = sorted(raw_log_ids, key=lambda value: value.encode("utf-8"))
    except (
        AttributeError,
        json.JSONDecodeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
    ) as error:
        raise HydrationFailureError() from error

    payload = {
        "id": row["id"],
        "created_at": row["created_at"],
        "superseded_at": row["superseded_at"],
        "claim": row["claim"],
        "claim_kind": row["claim_kind"],
        "project": project,
        "role": row["role"],
        "company": row["company"],
        "context": row["context"],
        "ownership_level": row["ownership_level"],
        "action": row["action"],
        "object": row["object"],
        "outcome": row["outcome"],
        "skills": skills,
        "technologies": technologies,
        "themes": themes,
        "occurred": {
            "start": row["occurred_start"],
            "end": row["occurred_end"],
            "precision": row["temporal_precision"],
            "confidence": row["temporal_confidence"],
        },
        "source_log_ids": source_log_ids,
        "evidence_item_ids": evidence_ids,
        "confidence": row["confidence"],
        "metadata": metadata,
    }
    return _hydrate(ExperienceFact, payload)


def _fact_source_rows(
    connection: sqlite3.Connection, fact_id: str
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT fs.fact_id, fs.evidence_item_id, fs.support_type, ei.raw_log_id
        FROM fact_sources AS fs
        JOIN evidence_items AS ei ON ei.id = fs.evidence_item_id
        WHERE fs.fact_id = ?
        """,
        (fact_id,),
    ).fetchall()


def get_experience_fact(
    connection: sqlite3.Connection, fact_id: str
) -> ExperienceFact | None:
    row = connection.execute(
        "SELECT * FROM experience_facts WHERE id = ?", (fact_id,)
    ).fetchone()
    if row is None:
        return None
    return hydrate_experience_fact(row, _fact_source_rows(connection, fact_id))


def list_experience_facts(
    connection: sqlite3.Connection, *, current_only: bool = True
) -> tuple[ExperienceFact, ...]:
    where = " WHERE superseded_at IS NULL" if current_only else ""
    rows = connection.execute("SELECT * FROM experience_facts" + where).fetchall()
    facts = [
        hydrate_experience_fact(row, _fact_source_rows(connection, row["id"]))
        for row in rows
    ]
    facts.sort(
        key=lambda item: (
            item.created_at.astimezone(timezone.utc),
            item.id.encode("utf-8"),
        )
    )
    return tuple(facts)


def mark_facts_superseded(
    connection: sqlite3.Connection,
    fact_ids: Iterable[str],
    superseded_at: datetime,
) -> None:
    if superseded_at.tzinfo is None or superseded_at.utcoffset() is None:
        raise IntegrityFailureError()
    ids = list(fact_ids)
    if len(ids) != len(set(ids)):
        raise IntegrityFailureError()
    for fact_id in ids:
        row = connection.execute(
            "SELECT superseded_at FROM experience_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if row is None or row[0] is not None:
            raise IntegrityFailureError()
    try:
        for fact_id in ids:
            cursor = connection.execute(
                """
                UPDATE experience_facts SET superseded_at = ?
                WHERE id = ? AND superseded_at IS NULL
                """,
                (_iso(superseded_at), fact_id),
            )
            if cursor.rowcount != 1:
                raise IntegrityFailureError()
    except sqlite3.IntegrityError as error:
        raise IntegrityFailureError() from error


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
