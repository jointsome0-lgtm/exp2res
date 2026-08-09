"""Offline §13.12/§18 verified-bullet-pack export tests.

Stage 12 is the only writer of `out/branch/<branch-id>/`, so these hold the
fixed member set and its byte rules, the §13.10 render order the pack recovers
from persisted state alone, the closed companion schemas, and — the property
that keeps a failure from being visible to a reader — that every closed-
document failure mode refuses *before* anything reaches the final path.
"""

from __future__ import annotations

import json
from pathlib import Path
import unicodedata

import pytest

from exp2res.domain.enums import ResumeTargetSection
from exp2res.domain.verification import aggregate_verification_status
from exp2res.errors import (
    BulletPackExportBlockedError,
    IntegrityFailureError,
    ManagedOutputIncompleteError,
    SelectorNotFoundError,
)
from exp2res.exports.branch import (
    branch_render_input_bundle,
    load_branch_graph,
    load_current_branch,
    render_order,
)
from exp2res.exports.bullet_pack import render_bullet_pack, section_heading
from exp2res.exports.companions import (
    build_bullet_pack_evidence_map,
    build_verification_report,
    companion_bytes,
)
from exp2res.exports.managed import (
    ResumeManifest,
    branch_member_bytes,
    build_branch_manifest,
)
from exp2res.services.export import export_assessment, export_bullet_pack
from exp2res.storage.repository import (
    current_branch_by_folded_name,
    get_experience_fact,
    list_resume_bullets_for_branch,
    list_self_claims_for_snapshot,
    update_resume_bullet_verification,
)
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from assessment_helpers import VeraIds, prepare_facts, prepare_high_facts
from test_branch_substrate import (
    BRANCH_NAME,
    REQUIREMENT_ID,
    anchor_snapshot,
    plant_job_description,
)
from test_stage6_assessment import assessment_response, run_stage6
from test_stage3_extraction import add_log, exact_day
from test_stage10_generation import (
    BULLET_TEXT,
    SECOND_TEXT,
    bullet_candidate,
    prepare_anchor,
    run_stage10,
    writer_response,
)
from test_stage11_bullet_verification import (
    finding,
    prepare_generated_branch,
    prepare_paired_anchor,
    run_stage11,
    verifier_response,
)


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


_MEMBERS = ("bullet_pack.md", "evidence_map.json", "verification_report.json")
_ALL = (*_MEMBERS, "manifest.json")


def verified_branch(workspace: Path, *, texts: list[str] | None = None):
    """One current branch whose every bullet carries a `supported` verdict."""

    ids, facts, snapshot_id, branch_id, bullet_ids = prepare_generated_branch(
        workspace, texts=texts
    )
    run_stage11(
        workspace,
        FakeContractRunner(
            [verifier_response([finding(bullet_id) for bullet_id in bullet_ids])]
        ),
        ids,
    )
    return ids, facts, snapshot_id, branch_id, bullet_ids


def branch_graph(workspace: Path, branch_id: str):
    with read_database(workspace) as connection:
        branch_row, branch = load_current_branch(connection, branch_id)
        return load_branch_graph(connection, branch_row=branch_row, branch=branch)


def published(workspace: Path, branch_id: str) -> Path:
    return workspace / "out" / "branch" / branch_id


def test_the_published_set_is_exactly_the_three_members_and_a_manifest(
    workspace: Path,
) -> None:
    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)

    exported = export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    directory = published(workspace, branch_id)
    assert sorted(item.name for item in directory.iterdir()) == sorted(_ALL)
    assert exported.manifest_path == str(directory / "manifest.json")
    assert exported.branch_id == branch_id
    assert sorted(exported.managed_paths) == sorted(
        str(directory / name) for name in _ALL
    )


def test_publishing_a_branch_leaves_its_anchor_snapshot_set_alone(
    workspace: Path,
) -> None:
    """§13.14 rule 1: the two managed kinds are keyed independently.

    Only the assessment kind sweeps same-view siblings, because only it has
    one current view per scope. A branch publication that reached across
    into `out/assessment/` would silently unpublish the mirror the owner
    exported minutes earlier.
    """

    _ids, _facts, snapshot_id, _branch_id, _bullet_ids = verified_branch(workspace)
    export_assessment(workspace, snapshot_id=snapshot_id)
    assessment_set = workspace / "out" / "assessment" / snapshot_id
    before = {item.name: item.read_bytes() for item in assessment_set.iterdir()}

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    after = {item.name: item.read_bytes() for item in assessment_set.iterdir()}
    assert after == before


def test_every_member_is_owner_private_and_ends_in_one_lf(workspace: Path) -> None:
    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    directory = published(workspace, branch_id)
    assert directory.stat().st_mode & 0o777 == 0o700
    for name in _ALL:
        member = directory / name
        assert member.stat().st_mode & 0o777 == 0o600
        data = member.read_bytes()
        assert data.endswith(b"\n") and not data.endswith(b"\n\n")


