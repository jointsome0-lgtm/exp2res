"""Closed §13.12 assessment JSON companion documents."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, field_validator, model_validator

from exp2res.domain.canonical import canonical_json_bytes
from exp2res.domain.enums import (
    AssessmentScope,
    ClaimKind,
    Confidence,
    CounterevidenceRefType,
    DetectionRefType,
    GapPriority,
    GapTrigger,
    ResumeTargetSection,
    SelfClaimDimension,
    VerificationStatus,
)
from exp2res.domain.models import (
    StrictModel,
    validate_free_text,
    validate_structural,
)

from .graph import AssessmentExportGraph, id_key
from .markdown import normalize_generated_text

if TYPE_CHECKING:  # pragma: no cover - `branch` imports this module's siblings
    from .branch import BranchExportGraph


class ExportDocument(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _require_unique(values: list[str]) -> list[str]:
    for value in values:
        validate_structural(value)
    if len(values) != len(set(values)):
        raise ValueError("duplicate typed ID")
    if values != sorted(values, key=id_key):
        raise ValueError("typed IDs are not byte ordered")
    return values


def _projected_text(value: str) -> str:
    validate_free_text(value, nonempty=True)
    if value != normalize_generated_text(value):
        raise ValueError("generated export text is not LF/NFC normalized")
    return value


class CounterevidenceExport(ExportDocument):
    statement: str
    source_ref_type: CounterevidenceRefType
    source_ref_id: str

    @field_validator("statement")
    @classmethod
    def normalized_statement(cls, value: str) -> str:
        return _projected_text(value)

    @field_validator("source_ref_id")
    @classmethod
    def structural_source(cls, value: str) -> str:
        return validate_structural(value)


class GapExport(ExportDocument):
    id: str
    target_type: DetectionRefType
    target_id: str
    question: str
    reason: GapTrigger
    priority: GapPriority
    answered: bool

    @field_validator("id", "target_id")
    @classmethod
    def structural_ids(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("question")
    @classmethod
    def normalized_question(cls, value: str) -> str:
        return _projected_text(value)


class ContradictionExport(ExportDocument):
    id: str
    title: str
    description: str
    left_ref_type: DetectionRefType
    left_ref_id: str
    right_ref_type: DetectionRefType
    right_ref_id: str

    @field_validator("id", "left_ref_id", "right_ref_id")
    @classmethod
    def structural_ids(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("title", "description")
    @classmethod
    def normalized_text(cls, value: str) -> str:
        return _projected_text(value)


class SnapshotExport(ExportDocument):
    id: str
    created_at: datetime
    scope: AssessmentScope
    title: str
    verification_status: VerificationStatus

    @field_validator("id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("title")
    @classmethod
    def normalized_title(cls, value: str) -> str:
        return _projected_text(value)

    @field_validator("created_at")
    @classmethod
    def aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must carry an offset")
        return value


class SelfClaimExport(ExportDocument):
    id: str
    claim: str
    claim_kind: ClaimKind
    dimension: SelfClaimDimension
    confidence: Confidence
    verification_status: VerificationStatus
    uncertainty: str | None
    source_fact_ids: list[str]
    counter_fact_ids: list[str]
    counterevidence: list[CounterevidenceExport]

    @field_validator("id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("claim")
    @classmethod
    def normalized_claim(cls, value: str) -> str:
        return _projected_text(value)

    @field_validator("uncertainty")
    @classmethod
    def normalized_uncertainty(cls, value: str | None) -> str | None:
        return None if value is None else _projected_text(value)

    @field_validator("source_fact_ids", "counter_fact_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)

    @model_validator(mode="after")
    def counter_facts_are_sources(self) -> "SelfClaimExport":
        # §11.6: the contrary marking is a subset of the closure, so a
        # consumer reading `source_fact_ids` never misses a counter member.
        if not set(self.counter_fact_ids).issubset(self.source_fact_ids):
            raise ValueError("counter fact is not a source fact")
        return self

    @field_validator("counterevidence")
    @classmethod
    def ordered_counterevidence(
        cls, value: list[CounterevidenceExport]
    ) -> list[CounterevidenceExport]:
        keys = [(item.source_ref_type, item.source_ref_id) for item in value]
        if len(keys) != len(set(keys)) or keys != sorted(
            keys, key=lambda item: (id_key(item[0]), id_key(item[1]))
        ):
            raise ValueError("counterevidence is duplicate or unordered")
        return value


class SelfClaimsDocument(ExportDocument):
    schema_version: Literal[3]
    snapshot: SnapshotExport
    claims: list[SelfClaimExport]
    unknowns: list[GapExport]
    contradictions: list[ContradictionExport]

    @model_validator(mode="after")
    def ordered_rows(self) -> "SelfClaimsDocument":
        for rows in (self.claims, self.unknowns, self.contradictions):
            ids = [item.id for item in rows]
            if len(ids) != len(set(ids)) or ids != sorted(ids, key=id_key):
                raise ValueError("document rows are duplicate or unordered")
        return self


class ClaimLink(ExportDocument):
    claim_id: str
    source_fact_ids: list[str]
    counter_fact_ids: list[str]

    @field_validator("claim_id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("source_fact_ids", "counter_fact_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)

    @model_validator(mode="after")
    def counter_facts_are_sources(self) -> "ClaimLink":
        # §13.12: the contrary members are repeated inside the closure, so a
        # consumer never reads a counter fact as support.
        if not set(self.counter_fact_ids).issubset(self.source_fact_ids):
            raise ValueError("counter fact is not a source fact")
        return self


class FactLink(ExportDocument):
    fact_id: str
    evidence_item_ids: list[str]
    source_log_ids: list[str]

    @field_validator("fact_id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("evidence_item_ids", "source_log_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)

    @model_validator(mode="after")
    def grounded_fact(self) -> "FactLink":
        # §12: a persisted fact always selects evidence, and that evidence
        # always names its log. An empty link would satisfy every closure
        # comparison while leaving the fact it stands for ungrounded.
        if not self.evidence_item_ids or not self.source_log_ids:
            raise ValueError("fact link carries no evidence or log")
        return self


class EvidenceLink(ExportDocument):
    evidence_item_id: str
    raw_log_id: str

    @field_validator("evidence_item_id", "raw_log_id")
    @classmethod
    def structural_ids(cls, value: str) -> str:
        return validate_structural(value)


class AssessmentEvidenceMapDocument(ExportDocument):
    schema_version: Literal[3]
    output_kind: Literal["assessment"]
    entity_id: str
    rendered_claim_ids: list[str]
    claim_links: list[ClaimLink]
    fact_links: list[FactLink]
    evidence_links: list[EvidenceLink]

    @field_validator("entity_id")
    @classmethod
    def structural_entity_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("rendered_claim_ids")
    @classmethod
    def unique_rendered_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)

    @model_validator(mode="after")
    def complete_ordered_links(self) -> "AssessmentEvidenceMapDocument":
        grouped_ids = (
            [item.claim_id for item in self.claim_links],
            [item.fact_id for item in self.fact_links],
            [item.evidence_item_id for item in self.evidence_links],
        )
        for ids in grouped_ids:
            if len(ids) != len(set(ids)) or ids != sorted(ids, key=id_key):
                raise ValueError("evidence-map links are duplicate or unordered")
        if self.rendered_claim_ids != grouped_ids[0]:
            raise ValueError("rendered claim IDs disagree with claim links")
        return self


class RenderedBulletExport(ExportDocument):
    bullet_id: str
    text: str
    target_section: ResumeTargetSection
    matched_jd_requirements: list[str]
    source_self_claim_ids: list[str]
    source_fact_ids: list[str]
    source_log_ids: list[str]

    @field_validator("bullet_id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("text")
    @classmethod
    def normalized_text(cls, value: str) -> str:
        return _projected_text(value)

    @field_validator(
        "matched_jd_requirements",
        "source_self_claim_ids",
        "source_fact_ids",
        "source_log_ids",
    )
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)

    @model_validator(mode="after")
    def grounded_bullet(self) -> "RenderedBulletExport":
        # §18: a bullet with no fact or no log has no closure to render from,
        # and the export projection is where that becomes visible to a reader.
        if not self.source_fact_ids or not self.source_log_ids:
            raise ValueError("rendered bullet has no provenance")
        return self


class BulletPackEvidenceMapDocument(ExportDocument):
    schema_version: Literal[3]
    output_kind: Literal["resume"]
    entity_id: str
    rendered_bullets: list[RenderedBulletExport]
    claim_links: list[ClaimLink]
    fact_links: list[FactLink]
    evidence_links: list[EvidenceLink]

    @field_validator("entity_id")
    @classmethod
    def structural_entity_id(cls, value: str) -> str:
        return validate_structural(value)

    @model_validator(mode="after")
    def complete_ordered_links(self) -> "BulletPackEvidenceMapDocument":
        # `rendered_bullets` carries §13.10 render order, so it is checked for
        # duplicates only; the three link lists are closures and stay ordered.
        bullet_ids = [item.bullet_id for item in self.rendered_bullets]
        if not bullet_ids or len(bullet_ids) != len(set(bullet_ids)):
            raise ValueError("rendered bullets are empty or duplicated")
        for ids in (
            [item.claim_id for item in self.claim_links],
            [item.fact_id for item in self.fact_links],
            [item.evidence_item_id for item in self.evidence_links],
        ):
            if len(ids) != len(set(ids)) or ids != sorted(ids, key=id_key):
                raise ValueError("evidence-map links are duplicate or unordered")
        # §13.12: every rendered bullet round-trips through the typed links, so
        # each cited claim and each reached fact carries its own closure row.
        claim_ids = {item.claim_id for item in self.claim_links}
        fact_ids = {item.fact_id for item in self.fact_links}
        evidence_ids = {item.evidence_item_id for item in self.evidence_links}
        fact_logs = {item.fact_id: set(item.source_log_ids) for item in self.fact_links}
        reached_facts: set[str] = set()
        cited_claims: set[str] = set()
        for bullet in self.rendered_bullets:
            if not set(bullet.source_self_claim_ids).issubset(claim_ids):
                raise ValueError("rendered bullet cites an unlinked claim")
            if not set(bullet.source_fact_ids).issubset(fact_ids):
                raise ValueError("rendered bullet cites an unlinked fact")
            # §18: a bullet's logs equal — not contain — the closure reached
            # through its own facts, so an unresolved or surplus log ID fails
            # the document exactly as it fails the persisted graph.
            own_logs: set[str] = set()
            for fact_id in bullet.source_fact_ids:
                own_logs |= fact_logs[fact_id]
            if set(bullet.source_log_ids) != own_logs:
                raise ValueError("rendered bullet logs disagree with its fact closure")
            cited_claims.update(bullet.source_self_claim_ids)
            reached_facts.update(bullet.source_fact_ids)
        # An unused claim link is an unused closure member like any other.
        if cited_claims != claim_ids:
            raise ValueError("claim links disagree with the cited closure")
        for link in self.claim_links:
            if not set(link.source_fact_ids).issubset(fact_ids):
                raise ValueError("claim link reaches an unlinked fact")
            reached_facts.update(link.source_fact_ids)
        # An unused extra member fails export exactly like a missing one.
        if reached_facts != fact_ids:
            raise ValueError("fact links disagree with the reached closure")
        evidence_logs = {item.evidence_item_id: item.raw_log_id for item in self.evidence_links}
        reached_evidence: set[str] = set()
        for link in self.fact_links:
            if not set(link.evidence_item_ids).issubset(evidence_ids):
                raise ValueError("fact link reaches an unlinked evidence item")
            reached_evidence.update(link.evidence_item_ids)
            # Each edge is checked on its own: comparing only the union would
            # accept two facts whose logs are swapped between them.
            if set(link.source_log_ids) != {
                evidence_logs[item] for item in link.evidence_item_ids
            }:
                raise ValueError("fact link logs disagree with its own evidence")
        if reached_evidence != evidence_ids:
            raise ValueError("evidence links disagree with the reached closure")
        return self


class FindingExport(ExportDocument):
    bullet_id: str
    verification_status: VerificationStatus
    unsupported_phrases: list[str]
    verifier_reason: str | None

    @field_validator("bullet_id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("unsupported_phrases")
    @classmethod
    def normalized_phrases(cls, value: list[str]) -> list[str]:
        # A model-authored ordered string list: §13.12 keeps persisted order,
        # so only the projection normalization is applied here.
        return [_projected_text(item) for item in value]

    @field_validator("verifier_reason")
    @classmethod
    def normalized_reason(cls, value: str | None) -> str | None:
        return None if value is None else _projected_text(value)


class VerificationReportDocument(ExportDocument):
    schema_version: Literal[3]
    branch_id: str
    findings: list[FindingExport]

    @field_validator("branch_id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @model_validator(mode="after")
    def unique_findings(self) -> "VerificationReportDocument":
        ids = [item.bullet_id for item in self.findings]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("findings are empty or duplicated")
        return self


def _gap_export(item) -> GapExport:
    return GapExport(
        id=item.id,
        target_type=item.target_type,
        target_id=item.target_id,
        question=normalize_generated_text(item.question),
        reason=item.reason,
        priority=item.priority,
        answered=item.answered,
    )


def _contradiction_export(item) -> ContradictionExport:
    return ContradictionExport(
        id=item.id,
        title=normalize_generated_text(item.title),
        description=normalize_generated_text(item.description),
        left_ref_type=item.left_ref_type,
        left_ref_id=item.left_ref_id,
        right_ref_type=item.right_ref_type,
        right_ref_id=item.right_ref_id,
    )


def build_self_claims_document(graph: AssessmentExportGraph) -> SelfClaimsDocument:
    snapshot = graph.snapshot.value
    claims: list[SelfClaimExport] = []
    for stored in graph.claims:
        claim = stored.value
        counterevidence = [
            CounterevidenceExport(
                statement=normalize_generated_text(item.statement),
                source_ref_type=item.source_ref_type,
                source_ref_id=item.source_ref_id,
            )
            for item in sorted(
                claim.counterevidence,
                key=lambda item: (
                    id_key(item.source_ref_type),
                    id_key(item.source_ref_id),
                ),
            )
        ]
        claims.append(
            SelfClaimExport(
                id=claim.id,
                claim=normalize_generated_text(claim.claim),
                claim_kind=claim.claim_kind,
                dimension=claim.dimension,
                confidence=claim.confidence,
                verification_status=claim.verification_status,
                uncertainty=(
                    None
                    if claim.uncertainty is None
                    else normalize_generated_text(claim.uncertainty)
                ),
                source_fact_ids=sorted(claim.source_fact_ids, key=id_key),
                counter_fact_ids=sorted(claim.counter_fact_ids, key=id_key),
                counterevidence=counterevidence,
            )
        )
    return SelfClaimsDocument(
        schema_version=3,
        snapshot=SnapshotExport(
            id=snapshot.id,
            created_at=snapshot.created_at,
            scope=snapshot.scope,
            title=normalize_generated_text(snapshot.title),
            verification_status=snapshot.verification_status,
        ),
        claims=claims,
        unknowns=[_gap_export(item.value) for item in graph.gaps],
        contradictions=[
            _contradiction_export(item.value) for item in graph.contradictions
        ],
    )


def build_evidence_map_document(
    graph: AssessmentExportGraph,
) -> AssessmentEvidenceMapDocument:
    return AssessmentEvidenceMapDocument(
        schema_version=3,
        output_kind="assessment",
        entity_id=graph.snapshot.value.id,
        rendered_claim_ids=[item.value.id for item in graph.claims],
        claim_links=[
            ClaimLink(
                claim_id=item.value.id,
                source_fact_ids=sorted(item.value.source_fact_ids, key=id_key),
                counter_fact_ids=sorted(item.value.counter_fact_ids, key=id_key),
            )
            for item in graph.claims
        ],
        fact_links=[
            FactLink(
                fact_id=item.value.id,
                evidence_item_ids=sorted(item.value.evidence_item_ids, key=id_key),
                source_log_ids=sorted(item.value.source_log_ids, key=id_key),
            )
            for item in graph.facts
        ],
        evidence_links=[
            EvidenceLink(evidence_item_id=item.id, raw_log_id=item.raw_log_id)
            for item in graph.evidence_items
        ],
    )


def build_bullet_pack_evidence_map(
    graph: "BranchExportGraph",
) -> BulletPackEvidenceMapDocument:
    return BulletPackEvidenceMapDocument(
        schema_version=3,
        output_kind="resume",
        entity_id=graph.branch.value.id,
        # §13.10 render order, recomputed by the graph — not ID order.
        rendered_bullets=[
            RenderedBulletExport(
                bullet_id=item.value.id,
                text=normalize_generated_text(item.value.text),
                target_section=item.value.target_section,
                matched_jd_requirements=sorted(
                    item.value.matched_jd_requirements, key=id_key
                ),
                source_self_claim_ids=sorted(
                    item.value.source_self_claim_ids, key=id_key
                ),
                source_fact_ids=sorted(item.value.source_fact_ids, key=id_key),
                source_log_ids=sorted(item.value.source_log_ids, key=id_key),
            )
            for item in graph.bullets
        ],
        claim_links=[
            ClaimLink(
                claim_id=item.value.id,
                source_fact_ids=sorted(item.value.source_fact_ids, key=id_key),
                counter_fact_ids=sorted(item.value.counter_fact_ids, key=id_key),
            )
            for item in graph.claims
        ],
        fact_links=[
            FactLink(
                fact_id=item.value.id,
                evidence_item_ids=sorted(item.value.evidence_item_ids, key=id_key),
                source_log_ids=sorted(item.value.source_log_ids, key=id_key),
            )
            for item in graph.facts
        ],
        evidence_links=[
            EvidenceLink(evidence_item_id=item.id, raw_log_id=item.raw_log_id)
            for item in graph.evidence_items
        ],
    )


def build_verification_report(graph: "BranchExportGraph") -> VerificationReportDocument:
    """Project each bullet's denormalized §11.8 status onto the report.

    §13.12: one row per rendered bullet in the same order and with no other
    ID. Append-only §11.14 finding history and `suggested_rewrite` never
    export, so nothing here reads the `verification_findings` table.
    """

    return VerificationReportDocument(
        schema_version=3,
        branch_id=graph.branch.value.id,
        findings=[
            FindingExport(
                bullet_id=item.value.id,
                verification_status=item.value.verification_status,
                unsupported_phrases=[
                    normalize_generated_text(phrase)
                    for phrase in item.value.unsupported_phrases
                ],
                verifier_reason=(
                    None
                    if item.value.verifier_reason is None
                    else normalize_generated_text(item.value.verifier_reason)
                ),
            )
            for item in graph.bullets
        ],
    )


def companion_bytes(document: ExportDocument) -> bytes:
    return canonical_json_bytes(document.model_dump(mode="python")) + b"\n"
