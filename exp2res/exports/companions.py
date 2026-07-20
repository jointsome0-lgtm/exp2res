"""Closed §13.12 assessment JSON companion documents."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    scope_target: str | None
    title: str
    verification_status: VerificationStatus

    @field_validator("id", "scope_target")
    @classmethod
    def structural_values(cls, value: str | None) -> str | None:
        return None if value is None else validate_structural(value)

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
    source_signal_ids: list[str]
    source_fact_ids: list[str]
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

    @field_validator("source_signal_ids", "source_fact_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)

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
    schema_version: Literal[1]
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
    source_signal_ids: list[str]
    source_fact_ids: list[str]

    @field_validator("claim_id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("source_signal_ids", "source_fact_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)


class SignalLink(ExportDocument):
    signal_id: str
    supporting_fact_ids: list[str]
    counter_fact_ids: list[str]

    @field_validator("signal_id")
    @classmethod
    def structural_id(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("supporting_fact_ids", "counter_fact_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return _require_unique(value)


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


class EvidenceLink(ExportDocument):
    evidence_item_id: str
    raw_log_id: str

    @field_validator("evidence_item_id", "raw_log_id")
    @classmethod
    def structural_ids(cls, value: str) -> str:
        return validate_structural(value)


class AssessmentEvidenceMapDocument(ExportDocument):
    schema_version: Literal[1]
    output_kind: Literal["assessment"]
    entity_id: str
    rendered_claim_ids: list[str]
    claim_links: list[ClaimLink]
    signal_links: list[SignalLink]
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
            [item.signal_id for item in self.signal_links],
            [item.fact_id for item in self.fact_links],
            [item.evidence_item_id for item in self.evidence_links],
        )
        for ids in grouped_ids:
            if len(ids) != len(set(ids)) or ids != sorted(ids, key=id_key):
                raise ValueError("evidence-map links are duplicate or unordered")
        if self.rendered_claim_ids != grouped_ids[0]:
            raise ValueError("rendered claim IDs disagree with claim links")
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
                source_signal_ids=sorted(claim.source_signal_ids, key=id_key),
                source_fact_ids=sorted(claim.source_fact_ids, key=id_key),
                counterevidence=counterevidence,
            )
        )
    return SelfClaimsDocument(
        schema_version=1,
        snapshot=SnapshotExport(
            id=snapshot.id,
            created_at=snapshot.created_at,
            scope=snapshot.scope,
            scope_target=snapshot.scope_target,
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
        schema_version=1,
        output_kind="assessment",
        entity_id=graph.snapshot.value.id,
        rendered_claim_ids=[item.value.id for item in graph.claims],
        claim_links=[
            ClaimLink(
                claim_id=item.value.id,
                source_signal_ids=sorted(item.value.source_signal_ids, key=id_key),
                source_fact_ids=sorted(item.value.source_fact_ids, key=id_key),
            )
            for item in graph.claims
        ],
        signal_links=[
            SignalLink(
                signal_id=item.value.id,
                supporting_fact_ids=sorted(
                    item.value.supporting_fact_ids, key=id_key
                ),
                counter_fact_ids=sorted(item.value.counter_fact_ids, key=id_key),
            )
            for item in graph.signals
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


def companion_bytes(document: ExportDocument) -> bytes:
    return canonical_json_bytes(document.model_dump(mode="python")) + b"\n"