def test_re_exporting_the_same_branch_state_is_byte_identical(
    workspace: Path,
) -> None:
    """§13.12: the same coherent branch state renders the same member bytes.

    The second export is a fresh render, not a read of the first: it rebuilds
    every member and compares them against the published set. Equality here is
    what makes the manifest's member digests a durable identity rather than a
    record of one particular run.
    """

    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(
        workspace, texts=[BULLET_TEXT, SECOND_TEXT]
    )

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    directory = published(workspace, branch_id)
    first = {name: (directory / name).read_bytes() for name in _MEMBERS}
    first_manifest = json.loads((directory / "manifest.json").read_text("utf-8"))

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    second = {name: (directory / name).read_bytes() for name in _MEMBERS}
    second_manifest = json.loads((directory / "manifest.json").read_text("utf-8"))

    assert first == second
    assert first_manifest["members"] == second_manifest["members"]
    assert (
        first_manifest["render_input_sha256"]
        == second_manifest["render_input_sha256"]
    )


def test_the_pack_renders_every_section_heading_and_no_filler(
    workspace: Path,
) -> None:
    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)

    graph = branch_graph(workspace, branch_id)
    rendered = render_bullet_pack(graph).decode("utf-8")

    lines = rendered.split("\n")
    assert lines[0] == "# Verified Bullet Pack"
    assert lines[1] == ""
    headings = [line for line in lines if line.startswith("## ")]
    assert headings == [
        f"## {section_heading(section)}"
        for section in ResumeTargetSection.__args__  # type: ignore[attr-defined]
    ]
    # §18: no empty logical line follows the final section, and §13.12 owns
    # the one final LF.
    assert rendered.endswith("\n") and not rendered.endswith("\n\n")
    bullet_lines = [line for line in lines if line.startswith("- ")]
    assert len(bullet_lines) == len(graph.bullets)


def test_the_heading_derivation_is_the_closed_split_and_capitalize(
    workspace: Path,
) -> None:
    assert section_heading("professional_experience") == "Professional Experience"
    assert section_heading("summary") == "Summary"
    assert section_heading("selected_projects") == "Selected Projects"


def test_the_render_order_is_recomputed_and_not_the_stored_id_order(
    workspace: Path,
) -> None:
    """§13.10 rule 56: Stage 12 recovers the order from persisted state.

    Allocated bullet IDs carry a random component, so the stored ID order and
    the render order agree only by luck. Sorting the same bullets by ID and by
    the §13.10 key must therefore be compared against the key, not against
    whatever the repository happened to return.
    """

    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(
        workspace, texts=[SECOND_TEXT, BULLET_TEXT]
    )

    graph = branch_graph(workspace, branch_id)
    with read_database(workspace) as connection:
        stored = list_resume_bullets_for_branch(connection, branch_id)

    expected = render_order(stored, [REQUIREMENT_ID])
    assert [item.value.id for item in graph.bullets] == [
        bullet.id for bullet in expected
    ]
    # The two candidate texts differ, so the key is total on text bytes alone.
    assert [item.value.text for item in graph.bullets] == sorted(
        bullet.text for bullet in stored
    )


def test_the_evidence_map_closes_over_every_rendered_bullet(
    workspace: Path,
) -> None:
    _ids, _facts, _snapshot_id, branch_id, bullet_ids = verified_branch(workspace)

    graph = branch_graph(workspace, branch_id)
    document = build_bullet_pack_evidence_map(graph)

    assert document.schema_version == 3
    assert document.output_kind == "resume"
    assert document.entity_id == branch_id
    assert [item.bullet_id for item in document.rendered_bullets] == [
        item.value.id for item in graph.bullets
    ]
    reached = {
        fact_id
        for bullet in document.rendered_bullets
        for fact_id in bullet.source_fact_ids
    }
    assert reached.issubset({item.fact_id for item in document.fact_links})
    assert {item.evidence_item_id for item in document.evidence_links} == {
        item_id
        for link in document.fact_links
        for item_id in link.evidence_item_ids
    }


def test_the_report_carries_one_row_per_bullet_in_render_order(
    workspace: Path,
) -> None:
    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(
        workspace, texts=[BULLET_TEXT, SECOND_TEXT]
    )

    graph = branch_graph(workspace, branch_id)
    report = build_verification_report(graph)

    assert report.schema_version == 3
    assert report.branch_id == branch_id
    assert [item.bullet_id for item in report.findings] == [
        item.value.id for item in graph.bullets
    ]
    assert {item.verification_status for item in report.findings} == {"supported"}


def test_the_report_never_carries_a_suggested_rewrite(workspace: Path) -> None:
    """§13.12: append-only finding history and rewrites never export."""

    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)

    graph = branch_graph(workspace, branch_id)
    payload = json.loads(companion_bytes(build_verification_report(graph)))

    assert set(payload) == {"schema_version", "branch_id", "findings"}
    for row in payload["findings"]:
        assert set(row) == {
            "bullet_id",
            "verification_status",
            "unsupported_phrases",
            "verifier_reason",
        }


