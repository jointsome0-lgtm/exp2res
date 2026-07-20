"""Shared deterministic §13.6 assessment-view selection."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from exp2res.domain.enums import AssessmentScope
from exp2res.domain.models import (
    ExperienceFact,
    SelfSignal,
    canonical_project_key,
)
from exp2res.errors import IntegrityFailureError
from exp2res.storage.repository import list_experience_facts, list_self_signals


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


@dataclass(frozen=True)
class AssessmentViewSelection:
    facts: tuple[ExperienceFact, ...]
    signals: tuple[SelfSignal, ...]
    context_facts: tuple[ExperienceFact, ...]


def select_assessment_view(
    connection: sqlite3.Connection,
    *,
    scope: AssessmentScope,
    scope_target: str | None,
) -> AssessmentViewSelection:
    """Re-derive Stage 6's exact global/project subject and context rows."""

    all_facts = tuple(
        sorted(list_experience_facts(connection), key=lambda item: _id_key(item.id))
    )
    all_signals = tuple(
        sorted(list_self_signals(connection), key=lambda item: _id_key(item.id))
    )
    if scope == "global":
        return AssessmentViewSelection(all_facts, all_signals, ())

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
    fact_by_id = {fact.id: fact for fact in all_facts}
    facts = tuple(
        sorted(
            (fact_by_id[item] for item in subject_ids),
            key=lambda item: _id_key(item.id),
        )
    )
    signals = tuple(
        signal
        for signal in all_signals
        if subject_ids.intersection(
            (*signal.supporting_fact_ids, *signal.counter_fact_ids)
        )
    )
    context_ids = {
        fact_id
        for signal in signals
        for fact_id in (*signal.supporting_fact_ids, *signal.counter_fact_ids)
        if fact_id not in subject_ids
    }
    try:
        context_facts = tuple(
            sorted(
                (fact_by_id[item] for item in context_ids),
                key=lambda item: _id_key(item.id),
            )
        )
    except KeyError as error:
        raise IntegrityFailureError("assessment_context_fact_missing") from error
    return AssessmentViewSelection(facts, signals, context_facts)
