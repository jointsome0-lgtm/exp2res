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
from exp2res.exports.graph import fs_id_key
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


def test_evidence_map_attributes_each_claim_to_its_own_persisted_facts() -> None:
    # §13.12: the closure union is not a substitute for attribution. Each link
    # is built from one persisted claim row, so exchanging two claims' edges
    # produces a different document even though the reached facts are equal.
    graph = assessment_graph()
    document = build_evidence_map_document(graph)
    linked = {link.claim_id: link for link in document.claim_links}
    for record in graph.claims:
        link = linked[record.value.id]
        assert list(link.source_fact_ids) == sorted(
            record.value.source_fact_ids, key=fs_id_key
        )
        assert list(link.counter_fact_ids) == sorted(
            record.value.counter_fact_ids, key=fs_id_key
        )

    pattern = next(
        item for item in graph.claims if item.value.claim_kind == "pattern_signal"
    )
    other = next(
        item
        for item in graph.claims
        if item.value.source_fact_ids != pattern.value.source_fact_ids
    )
    exchanged = {
        pattern.value.id: other.value,
        other.value.id: pattern.value,
    }
    swapped = replace(
        graph,
        claims=tuple(
            replace(
                record,
                value=record.value.model_copy(
                    update={
                        "source_fact_ids": exchanged[record.value.id].source_fact_ids,
                        "counter_fact_ids": exchanged[record.value.id].counter_fact_ids,
                    }
                ),
            )
            if record.value.id in exchanged
            else record
            for record in graph.claims
        ),
    )
    exported = build_evidence_map_document(swapped)
    assert {link.fact_id for link in exported.fact_links} == {
        link.fact_id for link in document.fact_links
    }
    assert exported.claim_links != document.claim_links


@pytest.mark.parametrize(
    "break_document",
    [
        pytest.param(
            lambda pinned: {
                **pinned,
                "claim_links": [
                    {**link, "source_fact_ids": ["fact_vera_9999"]}
                    for link in pinned["claim_links"]
                ],
            },
            id="claim-reaches-an-unlinked-fact",
        ),
        pytest.param(
            lambda pinned: {
                **pinned,
                "fact_links": [
                    *pinned["fact_links"],
                    {
                        "fact_id": "fact_vera_9999",
                        "evidence_item_ids": ["evidence_item_vera_0001"],
                        "source_log_ids": ["raw_log_vera_0001"],
                    },
                ],
            },
            id="unused-fact-link",
        ),
        pytest.param(
            lambda pinned: {
                **pinned,
                "evidence_links": [
                    *pinned["evidence_links"],
                    {
                        "evidence_item_id": "evidence_item_vera_9999",
                        "raw_log_id": "raw_log_vera_0001",
                    },
                ],
            },
            id="unused-evidence-link",
        ),
    ],
)
def test_assessment_map_rejects_unresolved_and_unused_closure_members(
    break_document: object,
) -> None:
    # §21.48: the assessment companion enforces the same claim → fact →
    # evidence → log closure as the bullet-pack one, so an unresolved or
    # unused member fails the document instead of reaching publication.
    pinned = json.loads(
        (REPOSITORY_ROOT / "tests" / "goldens" / "assessment" / "evidence_map.json")
        .read_text(encoding="utf-8")
    )
    assert AssessmentEvidenceMapDocument.model_validate(pinned)
    with pytest.raises(ValidationError):
        AssessmentEvidenceMapDocument.model_validate(break_document(pinned))


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


@pytest.mark.parametrize("logs", (["raw_log_vera_9999"], []))
def test_bullet_pack_map_rejects_bullet_logs_outside_its_fact_closure(
    logs: list[str],
) -> None:
    # §21.48: a bullet's logs equal the closure its own facts reach, so an
    # unresolved reference and a dropped one both fail the document.
    pinned = json.loads(
        (REPOSITORY_ROOT / "tests" / "goldens" / "branch" / "evidence_map.json")
        .read_text(encoding="utf-8")
    )
    assert BulletPackEvidenceMapDocument.model_validate(pinned)

    broken = dict(pinned)
    first, *rest = pinned["rendered_bullets"]
    broken["rendered_bullets"] = [{**first, "source_log_ids": logs}, *rest]
    with pytest.raises(ValidationError):
        BulletPackEvidenceMapDocument.model_validate(broken)