def test_the_manifest_identity_and_source_lists_are_the_resume_shape(
    workspace: Path,
) -> None:
    _ids, _facts, snapshot_id, branch_id, bullet_ids = verified_branch(workspace)

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    payload = json.loads(
        (published(workspace, branch_id) / "manifest.json").read_text("utf-8")
    )
    manifest = ResumeManifest.model_validate_json(
        (published(workspace, branch_id) / "manifest.json").read_bytes()
    )
    assert manifest.output_kind == "resume"
    assert manifest.manifest_version == 6
    assert manifest.entity_id == branch_id
    assert manifest.identity.branch_name == BRANCH_NAME
    assert manifest.identity.assessment_snapshot_id == snapshot_id
    assert manifest.source_ids.assessment_snapshot_ids == [snapshot_id]
    assert manifest.source_ids.resume_bullet_ids == sorted(
        bullet_ids, key=lambda value: value.encode("utf-8")
    )
    assert manifest.source_ids.jd_requirement_ids == [REQUIREMENT_ID]
    assert [item.name for item in manifest.members] == sorted(_MEMBERS)


def test_an_unverified_bullet_blocks_the_export(workspace: Path) -> None:
    """§16.11: only a `supported` bullet may enter the pack."""

    ids, _facts, _snapshot_id, branch_id, bullet_ids = prepare_generated_branch(
        workspace
    )

    with pytest.raises(BulletPackExportBlockedError):
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert not published(workspace, branch_id).exists()


@pytest.mark.parametrize(
    "status",
    ["partially_supported", "needs_clarification", "contradicted", "rejected"],
)
def test_every_non_supported_status_blocks_the_export(
    workspace: Path, status: str
) -> None:
    _ids, _facts, _snapshot_id, branch_id, bullet_ids = verified_branch(workspace)

    with writer_database(workspace) as connection:
        update_resume_bullet_verification(
            connection,
            bullet_id=bullet_ids[0],
            verification_status=status,
            unsupported_phrases=[],
            verifier_reason="A later transition moved this bullet off supported.",
        )
        connection.commit()

    with pytest.raises(BulletPackExportBlockedError):
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert not published(workspace, branch_id).exists()


@pytest.mark.parametrize(
    "status", ["partially_supported", "inferred_but_acceptable"]
)
def test_a_lesser_but_eligible_anchor_still_carries_the_pack(
    workspace: Path, status: str
) -> None:
    """§16.11: the anchor allowlist is wider than the bullet allowlist.

    The two gates are separate on purpose: a mirror the verifier only
    partially supported is still an honest anchor, so a branch whose every
    bullet is `supported` exports over it rather than inheriting the
    snapshot's weaker verdict.
    """

    _ids, _facts, snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)
    with writer_database(workspace) as connection:
        # The aggregate is recomputed at export, so the anchor's lesser status
        # has to come from a real member claim rather than a planted column.
        # No bullet in this fixture cites a claim, so moving one non-summary
        # member is enough to reduce the whole snapshot.
        claims = list_self_claims_for_snapshot(connection, snapshot_id)
        target = next(
            claim for claim in claims if claim.claim_kind != "narrative_summary"
        )
        connection.execute("DROP TRIGGER self_claims_lifecycle_update_guard")
        connection.execute(
            "UPDATE self_claims SET verification_status = ? WHERE id = ?",
            (status, target.id),
        )
        reduced = aggregate_verification_status(
            [
                status if claim.id == target.id else claim.verification_status
                for claim in claims
            ]
        )
        assert reduced == status
        connection.execute(
            "UPDATE assessment_snapshots SET verification_status = ? WHERE id = ?",
            (reduced, snapshot_id),
        )
        connection.commit()

    result = export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    assert published(workspace, branch_id).is_dir()
    assert sorted(Path(path).name for path in result.managed_paths) == sorted(_ALL)


def _damage_dead_anchor(workspace: Path, branch_id: str, snapshot_id: str) -> None:
    with writer_database(workspace) as connection:
        connection.execute(
            "UPDATE assessment_snapshots SET superseded_at = ? WHERE id = ?",
            (FIXED_NOW.isoformat(), snapshot_id),
        )
        connection.commit()


