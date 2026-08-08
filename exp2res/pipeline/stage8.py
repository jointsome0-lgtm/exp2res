"""§13.8 job-description parsing."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterable, Pattern, Sequence, cast

from pydantic import BaseModel

from exp2res.domain.models import JDRequirement, JobDescription, ParsedJD
from exp2res.errors import LLMCancelledError
from exp2res.llm.contracts import ContractWarning
from exp2res.llm.jd_parser import (
    JD_PARSER_CONTRACT,
    JDParserInput,
    JDParserOutput,
    JobDescriptionPayload,
)
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    insert_job_description,
    retained_requirement_ids,
)
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, writer_database

from .orchestration import PlannedCall, run_complete_stage


# §12 rule 11: allocation is collision-resistant with a random component, so a
# fresh value colliding is a defect rather than a load condition. The bounded
# local retry is §15.1's "retry locally when safe"; exhausting it fails the run
# atomically without another parser call.
_ALLOCATION_ATTEMPTS = 8


@dataclass(frozen=True)
class _ResolvedParse:
    job_description: JobDescription
    warnings: tuple[ContractWarning, ...]


@dataclass(frozen=True)
class Stage8Result:
    run_id: str
    job_description_id: str
    created_at: datetime
    title: str | None
    company: str | None
    requirement_ids: tuple[str, ...]
    warnings: tuple[ContractWarning, ...]


def _allocate(
    id_factory: Callable[[str], str], kind: str, taken: set[str]
) -> str:
    for _attempt in range(_ALLOCATION_ATTEMPTS):
        candidate = id_factory(kind)
        if candidate and candidate not in taken:
            taken.add(candidate)
            return candidate
    raise ValueError("job-description ID allocation failed")


def _resolve_for(
    *,
    connection: sqlite3.Connection,
    raw_text: str,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> Callable[[BaseModel], object]:
    def resolve(validated: BaseModel) -> object:
        output = cast(JDParserOutput, validated)
        # §11.13/§12 rule 10: requirement IDs are globally unique, so the
        # allocation avoids every ID a retained job description already holds
        # as well as every ID allocated for this candidate.
        taken = retained_requirement_ids(connection)
        requirements = tuple(
            JDRequirement(
                id=_allocate(id_factory, "jd_requirement", taken),
                kind=candidate.kind,
                text=candidate.text,
                keywords=list(candidate.keywords),
            )
            for candidate in output.parsed.requirements
        )
        parsed = ParsedJD(
            requirements=list(requirements),
            seniority_signals=list(output.parsed.seniority_signals),
            domain_signals=list(output.parsed.domain_signals),
            keywords=list(output.parsed.keywords),
            red_flags=list(output.parsed.red_flags),
        )
        entity_ids = {
            row[0] for row in connection.execute("SELECT id FROM job_descriptions")
        }
        job_description = JobDescription(
            id=_allocate(id_factory, "job_description", entity_ids),
            created_at=clock(),
            title=output.title,
            company=output.company,
            raw_text=raw_text,
            parsed=parsed,
        )
        return _ResolvedParse(job_description, tuple(output.warnings))

    return resolve


def run_job_description_parse(
    workspace: Path,
    *,
    raw_text: str,
    selection: LLMSelection,
    budgets: CallBudgets,
    runner: ContractRunner,
    id_factory: Callable[[str], str] = new_id,
    parent_run_id: str | None = None,
    reconcile: bool = True,
    connection: sqlite3.Connection | None = None,
    clock: Callable[[], datetime] | None = None,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    cli_version: str = "test-double",
    capability_check: Callable[[], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] | None = None,
    resolved_credentials: Iterable[bytes] = (),
) -> Stage8Result:
    """Parse one owner-supplied vacancy and persist it atomically."""

    now = clock or (lambda: datetime.now(timezone.utc))
    held = (
        nullcontext(connection)
        if connection is not None
        else writer_database(workspace, timeout_ms=timeout_ms, reconcile=reconcile)
    )
    # §14.14 rule 6: the parser's warnings live only in the resolved candidate,
    # so a cancellation delivered at or after the business commit would
    # otherwise report a durable job description with none of the advisories
    # that describe it. The capture spans the whole writer context — commit,
    # result construction, lock and connection teardown — because the
    # interrupt can land anywhere in it.
    resolved_parses: list[_ResolvedParse] = []
    try:
        return _run_locked_parse(
            workspace,
            held=held,
            raw_text=raw_text,
            selection=selection,
            budgets=budgets,
            runner=runner,
            id_factory=id_factory,
            parent_run_id=parent_run_id,
            now=now,
            cli_version=cli_version,
            capability_check=capability_check,
            monotonic=monotonic,
            sleeper=sleeper,
            jitter=jitter,
            token_patterns=token_patterns,
            resolved_credentials=resolved_credentials,
            resolved_parses=resolved_parses,
        )
    except (LLMCancelledError, KeyboardInterrupt) as error:
        # Only cancellation: a failed run persisted nothing its advisories
        # could describe.
        if resolved_parses and not getattr(error, "warnings", None):
            error.warnings = list(resolved_parses[-1].warnings)
        raise


def _run_locked_parse(
    workspace: Path,
    *,
    held,
    raw_text: str,
    selection: LLMSelection,
    budgets: CallBudgets,
    runner: ContractRunner,
    id_factory: Callable[[str], str],
    parent_run_id: str | None,
    now: Callable[[], datetime],
    cli_version: str,
    capability_check: Callable[[], None] | None,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
    jitter: Callable[[float, float], float] | None,
    token_patterns: Iterable[Pattern[bytes]] | None,
    resolved_credentials: Iterable[bytes],
    resolved_parses: list[_ResolvedParse],
) -> Stage8Result:
    with held as connection:
        # §15.9: the input is the owner-supplied payload, so no entity ID
        # exists yet and none transits (§29.3). `input_ids` stays empty for
        # the same reason.
        input_payload = JDParserInput(
            job_description=JobDescriptionPayload(raw_text=raw_text)
        )
        run_id = id_factory("run")
        resolve_candidate = _resolve_for(
            connection=connection,
            raw_text=raw_text,
            id_factory=id_factory,
            clock=now,
        )

        def resolve(validated: BaseModel) -> object:
            candidate = resolve_candidate(validated)
            resolved_parses.append(cast(_ResolvedParse, candidate))
            return candidate

        planned = (
            PlannedCall(
                input_payload=input_payload,
                input_ids=(),
                enrich=None,
                resolve=resolve,
            ),
        )

        def commit(
            held: sqlite3.Connection, resolved: Sequence[object]
        ) -> Iterable[str]:
            candidate = cast(_ResolvedParse, resolved[0])
            insert_job_description(held, candidate.job_description)
            return (candidate.job_description.id,)

        outcome = run_complete_stage(
            workspace,
            connection,
            stage="13.8",
            contract=JD_PARSER_CONTRACT,
            selection=selection,
            budgets=budgets,
            runner=runner,
            planned=planned,
            commit=commit,
            run_id=run_id,
            parent_run_id=parent_run_id,
            clock=now,
            cli_version=cli_version,
            capability_check=capability_check,
            monotonic=monotonic,
            sleeper=sleeper,
            jitter=jitter,
            token_patterns=token_patterns,
            resolved_credentials=resolved_credentials,
            input_ids=(),
        )
        resolved = cast(_ResolvedParse, outcome.resolved[0])
        persisted = resolved.job_description
        # The result carries the §14.14 rule 5 projection fields only: the
        # vacancy text stays in the database and never reaches an envelope.
        return Stage8Result(
            run_id=run_id,
            job_description_id=persisted.id,
            created_at=persisted.created_at,
            title=persisted.title,
            company=persisted.company,
            requirement_ids=tuple(
                requirement.id for requirement in persisted.parsed.requirements
            ),
            warnings=resolved.warnings,
        )
