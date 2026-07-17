"""Read-only §14.6 ExperienceFact inspection services."""

from __future__ import annotations

from pathlib import Path

from exp2res.domain.models import ExperienceFact
from exp2res.errors import SelectorNotFoundError
from exp2res.storage.repository import get_experience_fact, list_experience_facts
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, read_database


def list_facts(
    workspace: Path, *, timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> tuple[ExperienceFact, ...]:
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        return list_experience_facts(connection)


def show_fact(
    workspace: Path,
    *,
    fact_id: str,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ExperienceFact:
    with read_database(workspace, timeout_ms=timeout_ms) as connection:
        fact = get_experience_fact(connection, fact_id)
        # §14.14 rule 7: the §14.6 surface inspects current facts only —
        # historical-generation browsing beyond `runs show` is deferred, so
        # a superseded row is not addressable here.
        if fact is None or fact.superseded_at is not None:
            raise SelectorNotFoundError()
        return fact
