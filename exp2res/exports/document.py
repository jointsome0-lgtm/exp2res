"""The shared §17 section model both report members render from.

`report.md` and `report.html` are two emissions of the structure built here,
produced in one export transaction. Section selection, ordering, and value
choice therefore exist once: a renderer may only decide how a segment is
spelled in its own syntax, never which segments exist. Neither member is
rendered from the other's bytes, so the HTML page inherits no Markdown escape.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, Union

from exp2res.domain.models import SelfClaim
from exp2res.errors import IntegrityFailureError

from .graph import AssessmentExportGraph, id_key


HEADINGS = (
    "1. Summary",
    "2. Strongly Supported Facts",
    "3. Recurring Patterns and Interests",
    "4. Current Strengths",
    "5. Weakly Supported Strengths",
    "6. Gaps",
    "7. Contradictions",
    "8. Risks / Failure Modes",
    "9. Unknowns and Open Questions",
)

TITLE = "Self-Assessment Snapshot"

ValueStyle = Literal["prose", "token", "status"]


@dataclass(frozen=True)
class Key:
    """A renderer-owned field label. Never carries a stored value."""

    text: str
    emphasized: bool = True


@dataclass(frozen=True)
class Lit:
    """Renderer-owned text joining values inside one line."""

    text: str


@dataclass(frozen=True)
class Val:
    """One nonliteral value; each renderer escapes it for its own syntax.

    ``style`` is presentation only. It selects how a renderer may typeset the
    value — never whether the value renders, in which order, or under which
    heading — so the members keep identical content.
    """

    text: str
    style: ValueStyle = "prose"


Segment = Union[Key, Lit, Val]
Line = tuple[Segment, ...]


@dataclass(frozen=True)
class Block:
    """One list item: a lead line, its field lines, and nested items."""

    lead: Line
    fields: tuple[Line, ...] = ()
    children: tuple["Block", ...] = ()


@dataclass(frozen=True)
class Section:
    heading: str
    blocks: tuple[Block, ...] = ()


@dataclass(frozen=True)
class ReportDocument:
    title: str
    header: tuple[Line, ...]
    sections: tuple[Section, ...] = ()


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
    if claim.dimension in {
        "domain_interest",
        "working_style",
        "trajectory",
        "identity_hypothesis",
    }:
        return 3
    if claim.verification_status == "supported":
        return 4
    if claim.verification_status in {
        "partially_supported",
        "inferred_but_acceptable",
    }:
        return 5
    raise IntegrityFailureError("assessment_claim_section_invalid")


def _id_segments(values) -> list[Segment]:
    segments: list[Segment] = []
    for position, value in enumerate(sorted(values, key=id_key)):
        if position:
            segments.append(Lit(", "))
        segments.append(Val(value, "token"))
    return segments


def _sources_line(claim: SelfClaim) -> Line:
    # §17: the claim-level trail is the report's inline provenance; the
    # record-level closure stays in the evidence_map.json companion. Each
    # member the claim marks contrary carries the fixed ` (counter)` suffix,
    # the durable trace of the discarded §15.4 patterns' counter facts.
    counter = set(claim.counter_fact_ids)
    segments: list[Segment] = [Key("Sources")]
    for position, fact_id in enumerate(sorted(claim.source_fact_ids, key=id_key)):
        if position:
            segments.append(Lit(", "))
        segments.append(Val(fact_id, "token"))
        if fact_id in counter:
            segments.append(Lit(" (counter)"))
    if not claim.source_fact_ids:
        raise IntegrityFailureError("claim_sources_trail_empty")
    return tuple(segments)


def _counterevidence_children(claim: SelfClaim) -> tuple[Block, ...]:
    children: list[Block] = []
    for item in sorted(
        claim.counterevidence,
        key=lambda item: (
            id_key(item.source_ref_type),
            id_key(item.source_ref_id),
        ),
    ):
        children.append(
            Block(
                lead=(
                    Key("Verifier-grounded contrary evidence"),
                    Val(item.statement),
                ),
                fields=(
                    (
                        Key("Source"),
                        Val(item.source_ref_type, "token"),
                        Lit(" "),
                        Val(item.source_ref_id, "token"),
                    ),
                ),
            )
        )
    return tuple(children)


def _claim_block(claim: SelfClaim) -> Block:
    fields: list[Line] = [
        (Key("Claim ID"), Val(claim.id, "token")),
        (Key("Status"), Val(claim.verification_status, "status")),
    ]
    if claim.uncertainty is not None:
        fields.append((Key("Uncertainty"), Val(claim.uncertainty)))
    fields.append(_sources_line(claim))
    return Block(
        lead=(Val(claim.claim),),
        fields=tuple(fields),
        children=_counterevidence_children(claim),
    )


def _strong_fact_blocks(graph: AssessmentExportGraph) -> tuple[Block, ...]:
    facts = {item.value.id: item.value for item in graph.facts}
    supporting: dict[str, set[str]] = defaultdict(set)
    for stored in graph.claims:
        claim = stored.value
        if claim.verification_status != "supported":
            continue
        # §17: only a supporting membership reaches this section, so a fact
        # the claim marks contrary never renders as one of its strengths.
        reached = set(claim.source_fact_ids) - set(claim.counter_fact_ids)
        for fact_id in reached:
            if facts[fact_id].confidence == "high":
                supporting[fact_id].add(claim.id)

    blocks: list[Block] = []
    for fact_id in sorted(supporting, key=id_key):
        fact = facts[fact_id]
        blocks.append(
            Block(
                lead=(Val(fact.claim),),
                fields=(
                    (Key("Fact ID"), Val(fact.id, "token")),
                    (
                        Key("Supporting claim IDs"),
                        *_id_segments(supporting[fact_id]),
                    ),
                    (Key("Raw log IDs"), *_id_segments(fact.source_log_ids)),
                ),
            )
        )
    return tuple(blocks)


def _gap_blocks(graph: AssessmentExportGraph) -> tuple[Block, ...]:
    # §17: the question renders first, beside the gap ID that §14.7
    # `gaps answer` needs; unanswered blocks form the open-question set.
    blocks: list[Block] = []
    for stored in graph.gaps:
        gap = stored.value
        fields: list[Line] = [
            (Key("Gap ID"), Val(gap.id, "token")),
            (
                Key("Target"),
                Val(gap.target_type, "token"),
                Lit(" "),
                Val(gap.target_id, "token"),
            ),
            (Key("Reason"), Val(gap.reason)),
            (Key("Priority"), Val(gap.priority, "token")),
        ]
        if gap.answered:
            fields.append((Key("Answered since synthesis"), Lit("yes")))
        blocks.append(Block(lead=(Val(gap.question),), fields=tuple(fields)))
    return tuple(blocks)


def _contradiction_blocks(graph: AssessmentExportGraph) -> tuple[Block, ...]:
    # §17: the fixed origin label states §13.4's rule — a detection is Stage
    # 4's reading of its inputs and no Stage 7 verdict retires or adjudicates
    # it, so the row is never read as a verified conclusion.
    blocks: list[Block] = []
    for stored in graph.contradictions:
        contradiction = stored.value
        blocks.append(
            Block(
                lead=(Val(contradiction.title),),
                fields=(
                    (Key("Origin"), Lit("unadjudicated detector output")),
                    (Key("Description"), Val(contradiction.description)),
                    (
                        Key("Left reference"),
                        Val(contradiction.left_ref_type, "token"),
                        Lit(" "),
                        Val(contradiction.left_ref_id, "token"),
                    ),
                    (
                        Key("Right reference"),
                        Val(contradiction.right_ref_type, "token"),
                        Lit(" "),
                        Val(contradiction.right_ref_id, "token"),
                    ),
                ),
            )
        )
    return tuple(blocks)


def build_assessment_document(graph: AssessmentExportGraph) -> ReportDocument:
    """Project one loaded export graph into the deterministic §17 document."""

    snapshot = graph.snapshot.value
    blocks: dict[int, list[Block]] = {number: [] for number in range(1, 10)}
    for stored in graph.claims:
        claim = stored.value
        blocks[claim_section(claim)].append(_claim_block(claim))
    blocks[2] = list(_strong_fact_blocks(graph))
    blocks[7].extend(_contradiction_blocks(graph))
    blocks[9].extend(_gap_blocks(graph))

    return ReportDocument(
        title=TITLE,
        header=(
            (
                Key("Snapshot created", emphasized=False),
                Val(graph.snapshot_created_at_text, "token"),
            ),
            (Key("Scope", emphasized=False), Val(snapshot.scope, "token")),
        ),
        sections=tuple(
            Section(heading=heading, blocks=tuple(blocks[number]))
            for number, heading in enumerate(HEADINGS, start=1)
        ),
    )