def _damage_mixed_generation(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    with writer_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        connection.execute(
            "UPDATE resume_bullets SET generation_id = ? WHERE id = ?",
            ("gen_vera_other_batch", bullets[0].id),
        )
        connection.commit()


def _damage_log_closure(workspace: Path, branch_id: str, snapshot_id: str) -> None:
    with writer_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        connection.execute(
            "UPDATE resume_bullets SET source_log_ids_json = ? WHERE id = ?",
            (json.dumps(["log_vera_not_in_closure"]), bullets[0].id),
        )
        connection.commit()


def _damage_ungrounded_bullet(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    with writer_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        connection.execute(
            "UPDATE resume_bullets SET source_fact_ids_json = ?,"
            " source_log_ids_json = ? WHERE id = ?",
            (json.dumps([]), json.dumps([]), bullets[0].id),
        )
        connection.commit()


def _damage_foreign_requirement(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    with writer_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        connection.execute(
            "UPDATE resume_bullets SET matched_jd_requirements_json = ? WHERE id = ?",
            (json.dumps(["jdreq_vera_other_job"]), bullets[0].id),
        )
        connection.commit()


def _damage_empty_bullet_set(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    with writer_database(workspace) as connection:
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        connection.execute(
            "UPDATE resume_bullets SET superseded_at = ? WHERE branch_id = ?",
            (FIXED_NOW.isoformat(), branch_id),
        )
        connection.commit()


def _damage_ineligible_status(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    with writer_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        update_resume_bullet_verification(
            connection,
            bullet_id=bullets[0].id,
            verification_status="rejected",
            unsupported_phrases=["an unsupported production claim"],
            verifier_reason="The linked records do not carry this assertion.",
        )
        connection.commit()


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(_damage_dead_anchor, id="dead-anchor"),
        pytest.param(_damage_mixed_generation, id="mixed-generation"),
        pytest.param(_damage_log_closure, id="log-closure-mismatch"),
        pytest.param(_damage_ungrounded_bullet, id="ungrounded-bullet"),
        pytest.param(_damage_foreign_requirement, id="foreign-requirement"),
        pytest.param(_damage_empty_bullet_set, id="empty-bullet-set"),
        pytest.param(_damage_ineligible_status, id="ineligible-status"),
    ],
)
def test_every_closed_document_failure_writes_nothing_to_out_branch(
    workspace: Path, damage
) -> None:
    """§13.14 rule 8: a pre-commit failure publishes no candidate at all.

    Each damage below makes one of the closed documents unbuildable — a dead
    anchor, a pack spanning two batches, a closure that no longer equals the
    reached logs, a bullet grounded in no fact and no log at all, a
    requirement from another vacancy, an emptied batch, or a bullet outside
    the §16.11 allowlist. The property under test is not that
    export raises; it is that the reader never sees a partial set, a
    candidate, or a rollback sibling left behind by the attempt.
    """

    _ids, _facts, snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)
    parent = workspace / "out" / "branch"
    # The healthy branch exports, so each failure below is the damage talking.
    export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert published(workspace, branch_id).is_dir()

    # A prior published set would mask "nothing was written", so the export
    # under test starts from an empty parent.
    for entry in sorted(parent.iterdir()):
        for member in sorted(entry.iterdir()):
            member.unlink()
        entry.rmdir()

    damage(workspace, branch_id, snapshot_id)

    with pytest.raises((IntegrityFailureError, BulletPackExportBlockedError)):
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    assert not published(workspace, branch_id).exists()
    assert list(parent.iterdir()) == []


def test_an_unknown_branch_is_a_selector_miss(workspace: Path) -> None:
    verified_branch(workspace)

    with pytest.raises(SelectorNotFoundError):
        export_bullet_pack(workspace, branch_name="no-such-branch")


def test_the_selector_resolves_a_current_branch_by_its_folded_name(
    workspace: Path,
) -> None:
    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)

    exported = export_bullet_pack(workspace, branch_name=BRANCH_NAME.upper())

    assert exported.branch_id == branch_id
    assert exported.branch_name == BRANCH_NAME


def test_the_hashed_bundle_is_id_ordered_while_the_pack_is_render_ordered(
    workspace: Path,
) -> None:
    """§13.14 rule 2: entries are ID-byte-ordered within each entity type.

    Render order belongs to `bullet_pack.md` and its companions. If it reached
    the hashed bundle, `render_input_sha256` would depend on which requirement
    each bullet matched, and another conforming implementation reading the
    same rows would compute a different digest.
    """

    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(
        workspace, texts=[SECOND_TEXT, BULLET_TEXT]
    )

    graph = branch_graph(workspace, branch_id)
    bundle = branch_render_input_bundle(graph)

    bundled = [entry.value.id for entry in bundle.resume_bullets]
    assert bundled == sorted(bundled, key=lambda value: value.encode("utf-8"))
    # Same rows, both orders present: the bundle carries every rendered bullet.
    assert set(bundled) == {item.value.id for item in graph.bullets}


def branch_citing_a_claim(workspace: Path):
    """One verified branch whose single bullet cites a real anchor member.

    The default fixture's bullets cite no claim, so anything about the cited
    subset — its gate, its counterevidence closure — needs this graph instead.
    """

    ids, facts, snapshot_id = prepare_anchor(workspace)
    with read_database(workspace) as connection:
        claims = list_self_claims_for_snapshot(connection, snapshot_id)
    cited = next(claim for claim in claims if claim.claim_kind != "narrative_summary")
    generated = run_stage10(
        workspace,
        FakeContractRunner(
            [
                writer_response(
                    [bullet_candidate(fact_ids=list(facts), claim_ids=[cited.id])]
                )
            ]
        ),
        ids,
        snapshot_id=snapshot_id,
    )
    run_stage11(
        workspace,
        FakeContractRunner(
            [verifier_response([finding(item) for item in generated.bullet_ids])]
        ),
        ids,
    )
    assert generated.branch_id is not None
    return claims, cited, snapshot_id, generated.branch_id


def plant_counterevidence(
    workspace: Path, claim_id: str, *, ref_type: str, ref_id: str
) -> None:
    """Give a stored claim one counterevidence reference of the caller's shape."""

    payload = [
        {
            "statement": "A later record narrows how far this claim reaches.",
            "source_ref_type": ref_type,
            "source_ref_id": ref_id,
        }
    ]
    with writer_database(workspace) as connection:
        connection.execute("DROP TRIGGER self_claims_lifecycle_update_guard")
        connection.execute(
            "UPDATE self_claims SET counterevidence_json = ? WHERE id = ?",
            (json.dumps(payload, separators=(",", ":")), claim_id),
        )
        connection.commit()


def test_a_cited_claim_off_supported_is_a_gate_refusal_not_an_integrity_failure(
    workspace: Path,
) -> None:
    """§14.14: membership is integrity, status is a §16.11 consumer gate.

    A claim a later Stage 7 pass moved off `supported` leaves a perfectly
    coherent graph — nothing disagrees with itself. Reporting class 7 would
    tell the owner their workspace is damaged when the honest answer is that
    the pack no longer clears the gate.
    """

    claims, cited, snapshot_id, _branch_id = branch_citing_a_claim(workspace)
    assert export_bullet_pack(workspace, branch_name=BRANCH_NAME).branch_id

    with writer_database(workspace) as connection:
        connection.execute("DROP TRIGGER self_claims_lifecycle_update_guard")
        connection.execute(
            "UPDATE self_claims SET verification_status = ? WHERE id = ?",
            ("needs_clarification", cited.id),
        )
        connection.execute(
            "UPDATE assessment_snapshots SET verification_status = ? WHERE id = ?",
            (
                aggregate_verification_status(
                    [
                        "needs_clarification"
                        if claim.id == cited.id
                        else claim.verification_status
                        for claim in claims
                    ]
                ),
                snapshot_id,
            ),
        )
        connection.commit()

    with pytest.raises(BulletPackExportBlockedError):
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)


