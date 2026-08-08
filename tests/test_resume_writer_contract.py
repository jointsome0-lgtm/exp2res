"""§15.6 resume-writer contract boundary.

Stage 10 assembles this input itself, so these tests hold the boundary the
assembly crosses: what a cost-bearing provider call is allowed to carry, and
what the model is allowed to send back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from exp2res.domain.models import (
    EvidenceItem,
    ExperienceFact,
    JDRequirement,
    OccurredAt,
    ParsedJD,
    RawLog,
    SelfClaim,
)
from exp2res.llm.resume_writer import (
    BranchContext,
    FactEvidence,
    JobDescriptionContext,
    ResumeWriterInput,
    ResumeWriterOutput,
    SelectedFact,
)


pytestmark = pytest.mark.contract


WRITER_TIME = datetime(2026, 7, 20, 10, 0, tzinfo=timezone(timedelta(hours=2)))
SNAPSHOT_ID = "snapshot_vera_writer_0001"


def selected_fact(suffix: str) -> SelectedFact:
    raw = RawLog(
        id=f"log_vera_writer_{suffix}",
        recorded_at=WRITER_TIME,
        entry_type="manual_daily",
        source_type="manual_entry",
        occurred=OccurredAt(
            start=WRITER_TIME,
            end=None,
            precision="exact_day",
            confidence="high",
        ),
        raw_text="Vera Example source voice.",
    )
    evidence = EvidenceItem(
        id=f"evidence_vera_writer_{suffix}",
        created_at=WRITER_TIME,
        raw_log_id=raw.id,
        summary="Vera Example synthetic evidence summary.",
        strength="manual_claim",
    )
    fact = ExperienceFact(
        id=f"fact_vera_writer_{suffix}",
        created_at=WRITER_TIME,
        claim="Built a deterministic renderer.",
        claim_kind="observed_fact",
        context="independent_project",
        ownership_level="built",
        occurred=raw.occurred,
        source_log_ids=[raw.id],
        evidence_item_ids=[evidence.id],
        confidence="high",
    )
    return SelectedFact(
        fact=fact,
        evidence=[FactEvidence(evidence_item=evidence, raw_log=raw)],
    )


def writer_input(facts: list[SelectedFact], **overrides) -> ResumeWriterInput:
    values = {
        "branch": BranchContext(
            name="agent-engineer",
            job_description_id="jd_vera_writer_0001",
            assessment_snapshot_id=SNAPSHOT_ID,
            assessment_scope="global",
        ),
        "job_description": JobDescriptionContext(
            id="jd_vera_writer_0001",
            title="Agent Engineer",
            company="Example Co",
            parsed=ParsedJD(
                requirements=[
                    JDRequirement(
                        id="jdreq_vera_writer_0001",
                        kind="required_skill",
                        text="Build evidence-grounded LLM workflows.",
                        keywords=["provenance"],
                    )
                ],
            ),
        ),
        "selected_facts": facts,
        "supported_self_claims": [],
    }
    values.update(overrides)
    return ResumeWriterInput(**values)


def test_the_whole_pack_carries_each_fact_once() -> None:
    """§13.10 submits the exact current set, so a repeat is an assembly defect.

    A repeated row is still equal to its ID-sorted form, so ordering alone
    never catches it — and on the wire it would weight one fact's evidence
    twice while paying for it twice.
    """

    fact = selected_fact("0001")
    assert writer_input([fact]).selected_facts == [fact]

    with pytest.raises(ValidationError, match="duplicate selected fact"):
        writer_input([fact, fact])


def test_a_branch_name_that_can_never_be_persisted_never_reaches_the_wire() -> None:
    """§14.10's non-blank rule, held at the provider boundary too."""

    with pytest.raises(ValidationError, match="blank branch name"):
        BranchContext(
            name="   ",
            job_description_id="jd_vera_writer_0001",
            assessment_snapshot_id=SNAPSHOT_ID,
            assessment_scope="global",
        )


def test_a_claim_guides_generation_only_from_the_branch_snapshot() -> None:
    """§13.10: only supported current members of the anchored snapshot."""

    def claim(**overrides) -> SelfClaim:
        values = {
            "id": "claim_vera_writer_0001",
            "created_at": WRITER_TIME,
            "snapshot_id": SNAPSHOT_ID,
            "claim": "You reach for determinism before scale.",
            "claim_kind": "pattern_signal",
            "dimension": "working_style",
            "source_fact_ids": ["fact_vera_writer_0001"],
            "confidence": "high",
            "verification_status": "supported",
        }
        values.update(overrides)
        return SelfClaim(**values)

    facts = [selected_fact("0001")]
    assert writer_input(facts, supported_self_claims=[claim()]).supported_self_claims

    with pytest.raises(ValidationError, match="self claim is not supported"):
        writer_input(facts, supported_self_claims=[claim(verification_status="unsupported")])

    with pytest.raises(ValidationError, match="self claim is outside the branch snapshot"):
        writer_input(facts, supported_self_claims=[claim(snapshot_id="snapshot_vera_writer_0002")])


def test_an_empty_bullet_array_is_a_valid_writer_response() -> None:
    """§15.6: no-bullet is the honest answer Stage 10 turns into class 10."""

    assert ResumeWriterOutput(bullets=[], warnings=[]).bullets == []
