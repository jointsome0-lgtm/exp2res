"""Synthetic Vera Example graph builders for offline export tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from exp2res.domain.models import (
    AssessmentSnapshot,
    Contradiction,
    CounterevidenceItem,
    EvidenceItem,
    ExperienceFact,
    GapQuestion,
    OccurredAt,
    RawLog,
    SelfClaim,
)
from exp2res.exports.graph import (
    AssessmentExportGraph,
    FactSourceRecord,
    StoredRecord,
)


EXPORT_TIME = datetime(2026, 7, 20, 10, 0, tzinfo=timezone(timedelta(hours=2)))


def stored(value, prefix: str) -> StoredRecord:
    return StoredRecord(
        value=value,
        generation_id=f"generation_vera_{prefix}",
        produced_by_run_id=f"run_vera_{prefix}",
    )


def assessment_graph(
    *,
    answered: bool = False,
    snapshot_id: str = "snapshot_vera_export_0001",
    all_sections: bool = True,
) -> AssessmentExportGraph:
    raw = RawLog(
        id="log_vera_export_0001",
        recorded_at=EXPORT_TIME,
        entry_type="manual_daily",
        source_type="manual_entry",
        occurred=OccurredAt(
            start=EXPORT_TIME,
            end=None,
            precision="exact_day",
            confidence="high",
        ),
        raw_text="Vera Example private source voice.",
    )
    evidence = EvidenceItem(
        id="evidence_vera_export_0001",
        created_at=EXPORT_TIME,
        raw_log_id=raw.id,
        summary="Vera Example synthetic evidence summary.",
        strength="manual_claim",
    )
    fact = ExperienceFact(
        id="fact_vera_export_0001",
        created_at=EXPORT_TIME,
        claim="Built a deterministic renderer.",
        claim_kind="observed_fact",
        context="independent_project",
        ownership_level="built",
        occurred=raw.occurred,
        source_log_ids=[raw.id],
        evidence_item_ids=[evidence.id],
        confidence="high",
    )
    # §11.6/§15.4: the contrary member of a pattern claim's closure. It is an
    # ordinary fact — only the citing claim's `counter_fact_ids` marks it —
    # so the graph carries it with its own evidence like any other source.
    counter_evidence = EvidenceItem(
        id="evidence_vera_export_0002",
        created_at=EXPORT_TIME,
        raw_log_id=raw.id,
        summary="Vera Example synthetic contrary evidence summary.",
        strength="manual_claim",
    )
    counter_fact = ExperienceFact(
        id="fact_vera_export_0002",
        created_at=EXPORT_TIME,
        claim="Shipped one release without a deterministic check.",
        claim_kind="observed_fact",
        context="independent_project",
        ownership_level="built",
        occurred=raw.occurred,
        source_log_ids=[raw.id],
        evidence_item_ids=[counter_evidence.id],
        confidence="high",
    )
    gap = GapQuestion(
        id="gap_vera_export_0001",
        created_at=EXPORT_TIME,
        target_type="experience_fact",
        target_id=fact.id,
        question="What scale did you validate?",
        reason="missing_scale",
        priority="high",
        answered=answered,
        answer_log_id=raw.id if answered else None,
    )
    contradiction = Contradiction(
        id="contradiction_vera_export_0001",
        created_at=EXPORT_TIME,
        title="Scale evidence conflicts.",
        description="One synthetic source supports a prototype; another does not support scale.",
        left_ref_type="experience_fact",
        left_ref_id=fact.id,
        right_ref_type="raw_log",
        right_ref_id=raw.id,
    )

    claim_specs = [
        (
            "claim_vera_summary_0001",
            "Current evidence suggests you deliver deterministic local tools.",
            "narrative_summary",
            "trajectory",
            "supported",
        )
    ]
    if all_sections:
        claim_specs.extend(
            [
                ("claim_vera_signal_0001", "You repeat the pattern.", "pattern_signal", "working_style", "supported"),
                ("claim_vera_strength_0001", "You have a current strength.", "hypothesis", "technical_skill", "supported"),
                ("claim_vera_weak_0001", "You have a bounded strength.", "hypothesis", "technical_skill", "partially_supported"),
                ("claim_vera_gap_0001", "The supplied evidence leaves a gap.", "hypothesis", "gap", "needs_clarification"),
                ("claim_vera_contradicted_0001", "You validated the scale in question.", "hypothesis", "technical_skill", "contradicted"),
                ("claim_vera_risk_0001", "You carry a delivery risk.", "hypothesis", "risk", "supported"),
                ("claim_vera_unknown_0001", "The validated scale remains uncertain.", "hypothesis", "technical_skill", "needs_clarification"),
            ]
        )
    claims: list[StoredRecord[SelfClaim]] = []
    for claim_id, prose, kind, dimension, status in claim_specs:
        counterevidence = []
        if status == "contradicted":
            counterevidence = [
                CounterevidenceItem(
                    statement="The supplied source evidence supports only a prototype.",
                    source_ref_type="experience_fact",
                    source_ref_id=fact.id,
                )
            ]
        pattern_claim = kind == "pattern_signal"
        claim = SelfClaim(
            id=claim_id,
            created_at=EXPORT_TIME,
            snapshot_id=snapshot_id,
            claim=prose,
            claim_kind=kind,
            dimension=dimension,
            source_fact_ids=(
                [fact.id, counter_fact.id] if pattern_claim else [fact.id]
            ),
            counter_fact_ids=[counter_fact.id] if pattern_claim else [],
            confidence="high",
            verification_status=status,
            counterevidence=counterevidence,
            uncertainty=(
                "Evidence remains limited to the supplied records."
                if status == "needs_clarification"
                else None
            ),
        )
        claims.append(stored(claim, claim_id))
    claims.sort(key=lambda item: item.value.id.encode("utf-8"))
    summary = next(
        item.value.claim
        for item in claims
        if item.value.claim_kind == "narrative_summary"
    )
    snapshot = AssessmentSnapshot(
        id=snapshot_id,
        created_at=EXPORT_TIME,
        scope="global",
        title="Self-Assessment — Global",
        summary=summary,
        gap_question_ids=[gap.id],
        contradiction_ids=[contradiction.id],
        verification_status="contradicted" if all_sections else "supported",
    )
    # §13.12: the graph carries exactly the facts the claims reach, the way
    # §13.6 selection builds it — the contrary member enters only with the
    # pattern claim that cites it, never as an unused closure row.
    cited_counter = any(
        counter_fact.id in item.value.source_fact_ids for item in claims
    )
    return AssessmentExportGraph(
        snapshot=stored(snapshot, "snapshot"),
        snapshot_created_at_text="2026-07-20T10:00:00+02:00",
        claims=tuple(claims),
        facts=(
            (stored(fact, "fact"), stored(counter_fact, "counter_fact"))
            if cited_counter
            else (stored(fact, "fact"),)
        ),
        evidence_items=(evidence, counter_evidence) if cited_counter else (evidence,),
        raw_logs=(raw,),
        gaps=(stored(gap, "gap"),),
        contradictions=(stored(contradiction, "contradiction"),),
        fact_sources=(
            FactSourceRecord(
                fact_id=fact.id,
                evidence_item_id=evidence.id,
                support_type="direct",
            ),
        )
        + (
            (
                FactSourceRecord(
                    fact_id=counter_fact.id,
                    evidence_item_id=counter_evidence.id,
                    support_type="direct",
                ),
            )
            if cited_counter
            else ()
        ),
    )


def graph_with_gap_answered(graph: AssessmentExportGraph, answered: bool) -> AssessmentExportGraph:
    gap = graph.gaps[0]
    updated = gap.value.model_copy(
        update={
            "answered": answered,
            "answer_log_id": graph.raw_logs[0].id if answered else None,
        }
    )
    return replace(graph, gaps=(replace(gap, value=updated),))


def graph_with_gap_answered_after_export(
    graph: AssessmentExportGraph,
) -> AssessmentExportGraph:
    """Answer the listed gap with a new §14.7 log outside the claim closure."""

    gap = graph.gaps[0]
    answer = RawLog(
        id="log_vera_export_answer_0001",
        recorded_at=EXPORT_TIME + timedelta(hours=3),
        entry_type="gap_answer",
        source_type="manual_entry",
        occurred=OccurredAt(
            start=None,
            end=None,
            precision="unknown",
            confidence="unknown",
        ),
        raw_text="Vera Example answered the scale question.",
        metadata={
            "question_text": gap.value.question,
            "question_reason": gap.value.reason,
        },
    )
    updated = gap.value.model_copy(
        update={"answered": True, "answer_log_id": answer.id}
    )
    return replace(
        graph,
        gaps=(replace(gap, value=updated),),
        supplemental_raw_logs=(*graph.supplemental_raw_logs, answer),
    )
