"""Closed deterministic §13.12 assessment-companion tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json

from pydantic import ValidationError
import pytest

from exp2res.exports.companions import (
    AssessmentEvidenceMapDocument,
    BulletPackEvidenceMapDocument,
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

from conftest import REPOSITORY_ROOT
from export_helpers import assessment_graph, graph_with_gap_answered


pytestmark = pytest.mark.unit


def test_companion_encoding_has_canonical_key_order_utc_datetime_and_one_lf() -> None:
    graph = assessment_graph(all_sections=False)
    document = build_self_claims_document(graph)
    encoded = companion_bytes(document)
    assert encoded.endswith(b"\n") and not encoded.endswith(b"\n\n")
    assert encoded.startswith(b'{"claims":')
    assert b'"created_at":"2026-07-20T08:00:00.000000Z"' in encoded
    assert (
        b"Current evidence suggests you deliver deterministic local tools." in encoded
    )
    assert json.loads(encoded)["schema_version"] == 3

    evidence = companion_bytes(build_evidence_map_document(graph))
    assert evidence.startswith(b'{"claim_links":')
    assert json.loads(evidence)["rendered_claim_ids"] == sorted(
        json.loads(evidence)["rendered_claim_ids"], key=lambda value: value.encode()
    )


def test_companions_record_the_claim_counter_split() -> None:
    # §13.12/§11.6: §15.4's patterns are transport-only, so the claim's own
    # counter members are the whole durable record of contrary support —
    # both companions carry the split the §17 report renders as a suffix.
    graph = assessment_graph(all_sections=True)
    claims = json.loads(companion_bytes(build_self_claims_document(graph)))
    pattern = next(
        item for item in claims["claims"] if item["claim_kind"] == "pattern_signal"
    )
    assert pattern["source_fact_ids"] == [
        "fact_vera_export_0001",
        "fact_vera_export_0002",
    ]
    assert pattern["counter_fact_ids"] == ["fact_vera_export_0002"]

    evidence = json.loads(companion_bytes(build_evidence_map_document(graph)))
    link = next(
        item
        for item in evidence["claim_links"]
        if item["claim_id"] == pattern["id"]
    )
    assert link["source_fact_ids"] == pattern["source_fact_ids"]
    assert link["counter_fact_ids"] == pattern["counter_fact_ids"]
    others = [
        item for item in evidence["claim_links"] if item["claim_id"] != pattern["id"]
    ]
    assert others and all(item["counter_fact_ids"] == [] for item in others)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (SelfClaimsDocument, {"schema_version": 2, "extra": True}),
        (SelfClaimsDocument, {"schema_version": 3}),
        (AssessmentEvidenceMapDocument, {"schema_version": 2}),
        (AssessmentEvidenceMapDocument, {"schema_version": 3, "output_kind": "assessment"}),
    ],
)
def test_closed_companion_models_reject_extra_missing_and_wrong_version(
    model, payload
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_bullet_pack_map_rejects_a_claim_link_no_bullet_cites() -> None:
    # §21.48: an unused closure member fails export exactly like a missing one,
    # and a claim link is the one closure list bullets reach by citation.
    pinned = json.loads(
        (REPOSITORY_ROOT / "tests" / "goldens" / "branch" / "evidence_map.json")
        .read_text(encoding="utf-8")
    )
    assert BulletPackEvidenceMapDocument.model_validate(pinned)

    uncited = dict(pinned)
    uncited["claim_links"] = sorted(
        [
            *pinned["claim_links"],
            {
                "claim_id": "claim_vera_9999",
                "counter_fact_ids": [],
                "source_fact_ids": pinned["fact_links"][0]["fact_id"].split(),
            },
        ],
        key=lambda item: item["claim_id"].encode(),
    )
    with pytest.raises(ValidationError):
        BulletPackEvidenceMapDocument.model_validate(uncited)


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
        "report.html",
        "report.md",
        "self_claims.json",
    ]
    assert manifest.source_ids.self_claim_ids == [graph.claims[0].value.id]
    with pytest.raises(ValidationError):
        type(manifest).model_validate({**manifest.model_dump(), "unexpected": 1})

