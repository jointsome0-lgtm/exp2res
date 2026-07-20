"""Closed deterministic §13.12 assessment-companion tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json

from pydantic import ValidationError
import pytest

from exp2res.exports.companions import (
    AssessmentEvidenceMapDocument,
    SelfClaimsDocument,
    build_evidence_map_document,
    build_self_claims_document,
    companion_bytes,
)
from exp2res.exports.managed import (
    assessment_member_bytes,
    build_assessment_manifest,
    render_input_sha256,
)

from export_helpers import assessment_graph, graph_with_gap_answered


pytestmark = pytest.mark.unit


def test_companion_encoding_has_canonical_key_order_utc_datetime_and_one_lf() -> None:
    graph = assessment_graph(all_sections=False)
    document = build_self_claims_document(graph)
    encoded = companion_bytes(document)
    assert encoded.endswith(b"\n") and not encoded.endswith(b"\n\n")
    assert encoded.startswith(b'{"claims":')
    assert b'"created_at":"2026-07-20T08:00:00.000000Z"' in encoded
    assert b"Vera Example" in encoded
    assert json.loads(encoded)["schema_version"] == 1

    evidence = companion_bytes(build_evidence_map_document(graph))
    assert evidence.startswith(b'{"claim_links":')
    assert json.loads(evidence)["rendered_claim_ids"] == sorted(
        json.loads(evidence)["rendered_claim_ids"], key=lambda value: value.encode()
    )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (SelfClaimsDocument, {"schema_version": 1, "extra": True}),
        (SelfClaimsDocument, {"schema_version": 2}),
        (AssessmentEvidenceMapDocument, {"schema_version": 1}),
        (AssessmentEvidenceMapDocument, {"schema_version": 2, "output_kind": "assessment"}),
    ],
)
def test_closed_companion_models_reject_extra_missing_and_wrong_version(
    model, payload
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_render_input_hash_covers_gap_lifecycle_and_verification_at_same_ids() -> None:
    graph = assessment_graph(all_sections=False)
    answered = graph_with_gap_answered(graph, True)
    assert answered.gaps[0].value.id == graph.gaps[0].value.id
    assert render_input_sha256(answered) != render_input_sha256(graph)

    stored_claim = graph.claims[0]
    changed_claim = stored_claim.value.model_copy(
        update={"verification_status": "partially_supported"}
    )
    changed_snapshot = graph.snapshot.value.model_copy(
        update={"verification_status": "partially_supported"}
    )
    verification_changed = replace(
        graph,
        claims=(replace(stored_claim, value=changed_claim),),
        snapshot=replace(graph.snapshot, value=changed_snapshot),
    )
    assert verification_changed.snapshot.generation_id == graph.snapshot.generation_id
    assert render_input_sha256(verification_changed) != render_input_sha256(graph)


def test_manifest_is_closed_complete_and_member_byte_hashed() -> None:
    graph = assessment_graph(all_sections=False)
    members = assessment_member_bytes(graph)
    manifest = build_assessment_manifest(
        graph,
        members,
        created_at=datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )
    assert [item.name for item in manifest.members] == [
        "evidence_map.json",
        "report.md",
        "self_claims.json",
    ]
    assert manifest.source_ids.self_claim_ids == [graph.claims[0].value.id]
    with pytest.raises(ValidationError):
        type(manifest).model_validate({**manifest.model_dump(), "unexpected": 1})