def _damage_stale_aggregate(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    """A stored snapshot status that no longer reduces from its own claims."""

    with writer_database(workspace) as connection:
        connection.execute(
            "UPDATE assessment_snapshots SET verification_status = ? WHERE id = ?",
            ("partially_supported", snapshot_id),
        )
        connection.commit()


def _damage_claim_generation(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    """A member claim carrying another Stage 6 batch's provenance."""

    with writer_database(workspace) as connection:
        claims = list_self_claims_for_snapshot(connection, snapshot_id)
        connection.execute("DROP TRIGGER self_claims_lifecycle_update_guard")
        connection.execute(
            "UPDATE self_claims SET generation_id = ? WHERE id = ?",
            ("gen_vera_other_assessment", claims[0].id),
        )
        connection.commit()


def _damage_projection_collision(
    workspace: Path, branch_id: str, snapshot_id: str
) -> None:
    """Two byte-distinct stored texts that the §18 projection renders equal."""

    composed = "Documented the café deployment runbook."
    decomposed = unicodedata.normalize("NFD", composed)
    # The whole point of the case: distinct bytes, equal projection.
    assert composed != decomposed
    assert unicodedata.normalize("NFC", decomposed) == composed
    with writer_database(workspace) as connection:
        bullets = list_resume_bullets_for_branch(connection, branch_id)
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        for bullet, text in zip(bullets, (composed, decomposed)):
            connection.execute(
                "UPDATE resume_bullets SET text = ? WHERE id = ?", (text, bullet.id)
            )
        connection.commit()


@pytest.mark.parametrize(
    ("damage", "diagnostic"),
    [
        pytest.param(
            _damage_stale_aggregate,
            "snapshot_aggregate_mismatch",
            id="stale-aggregate",
        ),
        pytest.param(
            _damage_claim_generation,
            "snapshot_claim_generation_mismatch",
            id="claim-generation-mismatch",
        ),
    ],
)
def test_a_broken_anchor_is_refused_before_its_allowlist_verdict(
    workspace: Path, damage, diagnostic: str
) -> None:
    """§16.11's integrity half precedes its status half.

    Both damages leave a stored status inside the anchor allowlist. Trusting
    it would let a restored or migrated workspace license a pack off an
    aggregate that no claim set actually produces.
    """

    _ids, _facts, snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)

    damage(workspace, branch_id, snapshot_id)

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    # Pin the reason: an earlier check firing would make this test green while
    # leaving the integrity half of the gate unproven.
    assert str(raised.value) == diagnostic
    assert not published(workspace, branch_id).exists()


def test_texts_that_collide_only_after_projection_never_render_twice(
    workspace: Path,
) -> None:
    """§18: two IDs may not produce one logical line.

    Stage 10 refuses this collision before persistence, so reaching it means
    restored or migrated state. The exact-text check alone would pass here:
    the two stored values really are byte-distinct.
    """

    _ids, _facts, _snapshot_id, branch_id, _bullet_ids = verified_branch(
        workspace, texts=[BULLET_TEXT, SECOND_TEXT]
    )

    _damage_projection_collision(workspace, branch_id, _snapshot_id)

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    # Not `bullet_text_duplicate`: the stored values really are byte-distinct,
    # so only the projection check can catch this.
    assert str(raised.value) == "bullet_projection_collision"
    assert not published(workspace, branch_id).exists()


def test_an_uncited_anchor_member_still_moves_the_render_hash(
    workspace: Path,
) -> None:
    """§13.14 rule 2: the hash covers what *gates* a member, not only what
    renders one.

    §16.11's integrity half reduces the stored aggregate from every current
    member of the anchor, so an uncited claim is a gate input. Were it outside
    the hash, an edit that leaves the reduction on the same aggregate would
    recompute the published digest unchanged, and the set would stay current
    while a value the gate read had moved underneath it.
    """

    _ids, _facts, snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)
    export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    manifest_path = published(workspace, branch_id) / "manifest.json"
    before = json.loads(manifest_path.read_text("utf-8"))
    # The premise: no bullet here cites a claim, so every member is uncited and
    # nothing below could reach the hash through the rendered companions.
    assert before["source_ids"]["self_claim_ids"] == []

    with writer_database(workspace) as connection:
        claims = list_self_claims_for_snapshot(connection, snapshot_id)
        target = next(
            claim for claim in claims if claim.claim_kind != "narrative_summary"
        )
        connection.execute("DROP TRIGGER self_claims_lifecycle_update_guard")
        connection.execute(
            "UPDATE self_claims SET uncertainty = ? WHERE id = ?",
            ("A later pass narrowed what this claim rests on.", target.id),
        )
        connection.commit()

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    after = json.loads(manifest_path.read_text("utf-8"))

    # No status moved, so the gate still passes and every source list — the
    # cited-claim list included — is the same set as before.
    assert after["source_ids"] == before["source_ids"]
    assert after["members"] == before["members"]
    assert after["render_input_sha256"] != before["render_input_sha256"]