def test_bullet_pack_map_rejects_two_facts_with_their_logs_swapped() -> None:
    # §21.48: each fact-to-log edge is checked on its own, so a cross-wired
    # pair cannot look evidence-complete through the global union alone.
    pinned = json.loads(
        (REPOSITORY_ROOT / "tests" / "goldens" / "branch" / "evidence_map.json")
        .read_text(encoding="utf-8")
    )
    # The golden's two facts share one evidence item, so the cross-wiring is
    # only visible once each fact carries its own. This widened map is a
    # legitimate pack and must still validate.
    def wired(first_logs: list[str], second_logs: list[str]) -> dict:
        widened = dict(pinned)
        widened["evidence_links"] = [
            {"evidence_item_id": "evidence_item_vera_0001", "raw_log_id": "raw_log_vera_0001"},
            {"evidence_item_id": "evidence_item_vera_0002", "raw_log_id": "raw_log_vera_0002"},
        ]
        widened["fact_links"] = [
            {
                "fact_id": "fact_vera_0001",
                "evidence_item_ids": ["evidence_item_vera_0001"],
                "source_log_ids": first_logs,
            },
            {
                "fact_id": "fact_vera_0002",
                "evidence_item_ids": ["evidence_item_vera_0002"],
                "source_log_ids": second_logs,
            },
        ]
        widened["rendered_bullets"] = [
            {**pinned["rendered_bullets"][0], "source_log_ids": first_logs},
            {**pinned["rendered_bullets"][1], "source_log_ids": second_logs},
        ]
        return widened

    assert BulletPackEvidenceMapDocument.model_validate(
        wired(["raw_log_vera_0001"], ["raw_log_vera_0002"])
    )
    with pytest.raises(ValidationError):
        BulletPackEvidenceMapDocument.model_validate(
            wired(["raw_log_vera_0002"], ["raw_log_vera_0001"])
        )


def test_bullet_pack_map_rejects_a_fact_link_with_no_evidence_or_log() -> None:
    # §21.48: an empty link satisfies every subset and equality comparison
    # while leaving the fact it stands for outside the closure entirely.
    pinned = json.loads(
        (REPOSITORY_ROOT / "tests" / "goldens" / "branch" / "evidence_map.json")
        .read_text(encoding="utf-8")
    )
    emptied = dict(pinned)
    first, *rest = pinned["fact_links"]
    emptied["fact_links"] = [
        {**first, "evidence_item_ids": [], "source_log_ids": []},
        *rest,
    ]
    emptied["rendered_bullets"] = [
        {**bullet, "source_log_ids": []}
        if bullet["source_fact_ids"] == [first["fact_id"]]
        else bullet
        for bullet in pinned["rendered_bullets"]
    ]
    with pytest.raises(ValidationError):
        BulletPackEvidenceMapDocument.model_validate(emptied)


def test_bullet_pack_map_rejects_a_claim_link_with_no_source_fact() -> None:
    # §21.48: the claim-guided row must round-trip through its own claim's
    # direct-fact edges, which an empty claim link would silently skip while
    # the bullets' own facts keep the reached-fact union complete.
    pinned = json.loads(
        (REPOSITORY_ROOT / "tests" / "goldens" / "branch" / "evidence_map.json")
        .read_text(encoding="utf-8")
    )
    emptied = dict(pinned)
    first, *rest = pinned["claim_links"]
    emptied["claim_links"] = [
        {**first, "source_fact_ids": [], "counter_fact_ids": []},
        *rest,
    ]
    with pytest.raises(ValidationError):
        BulletPackEvidenceMapDocument.model_validate(emptied)


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

