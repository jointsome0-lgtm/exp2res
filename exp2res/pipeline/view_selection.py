"""Shared deterministic §13.6 assessment-view selection."""

from __future__ import annotations

import sqlite3

from exp2res.domain.models import ExperienceFact
from exp2res.storage.repository import list_experience_facts


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def select_assessment_view(
    connection: sqlite3.Connection,
) -> tuple[ExperienceFact, ...]:
    """Select the sole §13.6 view's subject set: every current fact, ID-ordered.

    Stage 7 re-derives Stage 6's selection from this one definition, so the
    subject set a verification reads is the set its generation was authored
    against rather than a second, independently drifting rule.
    """

    return tuple(
        sorted(list_experience_facts(connection), key=lambda item: _id_key(item.id))
    )