def test_a_cited_claims_counterevidence_target_joins_the_hashed_closure(
    workspace: Path,
) -> None:
    """§16.1: a supplemental row entering the export resolves its own chain.

    Counterevidence is the one reference kind that can leave a bullet pack's
    closure. Leaving the target unread would put only the reference string in
    the hash, so the row it points at could change — or stop being current —
    with the published set still recomputing as current.
    """

    _claims, cited, _snapshot_id, branch_id = branch_citing_a_claim(workspace)
    outside, _items = add_log(
        workspace,
        log_id="log_vera_counterevidence",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example recorded a later limit on the same work.",
        occurred=exact_day(16),
        item_specs=(("evi_vera_counterevidence", "manual_claim"),),
    )
    plant_counterevidence(workspace, cited.id, ref_type="raw_log", ref_id=outside.id)

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    manifest = json.loads(
        (published(workspace, branch_id) / "manifest.json").read_text("utf-8")
    )
    assert outside.id in manifest["source_ids"]["raw_log_ids"]
    graph = branch_graph(workspace, branch_id)
    # The closure field stays out of the rendered evidence map: only the hash
    # and the completeness lists widen.
    assert [item.id for item in graph.supplemental_raw_logs] == [outside.id]
    assert outside.id not in {item.id for item in graph.raw_logs}
    bundle = branch_render_input_bundle(graph)
    assert outside.id in {entry.value.id for entry in bundle.raw_logs}


def test_a_dangling_counterevidence_reference_stops_the_export(
    workspace: Path,
) -> None:
    """§13.3: a displaced or absent target is not a current source.

    The pack would otherwise publish a claim link whose counterevidence points
    at nothing, which reads to the owner as evidence that was weighed.
    """

    _claims, cited, _snapshot_id, branch_id = branch_citing_a_claim(workspace)
    plant_counterevidence(
        workspace, cited.id, ref_type="raw_log", ref_id="log_vera_never_captured"
    )

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert str(raised.value) == "export_source_reference_invalid"
    assert not published(workspace, branch_id).exists()


def test_a_partially_superseded_bullet_batch_never_exports(
    workspace: Path,
) -> None:
    """§12 rule 13: a branch and its bullets are swapped as one batch.

    `supersede_branches` is the only writer of either column and always marks
    the whole set, so a superseded bullet under a current branch is a
    half-applied swap. Selecting only current rows would publish the remainder
    as a complete pack — a bullet short, with nothing in the output saying so.
    """

    _ids, _facts, _snapshot_id, branch_id, bullet_ids = verified_branch(
        workspace, texts=[BULLET_TEXT, SECOND_TEXT]
    )

    with writer_database(workspace) as connection:
        connection.execute("DROP TRIGGER resume_bullets_lifecycle_update_guard")
        connection.execute(
            "UPDATE resume_bullets SET superseded_at = ? WHERE id = ?",
            ("2026-02-01T09:00:00+00:00", bullet_ids[0]),
        )
        connection.commit()

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert str(raised.value) == "bullet_batch_partially_superseded"
    assert not published(workspace, branch_id).exists()


