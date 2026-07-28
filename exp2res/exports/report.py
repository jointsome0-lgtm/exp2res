"""Deterministic §17 assessment ``report.md`` renderer."""

from __future__ import annotations

from collections import defaultdict

from exp2res.domain.models import SelfClaim, SelfSignal
from exp2res.errors import IntegrityFailureError

from .graph import AssessmentExportGraph, id_key
from .markdown import escape_generated


_HEADINGS = (
    "## 1. Summary",
    "## 2. Strongly Supported Facts",
    "## 3. Recurring Signals",
    "## 4. Current Strengths",
    "## 5. Weakly Supported Strengths",
    "## 6. Gaps",
    "## 7. Contradictions",
    "## 8. Risks / Failure Modes",
    "## 9. Unknowns and Open Questions",
    "## 10. Counterevidence",
)


def claim_section(claim: SelfClaim) -> int:
    if claim.claim_kind == "narrative_summary":
        return 1
    if claim.dimension == "gap":
        return 6
    if claim.dimension in {"risk", "constraint"}:
        return 8
    if claim.verification_status == "contradicted":
        return 7
    if claim.verification_status == "needs_clarification":
        return 9
    if claim.claim_kind == "pattern_signal":
        return 3
    if claim.verification_status == "supported":
        return 4
    if claim.verification_status in {
        "partially_supported",
        "inferred_but_acceptable",
    }:
        return 5
    raise IntegrityFailureError("assessment_claim_section_invalid")


def _id_list(values) -> str:
    return ", ".join(escape_generated(value) for value in sorted(values, key=id_key))


def _sources_line(claim: SelfClaim, signals_by_id: dict[str, SelfSignal]) -> str:
    # §17: the claim-level trail is the report's inline provenance; the
    # record-level closure stays in the evidence_map.json companion.
    entries: list[str] = []
    for signal_id in sorted(claim.source_signal_ids, key=id_key):
        signal = signals_by_id[signal_id]
        parts: list[str] = []
        if signal.supporting_fact_ids:
            parts.append(f"facts {_id_list(signal.supporting_fact_ids)}")
        if signal.counter_fact_ids:
            parts.append(f"counter {_id_list(signal.counter_fact_ids)}")
        entries.append(f"{escape_generated(signal_id)} ({'; '.join(parts)})")
    if claim.source_fact_ids:
        entries.append(f"facts {_id_list(claim.source_fact_ids)}")
    if not entries:
        raise IntegrityFailureError("claim_sources_trail_empty")
    return "  **Sources:** " + "; ".join(entries)


def _claim_block(
    claim: SelfClaim, signals_by_id: dict[str, SelfSignal]
) -> list[str]:
    lines = [f"- {escape_generated(claim.claim, continuation_indent='  ')}"]
    lines.append(
        f"  **Status:** {escape_generated(claim.verification_status, continuation_indent='  ')}"
    )
    if claim.uncertainty is not None:
        lines.append(
            f"  **Uncertainty:** {escape_generated(claim.uncertainty, continuation_indent='  ')}"
        )
    lines.append(_sources_line(claim, signals_by_id))
    return lines


def _strong_fact_rows(graph: AssessmentExportGraph) -> list[str]:
    facts = {item.value.id: item.value for item in graph.facts}
    signals = {item.value.id: item.value for item in graph.signals}
    supporting: dict[str, set[str]] = defaultdict(set)
    for stored in graph.claims:
        claim = stored.value
        if claim.verification_status != "supported":
            continue
        reached = set(claim.source_fact_ids)
        for signal_id in claim.source_signal_ids:
            signal = signals[signal_id]
            reached.update(signal.supporting_fact_ids)
            reached.update(signal.counter_fact_ids)
        for fact_id in reached:
            if facts[fact_id].confidence == "high":
                supporting[fact_id].add(claim.id)

    lines: list[str] = []
    for fact_id in sorted(supporting, key=id_key):
        fact = facts[fact_id]
        claim_ids = ", ".join(
            escape_generated(value) for value in sorted(supporting[fact_id], key=id_key)
        )
        lines.append(
            f"- {escape_generated(fact.claim, continuation_indent='  ')}"
        )
        lines.append(f"  **Fact ID:** {escape_generated(fact.id)}")
        lines.append(f"  **Supporting claim IDs:** {claim_ids}")
        lines.append(f"  **Raw log IDs:** {_id_list(fact.source_log_ids)}")
    return lines


