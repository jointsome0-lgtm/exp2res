"""Shared §13.3 rule 10 evidence-item projection."""

from __future__ import annotations

import sqlite3
from typing import Sequence

from exp2res.domain.models import EvidenceItem, ExperienceFact
from exp2res.errors import IntegrityFailureError
from exp2res.llm.fact_extractor import DisplacedSupportDescriptor
from exp2res.storage.repository import hydrate_evidence_item


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def project_evidence_context(
    connection: sqlite3.Connection,
    facts: Sequence[ExperienceFact],
    *,
    missing_diagnostic: str,
) -> tuple[EvidenceItem | DisplacedSupportDescriptor, ...]:
    evidence_ids = sorted(
        {item_id for fact in facts for item_id in fact.evidence_item_ids},
        key=_id_key,
    )
    context: list[EvidenceItem | DisplacedSupportDescriptor] = []
    for item_id in evidence_ids:
        row = connection.execute(
            """
            SELECT item.*,
                   EXISTS(
                       SELECT 1 FROM raw_logs AS correction
                       WHERE correction.corrects_log_id = owner.id
                   ) AS owner_displaced
            FROM evidence_items AS item
            JOIN raw_logs AS owner ON owner.id = item.raw_log_id
            WHERE item.id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise IntegrityFailureError(missing_diagnostic)
        item = hydrate_evidence_item(row)
        if row["owner_displaced"]:
            context.append(
                DisplacedSupportDescriptor(
                    id=item.id,
                    raw_log_id=item.raw_log_id,
                    strength=item.strength,
                    uri=item.uri,
                    path=item.path,
                )
            )
        else:
            context.append(item)
    return tuple(context)
