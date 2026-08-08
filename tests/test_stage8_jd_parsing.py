"""Offline §13.8/§15.9 job-description parsing, ID allocation, and atomicity."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from exp2res.errors import LLMInvocationError
from exp2res.llm.contracts import strict_output_schema
from exp2res.llm.jd_parser import JD_PARSER_CONTRACT, JDParserOutput
from exp2res.pipeline.stage8 import run_job_description_parse
from exp2res.storage.repository import list_job_descriptions
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets


pytestmark = pytest.mark.contract

VACANCY = (
    "We require evidence-grounded LLM workflow design. "
    "Production operations experience is preferred."
)


class ParserIds:
    __test__ = False

    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        self.counts[kind] += 1
        return f"{kind}_vera_{self.counts[kind]:04d}"


def parser_response(
    *,
    title: str | None = "Agent Engineer",
    company: str | None = "Vera Example Systems",
    requirements: list[dict[str, object]] | None = None,
    warnings: list[dict[str, str]] | None = None,
) -> bytes:
    return json.dumps(
        {
            "title": title,
            "company": company,
            "parsed": {
                "requirements": [
                    {
                        "kind": "required_skill",
                        "text": "Evidence-grounded LLM workflow design",
                        "keywords": ["LLM", "evidence-grounded"],
                    },
                    {
                        "kind": "preferred_skill",
                        "text": "Production operations experience",
                        "keywords": ["production operations"],
                    },
                ]
                if requirements is None
                else requirements,
                "seniority_signals": [],
                "domain_signals": ["LLM systems"],
                "keywords": ["LLM", "evidence-grounded", "production operations"],
                "red_flags": [],
            },
            "warnings": [] if warnings is None else warnings,
        },
        separators=(",", ":"),
    ).encode()


def run_stage8(
    workspace: Path,
    fake: FakeContractRunner,
    ids: ParserIds,
    *,
    raw_text: str = VACANCY,
):
    return run_job_description_parse(
        workspace,
        raw_text=raw_text,
        selection=SELECTION,
        budgets=budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def test_the_model_never_sees_a_service_owned_id_field() -> None:
    """§15.9/§24.27: no entity ID exists at call time, so none is declared."""

    schema = strict_output_schema(JD_PARSER_CONTRACT)
    serialized = json.dumps(schema)
    assert '"id"' not in serialized
    assert '"created_at"' not in serialized
    assert '"raw_text"' not in serialized
    requirement = schema["$defs"]["JDRequirementCandidate"]
    assert requirement["additionalProperties"] is False
    assert sorted(requirement["required"]) == ["keywords", "kind", "text"]
    assert sorted(schema["required"]) == ["company", "parsed", "title", "warnings"]


def test_an_omitted_model_authored_field_is_a_missing_judgment() -> None:
    """§15.11: an omitted key is not a conservative default."""

    payload = json.loads(parser_response())
    del payload["company"]
    with pytest.raises(ValidationError):
        JDParserOutput.model_validate_json(json.dumps(payload))


@pytest.mark.lifecycle
def test_happy_path_persists_typed_parse_with_service_assigned_ids(
    workspace: Path,
) -> None:
    """§21.24/§24.25: one typed ParsedJD with opaque, non-positional IDs."""

    ids = ParserIds()
    fake = FakeContractRunner(
        [
            parser_response(
                warnings=[
                    {
                        "type": "vera_note",
                        "message": "The supplied vacancy names no seniority level.",
                    }
                ]
            )
        ]
    )
    result = run_stage8(workspace, fake, ids)

    assert result.job_description_id == "job_description_vera_0001"
    assert result.title == "Agent Engineer"
    assert result.company == "Vera Example Systems"
    assert result.warnings[0].type == "vera_note"
    assert result.requirement_ids == (
        "jd_requirement_vera_0001",
        "jd_requirement_vera_0002",
    )

    with read_database(workspace) as connection:
        stored = list_job_descriptions(connection)
        run = connection.execute(
            "SELECT stage, status, output_ids_json FROM processing_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        calls = connection.execute(
            "SELECT status, input_hash, output_hash FROM llm_calls WHERE run_id = ?",
            (result.run_id,),
        ).fetchall()
    assert len(stored) == 1
    job_description = stored[0]
    assert job_description.raw_text == VACANCY
    assert [item.kind for item in job_description.parsed.requirements] == [
        "required_skill",
        "preferred_skill",
    ]
    assert job_description.parsed.domain_signals == ["LLM systems"]
    # §11.13: an ID is neither a list position nor authored prose.
    for index, requirement in enumerate(job_description.parsed.requirements):
        assert requirement.id != str(index)
        assert requirement.id not in requirement.text
    assert tuple(run)[:2] == ("13.8", "completed")
    assert json.loads(run["output_ids_json"]) == [result.job_description_id]
    # §12.15: Stage 8 is exactly one call.
    assert len(calls) == 1
    assert calls[0]["status"] == "completed"
    assert calls[0]["input_hash"] and calls[0]["output_hash"]


@pytest.mark.lifecycle
def test_requirement_ids_stay_unique_across_job_descriptions(
    workspace: Path,
) -> None:
    """§11.13/§12 rule 10: requirement identity is global, not per record."""

    ids = ParserIds()
    run_stage8(workspace, FakeContractRunner([parser_response()]), ids)
    run_stage8(workspace, FakeContractRunner([parser_response()]), ids)

    with read_database(workspace) as connection:
        stored = list_job_descriptions(connection)
    assert len(stored) == 2
    requirement_ids = [
        requirement.id
        for job_description in stored
        for requirement in job_description.parsed.requirements
    ]
    assert len(requirement_ids) == len(set(requirement_ids)) == 4


@pytest.mark.lifecycle
def test_a_retained_requirement_id_is_never_reallocated(workspace: Path) -> None:
    """§12 rule 10: the candidate is checked against every retained record."""

    run_stage8(workspace, FakeContractRunner([parser_response()]), ParserIds())

    class CollidingIds(ParserIds):
        def __call__(self, kind: str) -> str:
            if kind == "jd_requirement":
                return "jd_requirement_vera_0001"
            return f"{super().__call__(kind)}_retry"

    fake = FakeContractRunner([parser_response()])
    with pytest.raises(LLMInvocationError) as failure:
        run_stage8(workspace, fake, CollidingIds())

    assert failure.value.failure_code == "deterministic_enrichment_failed"
    # §13.8/§15.1: the allocation failure aborts without another parser call.
    assert len(fake.calls) == 1
    with read_database(workspace) as connection:
        assert len(list_job_descriptions(connection)) == 1
        statuses = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE stage = '13.8' "
            "ORDER BY started_at"
        ).fetchall()
    assert [tuple(row) for row in statuses] == [
        ("completed", None),
        ("failed", "deterministic_enrichment_failed"),
    ]


@pytest.mark.lifecycle
def test_a_final_model_failure_aborts_without_another_call(workspace: Path) -> None:
    """§13.8: the final ParsedJD must validate before anything persists."""

    class BlankRequirementIds(ParserIds):
        def __call__(self, kind: str) -> str:
            return "" if kind == "jd_requirement" else super().__call__(kind)


    fake = FakeContractRunner([parser_response()])
    with pytest.raises(LLMInvocationError) as failure:
        run_stage8(workspace, fake, BlankRequirementIds())

    assert failure.value.failure_code == "deterministic_enrichment_failed"
    assert len(fake.calls) == 1
    with read_database(workspace) as connection:
        assert list_job_descriptions(connection) == ()
        run = connection.execute(
            "SELECT status, failure_code FROM processing_runs WHERE stage = '13.8'"
        ).fetchone()
    assert tuple(run) == ("failed", "deterministic_enrichment_failed")


@pytest.mark.lifecycle
def test_the_faithful_demand_wording_persists_unrewritten(workspace: Path) -> None:
    """§21.25/§16.12: demand wording characterizes the vacancy, not the owner."""

    demanding = (
        "You must have expert-level production operations experience running "
        "high-volume services."
    )
    result = run_stage8(
        workspace,
        FakeContractRunner(
            [
                parser_response(
                    title=None,
                    company=None,
                    requirements=[
                        {
                            "kind": "required_skill",
                            "text": "Expert-level production operations experience",
                            "keywords": ["production operations"],
                        }
                    ],
                )
            ]
        ),
        ParserIds(),
        raw_text=demanding,
    )

    with read_database(workspace) as connection:
        stored = list_job_descriptions(connection)[0]
    assert result.title is None and result.company is None
    assert stored.parsed.requirements[0].text == (
        "Expert-level production operations experience"
    )
    assert stored.raw_text == demanding


@pytest.mark.lifecycle
def test_a_persisted_job_description_is_immutable(workspace: Path) -> None:
    """§11.11: retained context has no lifecycle transition to update."""

    result = run_stage8(workspace, FakeContractRunner([parser_response()]), ParserIds())
    with writer_database(workspace) as connection:
        with pytest.raises(Exception, match="job_description_immutable"):
            connection.execute(
                "UPDATE job_descriptions SET title = 'Rewritten' WHERE id = ?",
                (result.job_description_id,),
            )

@pytest.mark.lifecycle
def test_an_invalid_kind_retries_once_then_fails_without_persistence(
    workspace: Path,
) -> None:
    """§15.1/§21.24: at most one schema retry, and no untyped parse persists."""

    payload = json.loads(parser_response())
    payload["parsed"]["requirements"][0]["kind"] = "nice_to_have"
    invalid = json.dumps(payload, separators=(",", ":")).encode()
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError) as failure:
        run_stage8(workspace, fake, ParserIds())

    assert failure.value.failure_code == "response_validation_failed"
    assert len(fake.calls) == 2 and fake.calls[1].validation_errors is not None
    with read_database(workspace) as connection:
        assert list_job_descriptions(connection) == ()
        assert connection.execute(
            "SELECT COUNT(*) FROM job_descriptions"
        ).fetchone()[0] == 0


@pytest.mark.lifecycle
def test_a_colliding_requirement_id_is_reallocated_locally(workspace: Path) -> None:
    """§21.24: the service reallocates when safe instead of calling the parser."""

    run_stage8(workspace, FakeContractRunner([parser_response()]), ParserIds())

    class CollidesOnce(ParserIds):
        def __init__(self) -> None:
            super().__init__()
            self.collided = False

        def __call__(self, kind: str) -> str:
            if kind == "jd_requirement" and not self.collided:
                self.collided = True
                return "jd_requirement_vera_0001"
            return f"{super().__call__(kind)}_second"

    fake = FakeContractRunner([parser_response()])
    result = run_stage8(workspace, fake, CollidesOnce())

    assert len(fake.calls) == 1
    assert "jd_requirement_vera_0001" not in result.requirement_ids
    with read_database(workspace) as connection:
        stored = list_job_descriptions(connection)
    assert len(stored) == 2
    requirement_ids = [
        requirement.id
        for job_description in stored
        for requirement in job_description.parsed.requirements
    ]
    assert len(requirement_ids) == len(set(requirement_ids)) == 4
