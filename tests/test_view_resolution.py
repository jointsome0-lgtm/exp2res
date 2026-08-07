"""Offline §30 resolution tests: outcomes, projections, and revalidation.

Every test drives `exp2res.services.views.resolve` directly over a real Vera
Example workspace built by the offline pipeline, so the whole state-dependent
half of §21.57 is exercised without a socket. The transport-dependent half —
framing, authority, methods, admission, deadlines measured across real
sockets — belongs to the §14.17 command and its own tests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time

import pytest

from exp2res.exports.managed import (
    AssessmentManifest,
    manifest_bytes,
    read_current_assessment_members,
)
import exp2res.exports.managed as managed
from exp2res.services.capture import capture_gap_answer
from exp2res.services.export import export_assessment
from exp2res.services.views import MIRROR_ROUTE, QUESTIONS_ROUTE, resolve
import exp2res.services.views as views
from exp2res.storage.workspace import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_BUSY_TIMEOUT_MS,
    read_database,
    writer_database,
)

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage4_detection import run_stage4
from assessment_helpers import VeraIds, prepare_facts
from test_stage6_assessment import AssessmentIds, assessment_response, run_stage6
from test_stage7_verification import run_stage7, verifier_response


pytestmark = [pytest.mark.lifecycle]

MEMBERS = ("evidence_map.json", "report.html", "report.md", "self_claims.json")


def deadline(seconds: float = 30.0) -> float:
    return time.monotonic() + seconds


def gap_detection(fact_ids: list[str]) -> bytes:
    """Two open questions and no contradiction, so the mirror has unknowns."""

    return json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "experience_fact",
                    "target_id": fact_id,
                    "question": question,
                    "reason": "missing_scale",
                    "priority": "medium",
                }
                for fact_id, question in zip(
                    fact_ids,
                    (
                        "What scale did you validate that renderer at?",
                        "Who else relied on that pipeline?",
                    ),
                )
            ],
            "contradictions": [],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def exported_workspace(workspace: Path) -> str:
    """One current, export-eligible, published global assessment view."""

    ids = VeraIds()
    facts = prepare_facts(workspace, ids, count=2)
    # Stage 4 first: a later detection generation supersedes claims, so the
    # gaps this snapshot copies must already exist.
    run_stage4(workspace, FakeContractRunner([gap_detection(list(facts))]), ids)
    generated = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts))]),
        ids,
    )
    assert generated.snapshot_id is not None
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    export_assessment(workspace, snapshot_id=generated.snapshot_id)
    return generated.snapshot_id


def project_snapshot(workspace: Path) -> str:
    """One current project-scoped view beside the global one."""

    with read_database(workspace) as connection:
        facts = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM experience_facts WHERE superseded_at IS NULL"
            )
        ]
    generated = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=facts)]),
        AssessmentIds(),
        scope="project",
        target="Vera Example Project",
    )
    assert generated.snapshot_id is not None
    return generated.snapshot_id


def final_set(workspace: Path, snapshot_id: str) -> Path:
    return workspace / "out" / "assessment" / snapshot_id


def member(workspace: Path, snapshot_id: str, name: str) -> bytes:
    return (final_set(workspace, snapshot_id) / name).read_bytes()


def rewrite_companion(workspace: Path, snapshot_id: str, payload: bytes) -> None:
    """Replace `self_claims.json` and keep its manifest digest matching.

    §30 rule 7's `question_companion_invalid` row exists only for this state:
    complete current-output revalidation succeeds because the member matches
    its recorded digest, and the closed §13.12 schema then fails.
    """

    directory = final_set(workspace, snapshot_id)
    (directory / "self_claims.json").write_bytes(payload)
    (directory / "self_claims.json").chmod(0o600)
    manifest = AssessmentManifest.model_validate_json(
        (directory / "manifest.json").read_bytes()
    )
    updated = manifest.model_copy(
        update={
            "members": [
                item.model_copy(update={"sha256": hashlib.sha256(payload).hexdigest()})
                if item.name == "self_claims.json"
                else item
                for item in manifest.members
            ]
        }
    )
    (directory / "manifest.json").write_bytes(manifest_bytes(updated))
    (directory / "manifest.json").chmod(0o600)


def update_snapshot(workspace: Path, snapshot_id: str, column: str, value) -> None:
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"UPDATE assessment_snapshots SET {column} = ? WHERE id = ?",
            (value, snapshot_id),
        )
        connection.commit()


def update_one_claim(workspace: Path, snapshot_id: str, column: str, value) -> None:
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        claim_id = connection.execute(
            "SELECT id FROM self_claims WHERE snapshot_id = ? AND claim_kind != "
            "'narrative_summary' ORDER BY id LIMIT 1",
            (snapshot_id,),
        ).fetchone()[0]
        connection.execute(
            f"UPDATE self_claims SET {column} = ? WHERE id = ?", (value, claim_id)
        )
        connection.commit()


def mirror(workspace: Path, query: bytes | None, **kwargs):
    return resolve(workspace, MIRROR_ROUTE, query, deadline=deadline(), **kwargs)


def questions(workspace: Path, query: bytes | None, **kwargs):
    return resolve(workspace, QUESTIONS_ROUTE, query, deadline=deadline(), **kwargs)


def test_both_selector_forms_serve_the_revalidated_member_bytes(
    workspace: Path,
) -> None:
    snapshot_id = exported_workspace(workspace)
    published = member(workspace, snapshot_id, "report.html")

    by_identity = mirror(workspace, b"scope=global")
    by_id = mirror(workspace, f"snapshot={snapshot_id}".encode("ascii"))
    repeated = mirror(workspace, b"scope=global")

    for page in (by_identity, by_id, repeated):
        assert page.outcome == "served"
        assert page.status == 200
        # Byte-identical: the view serves what §13.14 revalidation verified,
        # never a second rendering of the same projection.
        assert page.body == published
        assert page.published_member is True
        assert page.content_type == "text/html; charset=utf-8"
        # The served body carries no outcome metadata at all.
        assert b"Exp2Res-View-Outcome" not in page.body
        assert b"served" not in page.body


def test_questions_project_only_unanswered_question_values(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    unknowns = companion["unknowns"]
    assert unknowns, "the fixture must publish at least one open question"

    page = questions(workspace, b"scope=global")
    by_id = questions(workspace, f"snapshot={snapshot_id}".encode("ascii"))
    text = page.body.decode("utf-8")

    assert page.outcome == "served"
    assert page.status == 200
    assert page.published_member is False
    assert by_id.body == page.body
    for unknown in unknowns:
        assert (unknown["question"] in text) is not unknown["answered"]
        # No gap ID, target, reason, or priority reaches the page.
        assert unknown["id"] not in text
        assert unknown["target_id"] not in text
        assert unknown["reason"] not in text
        assert unknown["priority"] not in text
    for claim in companion["claims"]:
        assert claim["claim"] not in text
    assert snapshot_id not in text
    assert str(workspace) not in text
    assert "self_claims.json" not in text


def test_a_gap_answered_after_synthesis_leaves_the_page(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    target = companion["unknowns"][0]
    survivor = companion["unknowns"][1]

    # The ordinary answer path: a new RawLog, and the published set invalidated
    # until the owner re-exports.
    capture_gap_answer(
        workspace,
        gap_id=target["id"],
        raw_text="Vera Example answered that question in a diary note.",
        artifacts=(),
    )
    invalidated = questions(workspace, b"scope=global")
    export_assessment(workspace, snapshot_id=snapshot_id)
    after = questions(workspace, b"scope=global")

    assert invalidated.outcome == "export_not_current"
    text = after.body.decode("utf-8")
    assert after.outcome == "served"
    assert target["question"] not in text
    assert survivor["question"] in text


def test_invalidation_is_observed_by_the_next_request_and_names_the_export(
    workspace: Path,
) -> None:
    snapshot_id = exported_workspace(workspace)
    served = mirror(workspace, b"scope=global")
    assert served.outcome == "served"

    gap_id = json.loads(member(workspace, snapshot_id, "self_claims.json"))["unknowns"][
        0
    ]["id"]
    capture_gap_answer(
        workspace,
        gap_id=gap_id,
        raw_text="Vera Example answered that question in a diary note.",
        artifacts=(),
    )

    for route in (MIRROR_ROUTE, QUESTIONS_ROUTE):
        page = resolve(workspace, route, b"scope=global", deadline=deadline())
        assert page.outcome == "export_not_current"
        assert page.status == 409
        assert b"export assessment" in page.body
        assert snapshot_id.encode("ascii") in page.body
        assert served.body not in page.body

    # Nothing is cached: republishing makes the next request serve again.
    export_assessment(workspace, snapshot_id=snapshot_id)
    assert mirror(workspace, b"scope=global").outcome == "served"


def test_a_stale_but_replaceable_set_is_export_not_current(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    # A rendered claim field moves without touching the aggregate or the
    # narrative-summary invariant, so §13.14's render-input hash is the only
    # thing that no longer matches the published manifest.
    update_one_claim(
        workspace, snapshot_id, "uncertainty", "Vera Example remains uncertain here."
    )

    page = mirror(workspace, b"scope=global")

    assert page.outcome == "export_not_current"
    assert final_set(workspace, snapshot_id).is_dir()


def test_an_empty_question_projection_is_a_valid_served_document(
    workspace: Path,
) -> None:
    snapshot_id = exported_workspace(workspace)
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    for unknown in companion["unknowns"]:
        capture_gap_answer(
            workspace,
            gap_id=unknown["id"],
            raw_text=f"Vera Example answered {unknown['question']}",
            artifacts=(),
        )
    export_assessment(workspace, snapshot_id=snapshot_id)

    page = questions(workspace, b"scope=global")
    text = page.body.decode("utf-8")

    assert page.outcome == "served"
    assert page.status == 200
    assert text.startswith("<!DOCTYPE html>")
    assert text.rstrip().endswith("</html>")
    assert "Open Questions" in text
    for unknown in companion["unknowns"]:
        assert unknown["question"] not in text


def test_a_question_value_cannot_carry_markup_into_the_page(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    hostile = '<img src=https://example.invalid/q onerror=alert(1)> & "quoted"'
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE gap_questions SET question = ? WHERE id = ?",
            (hostile, companion["unknowns"][0]["id"]),
        )
        connection.commit()
    export_assessment(workspace, snapshot_id=snapshot_id)

    page = questions(workspace, b"scope=global")
    text = page.body.decode("utf-8")

    assert page.outcome == "served"
    assert "&lt;img src=https://example.invalid/q onerror=alert(1)&gt;" in text
    assert "<img" not in text
    assert "onerror" not in text.replace("onerror=alert(1)&gt;", "")
    assert "&amp; &quot;quoted&quot;" in text


def test_a_schema_invalid_companion_is_not_an_internal_error(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    published = member(workspace, snapshot_id, "report.html")
    companion = json.loads(member(workspace, snapshot_id, "self_claims.json"))
    companion["unexpected_field"] = "Vera Example"
    rewrite_companion(
        workspace,
        snapshot_id,
        json.dumps(companion, separators=(",", ":")).encode("utf-8") + b"\n",
    )

    page = questions(workspace, b"scope=global")
    still_served = mirror(workspace, b"scope=global")

    assert page.outcome == "question_companion_invalid"
    assert page.status == 409
    assert b"export assessment" in page.body
    assert b"Vera Example" not in page.body
    # Revalidation succeeded, so only the projection fails: the mirror route
    # never reads the companion at all.
    assert still_served.outcome == "served"
    assert still_served.body == published


@pytest.mark.parametrize(
    "corruption",
    ["truncated_manifest", "removed_member", "tampered_member", "symlinked_out_root"],
)
def test_state_no_re_export_can_replace_is_the_residual_outcome(
    workspace: Path, corruption: str
) -> None:
    snapshot_id = exported_workspace(workspace)
    directory = final_set(workspace, snapshot_id)
    if corruption == "truncated_manifest":
        (directory / "manifest.json").write_bytes(b"{")
    elif corruption == "removed_member":
        (directory / "report.md").unlink()
    elif corruption == "tampered_member":
        tampered = directory / "report.html"
        tampered.write_bytes(tampered.read_bytes() + b"<!-- tampered -->")
    else:
        moved = workspace / "elsewhere"
        (workspace / "out").rename(moved)
        (workspace / "out").symlink_to(moved, target_is_directory=True)

    page = mirror(workspace, b"scope=global")

    assert page.outcome == "export_residual"
    assert page.status == 409
    assert b"tampered" not in page.body
    assert str(workspace).encode("utf-8") not in page.body


def test_an_absent_managed_root_is_replaceable_output_not_a_migration(
    workspace: Path,
) -> None:
    """§30: managed output that is simply missing is `export_not_current`.

    The §8.1 read gate refuses an absent `out/` root exactly as it refuses an
    unreadable schema, but the schema here is one this build reads and the
    §14.9 export puts the root back. Telling the owner to migrate would send
    them at the wrong thing, and the residual outcome would claim a repair no
    re-export can perform.
    """

    snapshot_id = exported_workspace(workspace)
    shutil.rmtree(workspace / "out")

    page = mirror(workspace, b"scope=global")

    assert page.outcome == "export_not_current"
    assert page.status == 409
    assert b"schema" not in page.body.lower()
    # Classified in phase 2, so it still carries the remedy for this snapshot.
    assert snapshot_id.encode("ascii") in page.body


def test_an_absent_managed_root_preserves_an_available_schema_migration(
    workspace: Path,
) -> None:
    exported_workspace(workspace)
    shutil.rmtree(workspace / "out")
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        connection.execute(
            "UPDATE schema_meta SET version = ? WHERE version = ?",
            (CURRENT_SCHEMA_VERSION - 1, CURRENT_SCHEMA_VERSION),
        )

    page = mirror(workspace, b"scope=global")

    assert page.outcome == "schema_incompatible"
    assert page.status == 409
    assert b"exp2res db migrate" in page.body
    assert b"exp2res db status" not in page.body


def test_an_absent_managed_root_does_not_pre_empt_the_snapshot_outcomes(
    workspace: Path,
) -> None:
    """§30's ordering survives a missing root: business state answers first.

    Refusing in front of the read would report missing output for a workspace
    whose real answer is that it has no current assessment view at all.
    """

    shutil.rmtree(workspace / "out")

    page = mirror(workspace, b"scope=global")

    assert page.outcome == "no_current_view"
    assert page.status == 404


def test_a_final_entry_replaced_mid_read_is_export_changed(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = exported_workspace(workspace)
    directory = final_set(workspace, snapshot_id)
    real_read = managed._read_regular
    swapped: list[bool] = []

    def swapping_read(path: Path, out_root: Path) -> bytes:
        data = real_read(path, out_root)
        if not swapped:
            # One concurrent ordinary publication: the same complete bytes at
            # the same path, as a different entry.
            swapped.append(True)
            replacement = directory.parent / ".replacement"
            shutil.copytree(directory, replacement)
            shutil.rmtree(directory)
            replacement.rename(directory)
        return data

    monkeypatch.setattr(managed, "_read_regular", swapping_read)
    page = mirror(workspace, b"scope=global")

    assert swapped
    assert page.outcome == "export_changed"
    assert page.status == 409
    # The replacement may already be healthy current output, so the page gives
    # no removal or repair instruction.
    assert b"Remove or repair" not in page.body
    # A later request revalidates the observed entry from the beginning.
    monkeypatch.undo()
    assert mirror(workspace, b"scope=global").outcome == "served"


def test_an_entry_replaced_before_its_first_check_is_export_changed(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = exported_workspace(workspace)
    directory = final_set(workspace, snapshot_id)
    real_is_dir = managed._is_real_dir
    swapped: list[bool] = []

    def swapping_is_dir(path: Path) -> bool:
        if path == directory and not swapped:
            # The entry moves between being identified and the very first
            # observation made of it, and what takes its place is not even a
            # directory — the shape rule 6 skips.
            swapped.append(True)
            replacement = directory.parent / ".replacement"
            shutil.copytree(directory, replacement)
            shutil.rmtree(directory)
            directory.symlink_to(replacement, target_is_directory=True)
        return real_is_dir(path)

    monkeypatch.setattr(managed, "_is_real_dir", swapping_is_dir)
    page = mirror(workspace, b"scope=global")

    assert swapped
    # Every observation after the entry is identified is guarded: a path that
    # merely changed under the reader is never manual-repair state.
    assert page.outcome == "export_changed"
    assert b"Remove or repair" not in page.body


def test_the_integrity_half_runs_before_the_status_half(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    # A stored aggregate that no longer reduces from its own claims, whose
    # status also fails the §16.11 export allowlist.
    update_snapshot(workspace, snapshot_id, "verification_status", "unsupported")

    by_identity = mirror(workspace, b"scope=global")
    by_id = mirror(workspace, f"snapshot={snapshot_id}".encode("ascii"))

    for page in (by_identity, by_id):
        assert page.outcome == "assessment_inconsistent"
        assert page.status == 409
        # The remedy recomputes the aggregate on exactly this snapshot.
        assert b"assess verify" in page.body
        assert snapshot_id.encode("ascii") in page.body


def test_a_missing_narrative_summary_names_a_selector_specific_remedy(
    workspace: Path,
) -> None:
    snapshot_id = exported_workspace(workspace)
    update_snapshot(workspace, snapshot_id, "summary", "Vera Example rewrote this.")

    by_identity = mirror(workspace, b"scope=global")
    by_id = mirror(workspace, f"snapshot={snapshot_id}".encode("ascii"))

    assert by_identity.outcome == "assessment_inconsistent"
    assert b"assess generate" in by_identity.body
    assert by_id.outcome == "assessment_inconsistent"
    # Generation creates a new ID, so it never repairs this exact-ID URL.
    assert b"assess generate" not in by_id.body
    assert b"assess list" in by_id.body


@pytest.mark.parametrize(
    ("column", "value"),
    [
        # A claim the §13.6 swap left behind in another generation.
        ("generation_id", "01JAAAAAAAAAAAAAAAAAAAAAAA"),
        # A member claim that is no longer current at all.
        ("superseded_at", "2026-02-02T09:00:00+00:00"),
        # A stored row this build can no longer hydrate.
        ("metadata_json", "{"),
    ],
)
def test_a_broken_claim_graph_is_a_named_refusal_not_an_internal_error(
    workspace: Path, column: str, value: str
) -> None:
    snapshot_id = exported_workspace(workspace)
    update_one_claim(workspace, snapshot_id, column, value)

    by_identity = mirror(workspace, b"scope=global")
    by_id = mirror(workspace, f"snapshot={snapshot_id}".encode("ascii"))

    for page in (by_identity, by_id):
        # Broken stored state no corrected request repairs is rule 7's own
        # 409 row, never the unexpected-failure one.
        assert page.outcome == "assessment_inconsistent"
        assert page.status == 409
        # The refusal names the class and its remedy, never stored detail.
        assert column.encode("ascii") not in page.body
        assert str(workspace).encode("utf-8") not in page.body
    # Claim membership belongs to generation, which creates a new ID.
    assert b"assess generate" in by_identity.body
    assert b"assess generate" not in by_id.body
    assert b"assess list" in by_id.body


def test_an_unrelated_broken_snapshot_cannot_block_the_global_view(
    workspace: Path,
) -> None:
    snapshot_id = exported_workspace(workspace)
    project = project_snapshot(workspace)
    # A current project-scoped row this build cannot hydrate at all. V1 never
    # serves it, so it must not decide the outcome of the view that is served.
    update_snapshot(workspace, project, "metadata_json", "{")

    page = mirror(workspace, b"scope=global")

    assert page.outcome == "served"
    assert page.body == member(workspace, snapshot_id, "report.html")


def test_a_duplicated_current_identity_names_no_remedy(workspace: Path) -> None:
    exported_workspace(workspace)
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE assessment_snapshots SET scope_target = NULL, scope = 'global' "
            "WHERE scope = 'project'"
        )
        connection.commit()
    project = project_snapshot(workspace)
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE assessment_snapshots SET scope = 'global', scope_target = NULL "
            "WHERE id = ?",
            (project,),
        )
        connection.commit()

    page = mirror(workspace, b"scope=global")

    assert page.outcome == "assessment_inconsistent"
    assert page.status == 409
    # §13.6 is not a corruption-repair surface, so no command is named.
    assert b"Run this command" not in page.body


@pytest.mark.parametrize(
    ("status", "identity_remedy", "id_remedy"),
    [
        ("unverified", b"assess verify", b"assess verify"),
        ("unsupported", b"assess generate", b"assess list"),
    ],
)
def test_a_blocked_assessment_is_a_completed_semantic_refusal(
    workspace: Path, status: str, identity_remedy: bytes, id_remedy: bytes
) -> None:
    snapshot_id = exported_workspace(workspace)
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        # Both halves stay consistent: every claim and the aggregate move
        # together, so only §16.11's status half can refuse.
        connection.execute(
            "UPDATE self_claims SET verification_status = ? WHERE snapshot_id = ?",
            (status, snapshot_id),
        )
        connection.execute(
            "UPDATE assessment_snapshots SET verification_status = ? WHERE id = ?",
            (status, snapshot_id),
        )
        connection.commit()
    # The published set is stale as well: rule 7 orders the gate first, so the
    # 403 wins over the 409 the export would otherwise name.
    (final_set(workspace, snapshot_id) / "report.md").write_bytes(b"stale")

    by_identity = mirror(workspace, b"scope=global")
    by_id = mirror(workspace, f"snapshot={snapshot_id}".encode("ascii"))

    for page in (by_identity, by_id):
        assert page.outcome == "assessment_blocked"
        assert page.status == 403
        assert b"export assessment" not in page.body
    assert identity_remedy in by_identity.body
    assert id_remedy in by_id.body


def test_no_current_view_never_falls_back_to_history(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    update_snapshot(
        workspace, snapshot_id, "superseded_at", FIXED_NOW.isoformat()
    )

    by_identity = mirror(workspace, b"scope=global")
    by_id = mirror(workspace, f"snapshot={snapshot_id}".encode("ascii"))

    for page in (by_identity, by_id):
        assert page.outcome == "no_current_view"
        assert page.status == 404
    assert b"assess generate" in by_identity.body
    # No stored row names the view an exact-ID requester meant.
    assert b"assess generate" not in by_id.body
    assert b"assess list" in by_id.body


@pytest.mark.parametrize(
    "query",
    [
        None,
        b"",
        b"scope",
        b"scope=",
        b"scope=Global",
        b"scope=global&scope=global",
        b"scope=global&snapshot=snapshot_vera_0001",
        b"scope=global&extra=1",
        b"view=global",
        b"%73cope=global",
        b"snapshot=",
        b"snapshot=../../etc/passwd",
        b"snapshot=Snapshot%20Vera",
        b"snapshot=snapshot%2",
        b"snapshot=%ff",
        b"snapshot=" + b"a" * 129,
    ],
)
def test_a_nonmatching_selector_is_refused_without_reflecting_the_request(
    workspace: Path, query: bytes | None
) -> None:
    exported_workspace(workspace)
    page = mirror(workspace, query)

    assert page.outcome == "invalid_selector"
    assert page.status == 400
    for fragment in (b"passwd", b"Snapshot", b"view=global", b"extra", b"%73cope"):
        assert fragment not in page.body


@pytest.mark.parametrize("snapshot_id", [b"a", b"a" * 128])
def test_a_grammar_valid_id_reaches_ordinary_resolution(
    workspace: Path, snapshot_id: bytes
) -> None:
    exported_workspace(workspace)
    page = mirror(workspace, b"snapshot=" + snapshot_id)

    assert page.outcome == "no_current_view"
    assert page.status == 404
    if len(snapshot_id) > 1:
        assert snapshot_id not in page.body


def test_a_double_encoded_selector_resolves_to_nothing(workspace: Path) -> None:
    snapshot_id = exported_workspace(workspace)
    # One decoding pass yields the literal escape text, which is not the ID.
    page = mirror(workspace, b"snapshot=%25" + snapshot_id.encode("ascii")[1:])

    assert page.outcome == "invalid_selector"


@pytest.mark.parametrize(
    "query", [b"scope=project", b"scope=project&project=Vera%20Example%20Project"]
)
def test_the_deferred_project_form_is_refused_as_deferred(
    workspace: Path, query: bytes
) -> None:
    exported_workspace(workspace)
    page = mirror(workspace, query)

    assert page.outcome == "invalid_selector"
    assert b"deferred" in page.body
    assert b"Vera Example Project" not in page.body


def test_a_project_scoped_snapshot_is_not_reachable_through_its_id(
    workspace: Path,
) -> None:
    exported_workspace(workspace)
    project_id = project_snapshot(workspace)

    page = mirror(workspace, f"snapshot={project_id}".encode("ascii"))
    question_page = questions(workspace, f"snapshot={project_id}".encode("ascii"))

    for answer in (page, question_page):
        assert answer.outcome == "invalid_selector"
        assert answer.status == 400
        assert b"deferred" in answer.body
        assert project_id.encode("ascii") not in answer.body


@pytest.mark.parametrize("route", [b"/", b"/mirror/", b"/questions/x", b"/report.html"])
def test_a_route_outside_the_closed_set_is_refused_before_state(
    workspace: Path, route: bytes
) -> None:
    page = resolve(workspace, route, b"scope=global", deadline=deadline())

    assert page.outcome == "route_not_found"
    assert page.status == 404
    assert b"scope=global" not in page.body
    if route != b"/":
        assert route not in page.body


def test_an_expired_processing_budget_opens_no_read_transaction(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported_workspace(workspace)

    def refuse_to_read(*_arguments, **_keywords):  # pragma: no cover - must not run
        raise AssertionError("the read transaction must not open")

    monkeypatch.setattr("exp2res.services.views.read_database", refuse_to_read)
    page = resolve(
        workspace,
        MIRROR_ROUTE,
        b"scope=global",
        deadline=time.monotonic() + 2 * DEFAULT_BUSY_TIMEOUT_MS / 1000 - 0.5,
    )

    assert page.outcome == "processing_timeout"
    assert page.status == 503


@pytest.mark.parametrize(
    "query",
    [
        # The served projection…
        b"scope=global",
        # …and a refusal, which is a composed row exactly like it.
        b"scope=nonsense",
    ],
)
def test_a_budget_that_expires_during_composition_is_the_timeout(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, query: bytes
) -> None:
    exported_workspace(workspace)
    expiry = time.monotonic() + 1.0
    real_render = views.render_html
    composed: list[bool] = []

    def slow_render(document):
        body = real_render(document)
        composed.append(True)
        # The projection itself outlives the budget it was composed under.
        time.sleep(max(0.0, expiry - time.monotonic()) + 0.05)
        return body

    monkeypatch.setattr(views, "render_html", slow_render)
    page = resolve(
        workspace, QUESTIONS_ROUTE, query, deadline=expiry, busy_timeout_ms=50
    )

    assert composed, "the outcome must actually have been composed"
    # §30 rule 7: a row is this request's outcome only once it is fully
    # determined *and* composed, so an expired budget wins over the document.
    assert page.outcome == "processing_timeout"
    assert page.status == 503
    assert page.published_member is False


def test_sqlite_contention_beyond_the_bounded_wait_is_workspace_busy(
    workspace: Path,
) -> None:
    exported_workspace(workspace)
    blocker = sqlite3.connect(
        workspace / ".exp2res" / "exp2res.sqlite", isolation_level=None
    )
    try:
        blocker.execute("PRAGMA locking_mode = EXCLUSIVE")
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("PRAGMA user_version = 1")
        page = resolve(
            workspace,
            MIRROR_ROUTE,
            b"scope=global",
            deadline=time.monotonic() + 3 * 0.2,
            busy_timeout_ms=200,
        )
    finally:
        blocker.rollback()
        blocker.close()

    assert page.outcome == "workspace_busy"
    assert page.status == 503
    # The advisory writer lock is never consulted and never named.
    assert b"writer lock" not in page.body


def test_serving_writes_no_row_and_holds_no_writer_lock(workspace: Path) -> None:
    exported_workspace(workspace)
    with read_database(workspace) as connection:
        before = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM processing_runs), "
                "(SELECT count(*) FROM llm_calls)"
            ).fetchone()
        )

    assert mirror(workspace, b"scope=global").outcome == "served"
    # A business writer can take the workspace while the view is readable.
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.commit()
    assert questions(workspace, b"scope=global").outcome == "served"

    with read_database(workspace) as connection:
        after = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM processing_runs), "
                "(SELECT count(*) FROM llm_calls)"
            ).fetchone()
        )
    assert after == before


def test_the_connection_registrar_scopes_exactly_the_read_transaction(
    workspace: Path,
) -> None:
    exported_workspace(workspace)
    events: list[str] = []

    class _Registrar:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __enter__(self):
            events.append("registered")
            return self

        def __exit__(self, *_exception) -> None:
            events.append("released")

    page = resolve(
        workspace,
        MIRROR_ROUTE,
        b"scope=global",
        deadline=deadline(),
        register_connection=_Registrar,
    )

    assert page.outcome == "served"
    assert events == ["registered", "released"]


def test_the_reader_returns_the_bytes_whose_digests_it_verified(
    workspace: Path,
) -> None:
    snapshot_id = exported_workspace(workspace)
    with read_database(workspace) as connection:
        from exp2res.exports.graph import load_assessment_graph, load_current_snapshot

        snapshot_row, snapshot = load_current_snapshot(connection, snapshot_id)
        graph = load_assessment_graph(
            connection, snapshot_row=snapshot_row, snapshot=snapshot
        )
    read = read_current_assessment_members(workspace, graph)

    assert read.status == "current"
    assert read.members is not None
    assert set(read.members) == set(MEMBERS)
    for name, data in read.members.items():
        assert data == member(workspace, snapshot_id, name)
        assert hashlib.sha256(data).hexdigest() == next(
            row["sha256"]
            for row in json.loads(member(workspace, snapshot_id, "manifest.json"))[
                "members"
            ]
            if row["name"] == name
        )