def branch_over_a_partly_cited_anchor(workspace: Path):
    """A verified branch whose one bullet reaches only the first of two facts.

    Every member claim cites both, so the second fact is reachable only through
    the anchor's uncited side — the half no companion renders.
    """

    ids, facts, snapshot_id = prepare_paired_anchor(workspace)
    generated = run_stage10(
        workspace,
        FakeContractRunner(
            [writer_response([bullet_candidate(fact_ids=[facts[0]])])]
        ),
        ids,
        snapshot_id=snapshot_id,
    )
    run_stage11(
        workspace,
        FakeContractRunner(
            [verifier_response([finding(item) for item in generated.bullet_ids])]
        ),
        ids,
    )
    assert generated.branch_id is not None
    return facts, snapshot_id, generated.branch_id


def test_an_uncited_members_grounding_joins_the_hashed_closure(
    workspace: Path,
) -> None:
    """§13.14 rule 2: what the gate reads is hashed, but not rendered.

    The uncited member's fact is read to decide whether its `supported` verdict
    still stands, so it belongs to the completeness lists and the hash — and
    nowhere near the evidence map, which closes over rendered bullets alone.
    """

    facts, _snapshot_id, branch_id = branch_over_a_partly_cited_anchor(workspace)

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)

    graph = branch_graph(workspace, branch_id)
    assert [item.value.id for item in graph.facts] == [facts[0]]
    assert [item.value.id for item in graph.supplemental_facts] == [facts[1]]
    manifest = json.loads(
        (published(workspace, branch_id) / "manifest.json").read_text("utf-8")
    )
    assert sorted(manifest["source_ids"]["experience_fact_ids"]) == sorted(facts)
    bundle = branch_render_input_bundle(graph)
    assert {entry.value.id for entry in bundle.experience_facts} == set(facts)
    evidence_map = json.loads(
        (published(workspace, branch_id) / "evidence_map.json").read_text("utf-8")
    )
    assert facts[1] not in json.dumps(evidence_map)


def test_an_uncited_members_dead_fact_never_licenses_the_pack(
    workspace: Path,
) -> None:
    """§16.11: the aggregate is reduced from every member, so every member's
    grounding is a gate input.

    A claim whose fact is gone carries a verdict nothing supports any more.
    Left unchecked, the same snapshot would refuse `export assessment` as
    damaged while still licensing this pack.
    """

    facts, _snapshot_id, branch_id = branch_over_a_partly_cited_anchor(workspace)

    with writer_database(workspace) as connection:
        connection.execute("DROP TRIGGER experience_facts_lifecycle_update_guard")
        connection.execute(
            "UPDATE experience_facts SET superseded_at = ? WHERE id = ?",
            ("2026-02-01T09:00:00+00:00", facts[1]),
        )
        connection.commit()

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    # Not a bullet-side diagnostic: no bullet or cited claim reaches this fact.
    assert str(raised.value) == "anchor_claim_fact_not_current"
    assert not published(workspace, branch_id).exists()


def displace(workspace: Path, log_id: str, *, correction_id: str) -> str:
    """Plant a §13.3 correction over one log without the recompute it triggers.

    Capture would supersede everything downstream; a restored or migrated
    workspace can hold the correction with the derived rows still current.
    """

    correction, _items = add_log(
        workspace,
        log_id=correction_id,
        recorded_at=FIXED_NOW,
        raw_text="Vera Example corrected the earlier record.",
        occurred=exact_day(16),
        item_specs=((f"evi_{correction_id}", "manual_claim"),),
        corrects_log_id=log_id,
    )
    return correction.id


def branch_over_split_member_claims(workspace: Path):
    """Two facts under an anchor whose members cite one fact each.

    `prepare_paired_anchor` has every member cite both facts, which is exactly
    what hides a per-claim failure behind a sibling's evidence.
    """

    ids = VeraIds()
    facts = prepare_facts(workspace, ids, count=2)
    assessed = run_stage6(
        workspace,
        FakeContractRunner(
            [
                assessment_response(
                    fact_ids=[facts[0]], narrative_fact_ids=[facts[1]]
                )
            ]
        ),
        ids,
    )
    assert assessed.snapshot_id is not None
    anchor_snapshot(workspace, assessed.snapshot_id)
    plant_job_description(workspace)
    generated = run_stage10(
        workspace,
        FakeContractRunner(
            [writer_response([bullet_candidate(fact_ids=[facts[0]])])]
        ),
        ids,
        snapshot_id=assessed.snapshot_id,
    )
    run_stage11(
        workspace,
        FakeContractRunner(
            [verifier_response([finding(item) for item in generated.bullet_ids])]
        ),
        ids,
    )
    assert generated.branch_id is not None
    return facts, assessed.snapshot_id, generated.branch_id


def test_one_members_live_chain_never_answers_for_another_member(
    workspace: Path,
) -> None:
    """§16.1 is per row: *this* claim reaches one live chain.

    The chain helper returns on the first fact that reaches a retained log, so
    checking the union of every member's facts would let a healthy member's
    evidence license a member whose own facts are all displaced.
    """

    facts, _snapshot_id, branch_id = branch_over_split_member_claims(workspace)
    with read_database(workspace) as connection:
        summary_fact = get_experience_fact(connection, facts[1])
    assert summary_fact is not None
    displace(
        workspace,
        summary_fact.source_log_ids[0],
        correction_id="log_vera_displacing_summary",
    )

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    # The bullet and the other member still reach a retained log through the
    # first fact, so only a per-claim check can see this.
    assert str(raised.value) == "anchor_claim_direct_chain_missing"
    assert not published(workspace, branch_id).exists()


