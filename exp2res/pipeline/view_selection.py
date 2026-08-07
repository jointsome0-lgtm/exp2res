"""Shared deterministic §13.6 assessment-view selection."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from exp2res.domain.enums import AssessmentScope
from exp2res.domain.models import ExperienceFact, canonical_project_key
from exp2res.storage.repository import list_experience_facts


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


@dataclass(frozen=True)
class AssessmentViewSelection:
    facts: tuple[ExperienceFact, ...]


def select_assessment_view(
    connection: sqlite3.Connection,
    *,
    scope: AssessmentScope,
    scope_target: str | None,
) -> AssessmentViewSelection:
    """Re-derive Stage 6's exact global/project subject selection."""

    all_facts = tuple(
        sorted(list_experience_facts(connection), key=lambda item: _id_key(item.id))
    )
    if scope == "global":
        return AssessmentViewSelection(all_facts)

    assert scope_target is not None
    project_key = canonical_project_key(scope_target)
    subject_ids = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM experience_facts "
            "WHERE superseded_at IS NULL AND project_key = ?",
            (project_key,),
        )
    }
    return AssessmentViewSelection(
        tuple(fact for fact in all_facts if fact.id in subject_ids)
    )