def _gap_rows(graph: AssessmentExportGraph) -> list[str]:
    # §17: the question renders first, beside the gap ID that §14.7
    # `gaps answer` needs; unanswered blocks form the open-question set.
    lines: list[str] = []
    for stored in graph.gaps:
        gap = stored.value
        lines.append(f"- {escape_generated(gap.question, continuation_indent='  ')}")
        lines.append(f"  **Gap ID:** {escape_generated(gap.id)}")
        lines.append(
            "  **Target:** "
            f"{escape_generated(gap.target_type)} {escape_generated(gap.target_id)}"
        )
        lines.append(f"  **Reason:** {escape_generated(gap.reason)}")
        lines.append(f"  **Priority:** {escape_generated(gap.priority)}")
        if gap.answered:
            lines.append("  **Answered since synthesis:** yes")
    return lines


def _contradiction_rows(graph: AssessmentExportGraph) -> list[str]:
    lines: list[str] = []
    for stored in graph.contradictions:
        contradiction = stored.value
        lines.append(
            f"- {escape_generated(contradiction.title, continuation_indent='  ')}"
        )
        lines.append(
            "  **Description:** "
            + escape_generated(contradiction.description, continuation_indent="  ")
        )
        lines.append(
            "  **Left reference:** "
            f"{escape_generated(contradiction.left_ref_type)} "
            f"{escape_generated(contradiction.left_ref_id)}"
        )
        lines.append(
            "  **Right reference:** "
            f"{escape_generated(contradiction.right_ref_type)} "
            f"{escape_generated(contradiction.right_ref_id)}"
        )
    return lines


def _counterevidence_rows(graph: AssessmentExportGraph) -> list[str]:
    lines: list[str] = []
    for stored in graph.claims:
        claim = stored.value
        if not claim.counterevidence:
            continue
        lines.append(f"- **Claim:** {escape_generated(claim.id)}")
        lines.append(f"  **Status:** {escape_generated(claim.verification_status)}")
        for item in sorted(
            claim.counterevidence,
            key=lambda item: (
                id_key(item.source_ref_type),
                id_key(item.source_ref_id),
            ),
        ):
            lines.append(
                "  - **Verifier-grounded contrary evidence:** "
                + escape_generated(item.statement, continuation_indent="    ")
            )
            lines.append(
                "    **Source:** "
                f"{escape_generated(item.source_ref_type)} "
                f"{escape_generated(item.source_ref_id)}"
            )
    return lines


def render_report(graph: AssessmentExportGraph) -> bytes:
    snapshot = graph.snapshot.value
    signals_by_id = {item.value.id: item.value for item in graph.signals}
    sections: dict[int, list[str]] = {number: [] for number in range(1, 11)}
    for stored in graph.claims:
        claim = stored.value
        sections[claim_section(claim)].extend(_claim_block(claim, signals_by_id))
    sections[2] = _strong_fact_rows(graph)
    sections[7].extend(_contradiction_rows(graph))
    sections[9].extend(_gap_rows(graph))
    sections[10] = _counterevidence_rows(graph)

    lines = [
        "# Self-Assessment Snapshot",
        "",
        f"Snapshot created: {escape_generated(graph.snapshot_created_at_text)}",
        f"Scope: {escape_generated(snapshot.scope)}",
    ]
    if snapshot.scope_target is not None:
        lines.append(f"Scope target: {escape_generated(snapshot.scope_target)}")
    lines.append("")
    for number, heading in enumerate(_HEADINGS, start=1):
        lines.append(heading)
        lines.extend(sections[number])
        lines.append("")
    return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")