def test_an_uncited_members_dangling_counterevidence_stops_the_export(
    workspace: Path,
) -> None:
    """§16.1 over the whole anchor, not just the cited half.

    An uncited member carries the aggregate the gate reads and rides in the
    render hash, so its typed provenance has to still resolve too.
    """

    _ids, _facts, snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)
    with read_database(workspace) as connection:
        claims = list_self_claims_for_snapshot(connection, snapshot_id)
    uncited = next(
        claim for claim in claims if claim.claim_kind != "narrative_summary"
    )
    plant_counterevidence(
        workspace, uncited.id, ref_type="raw_log", ref_id="log_vera_never_captured"
    )

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert str(raised.value) == "export_source_reference_invalid"
    assert not published(workspace, branch_id).exists()


def branch_over_a_corrected_lineage(workspace: Path):
    """A branch whose facts already span a §13.3 correction lineage.

    `prepare_high_facts` gives each fact a displaced `design_doc` root and a
    retained correction, which is the one shape where a fact stays selectable
    with a displaced source in its own closure.
    """

    ids = VeraIds()
    facts, _root_item, _correction_item = prepare_high_facts(workspace, ids)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    assert assessed.snapshot_id is not None
    anchor_snapshot(workspace, assessed.snapshot_id)
    plant_job_description(workspace)
    generated = run_stage10(
        workspace,
        FakeContractRunner(
            [writer_response([bullet_candidate(fact_ids=[facts[0]])])]
        ),
        ids,
        snapshot_id=assessed.snapshot_id,
    )
    run_stage11(
        workspace,
        FakeContractRunner(
            [verifier_response([finding(item) for item in generated.bullet_ids])]
        ),
        ids,
    )
    assert generated.branch_id is not None
    return facts, generated.branch_id


def test_a_correction_the_chain_gate_consulted_moves_the_render_hash(
    workspace: Path,
) -> None:
    """§13.14 rule 2: the gate's inputs include the rows that answer it.

    §13.3 records displacement on the *correcting* log, so the retained-chain
    gate and §13.3's source-selection predicate both read rows no closure
    reaches. A correction planted over an already-displaced root leaves every
    rendered byte, every source ID, and both gate answers alone — and, if it
    stayed off the hashed surface, the published set would recompute as
    current with a row the gate consulted having appeared underneath it.
    """

    _facts, branch_id = branch_over_a_corrected_lineage(workspace)
    export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    manifest_path = published(workspace, branch_id) / "manifest.json"
    before = json.loads(manifest_path.read_text("utf-8"))

    correction_id = displace(
        workspace,
        "log_vera_signal_root",
        correction_id="log_vera_second_correction",
    )

    export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    after = json.loads(manifest_path.read_text("utf-8"))

    assert correction_id not in before["source_ids"]["raw_log_ids"]
    assert correction_id in after["source_ids"]["raw_log_ids"]
    assert after["render_input_sha256"] != before["render_input_sha256"]


def test_a_correction_that_strands_a_fact_stops_the_export(
    workspace: Path,
) -> None:
    """The same rows, read as §13.3 intends: a displacement that leaves a fact
    with no selectable source refuses the export rather than shrinking it."""

    facts, _snapshot_id, branch_id = branch_over_a_partly_cited_anchor(workspace)
    with read_database(workspace) as connection:
        supplemental = get_experience_fact(connection, facts[1])
    assert supplemental is not None
    displace(
        workspace,
        supplemental.source_log_ids[0],
        correction_id="log_vera_displacing_supplemental",
    )

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert str(raised.value) == "fact_source_selection_invalid"
    assert not published(workspace, branch_id).exists()


@pytest.mark.parametrize(
    "column, diagnostic",
    [
        ("gap_question_ids_json", "snapshot_gap_reference_invalid"),
        ("contradiction_ids_json", "snapshot_contradiction_reference_invalid"),
    ],
)
def test_an_anchors_unresolvable_typed_reference_stops_the_export(
    workspace: Path, column: str, diagnostic: str
) -> None:
    """§11.7: a read-time consumer re-resolves the snapshot's reference lists.

    Persisted IDs are not evidence that the rows still exist. The bullet pack
    renders neither list, but it accepts the snapshot as its anchor, and an
    anchor whose own typed graph no longer resolves is damaged state.
    """

    _ids, _facts, snapshot_id, branch_id, _bullet_ids = verified_branch(workspace)

    with writer_database(workspace) as connection:
        connection.execute("DROP TRIGGER assessment_snapshots_lifecycle_update_guard")
        connection.execute(
            f"UPDATE assessment_snapshots SET {column} = ? WHERE id = ?",
            (json.dumps(["missing_vera_reference"]), snapshot_id),
        )
        connection.commit()

    with pytest.raises(IntegrityFailureError) as raised:
        export_bullet_pack(workspace, branch_name=BRANCH_NAME)
    assert str(raised.value) == diagnostic
    assert not published(workspace, branch_id).exists()
