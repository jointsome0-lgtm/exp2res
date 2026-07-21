"""Offline §13.6 assessment contract, view selection, and lifecycle tests."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sqlite3

import pytest

from exp2res.domain.models import CounterevidenceItem, SelfClaim
from exp2res.errors import EmptyAssessmentViewError, IntegrityFailureError, LLMInvocationError
from exp2res.pipeline.stage6 import assessment_view_key, run_assessment_generation
from exp2res.storage.repository import (
    insert_self_claim,
    insert_assessment_snapshot,
    list_assessment_snapshots,
    list_self_claims_for_snapshot,
)
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import (
    SELECTION,
    add_log,
    budgets,
    empty_response,
    exact_day,
    fact_response,
    run_stage3,
)
from test_stage5_signals import SignalIds, prepare_facts, run_stage5, signal_response
from test_stage4_detection import detector_response, run_stage4
from exp2res.services.logs import delete_log


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


class AssessmentIds:
    __test__ = False

    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        self.counts[kind] += 1
        return f"{kind}_vera_assess_{self.counts[kind]:04d}"


def assessment_response(
    *,
    fact_ids: list[str],
    signal_ids: list[str],
    narrative_count: int = 1,
    confidence: str = "medium",
) -> bytes:
    claims = [
        {
            "claim": "Vera Example currently shows a provenance-aware working pattern.",
            "claim_kind": "pattern_signal",
            "dimension": "working_style",
            "source_signal_ids": signal_ids,
            "source_fact_ids": fact_ids,
            "confidence": confidence,
            "uncertainty": "Vera Example evidence remains limited to supplied records.",
        }
    ]
    for index in range(narrative_count):
        claims.append(
            {
                "claim": f"Current evidence suggests Vera Example pattern {index + 1}.",
                "claim_kind": "narrative_summary",
                "dimension": "trajectory",
                "source_signal_ids": signal_ids,
                "source_fact_ids": fact_ids,
                "confidence": confidence,
                "uncertainty": None,
            }
        )
    return json.dumps(
        {"self_claims": claims, "warnings": []}, separators=(",", ":")
    ).encode("utf-8")


def prepare_graph(workspace: Path):
    ids = SignalIds()
    fact_ids = prepare_facts(workspace, ids)
    signals = run_stage5(
        workspace,
        FakeContractRunner([signal_response(list(fact_ids))]),
        ids,
    ).current_signals
    return ids, fact_ids, tuple(item.id for item in signals)


def run_stage6(
    workspace: Path,
    fake: FakeContractRunner,
    ids,
    *,
    scope: str = "global",
    target: str | None = None,
):
    return run_assessment_generation(
        workspace,
        scope=scope,
        scope_target=target,
        selection=SELECTION,
        budgets=budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def plant_assessment_set(workspace: Path, snapshot_id: str) -> Path:
    parent = workspace / "out" / "assessment"
    parent.mkdir(mode=0o700, exist_ok=True)
    path = parent / snapshot_id
    path.mkdir(mode=0o700)
    (path / "Vera Example stale member").write_text(
        "Vera Example stale member\n", encoding="utf-8"
    )
    return path


def test_global_happy_path_shares_generation_and_summary(workspace: Path) -> None:
    ids, facts, signals = prepare_graph(workspace)
    result = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
        ),
        ids,
    )
    assert result.snapshot is not None
    assert result.snapshot.title == "Self-Assessment — Global"
    narratives = [item for item in result.claims if item.claim_kind == "narrative_summary"]
    assert len(narratives) == 1
    assert result.snapshot.summary == narratives[0].claim
    assert result.snapshot.verification_status == "unverified"
    assert all(item.verification_status == "unverified" for item in result.claims)
    with read_database(workspace) as connection:
        generations = connection.execute(
            "SELECT generation_id FROM assessment_snapshots WHERE id = ? "
            "UNION SELECT generation_id FROM self_claims WHERE snapshot_id = ?",
            (result.snapshot.id, result.snapshot.id),
        ).fetchall()
        run = connection.execute(
            "SELECT stage, status FROM processing_runs WHERE id = ?", (result.run_id,)
        ).fetchone()
    assert len(generations) == 1
    assert tuple(run) == ("13.6", "completed")


def test_empty_global_fails_before_provider_and_processing_run(workspace: Path) -> None:
    fake = FakeContractRunner([])
    with pytest.raises(EmptyAssessmentViewError):
        run_stage6(workspace, fake, AssessmentIds())
    assert fake.calls == []
    with read_database(workspace) as connection:
        assert connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0] == 0


def test_gaps_only_global_mirrors_open_questions(workspace: Path) -> None:
    ids = AssessmentIds()
    log, _items = add_log(
        workspace,
        log_id="log_vera_gap_only",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example noted an unresolved scope question.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_gap_only", "manual_claim"),),
    )
    run_stage3(workspace, FakeContractRunner([empty_response()]), ids, log_id=log.id)
    gap_only_detection = json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "raw_log",
                    "target_id": log.id,
                    "question": "What scale did Vera Example validate?",
                    "reason": "missing_scale",
                    "priority": "medium",
                }
            ],
            "contradictions": [],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    run_stage4(workspace, FakeContractRunner([gap_only_detection]), ids)
    with read_database(workspace) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM experience_facts WHERE superseded_at IS NULL"
            ).fetchone()[0]
            == 0
        )
    gaps_only_assessment = json.dumps(
        {
            "self_claims": [
                {
                    "claim": (
                        "Current evidence suggests Vera Example has open questions, "
                        "not conclusions."
                    ),
                    "claim_kind": "narrative_summary",
                    "dimension": "gap",
                    "source_signal_ids": [],
                    "source_fact_ids": [],
                    "confidence": "unknown",
                    "uncertainty": "Vera Example has no extracted facts yet.",
                }
            ],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    result = run_stage6(workspace, FakeContractRunner([gaps_only_assessment]), ids)
    assert result.snapshot is not None
    assert len(result.snapshot.gap_question_ids) == 1
    assert result.snapshot.contradiction_ids == []
    assert result.claims[0].confidence == "unknown"
    assert result.claims[0].source_fact_ids == []


def test_empty_project_fails_before_provider_and_new_processing_run(workspace: Path) -> None:
    prepare_facts(workspace, SignalIds())
    with read_database(workspace) as connection:
        before = connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
    fake = FakeContractRunner([])
    with pytest.raises(EmptyAssessmentViewError):
        run_stage6(workspace, fake, AssessmentIds(), scope="project", target="Atlas")
    assert fake.calls == []
    with read_database(workspace) as connection:
        assert connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0] == before


@pytest.mark.parametrize("narrative_count", [0, 2])
def test_narrative_count_retries_once_then_fails_atomically(
    workspace: Path, narrative_count: int
) -> None:
    ids, facts, signals = prepare_graph(workspace)
    invalid = assessment_response(
        fact_ids=list(facts), signal_ids=list(signals), narrative_count=narrative_count
    )
    with pytest.raises(LLMInvocationError) as error:
        run_stage6(workspace, FakeContractRunner([invalid, invalid]), ids)
    assert error.value.failure_code == "response_validation_failed"
    with read_database(workspace) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assessment_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM self_claims").fetchone()[0] == 0


def test_empty_source_unknown_passes_but_non_unknown_fails_cap(workspace: Path) -> None:
    ids, _facts, _signals = prepare_graph(workspace)
    valid = assessment_response(fact_ids=[], signal_ids=[], confidence="unknown")
    result = run_stage6(workspace, FakeContractRunner([valid]), ids)
    assert result.claims

    invalid = assessment_response(fact_ids=[], signal_ids=[], confidence="low")
    with pytest.raises(LLMInvocationError):
        run_stage6(workspace, FakeContractRunner([invalid, invalid]), ids)


@pytest.mark.parametrize(
    ("fact_ids", "signal_ids"),
    [(["fact_vera_unsupplied"], []), ([], ["signal_vera_unsupplied"])],
)
def test_unsupplied_claim_reference_retries_then_fails_without_rows(
    workspace: Path, fact_ids: list[str], signal_ids: list[str]
) -> None:
    ids, _facts, _signals = prepare_graph(workspace)
    invalid = assessment_response(fact_ids=fact_ids, signal_ids=signal_ids, confidence="unknown")
    with pytest.raises(LLMInvocationError) as error:
        run_stage6(workspace, FakeContractRunner([invalid, invalid]), ids)
    assert error.value.failure_code == "response_validation_failed"
    with read_database(workspace) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assessment_snapshots").fetchone()[0] == 0


def test_claim_confidence_above_strongest_supplied_source_retries_then_fails(
    workspace: Path,
) -> None:
    ids, facts, signals = prepare_graph(workspace)
    invalid = assessment_response(
        fact_ids=list(facts), signal_ids=list(signals), confidence="high"
    )
    with pytest.raises(LLMInvocationError):
        run_stage6(workspace, FakeContractRunner([invalid, invalid]), ids)


def test_snapshot_copies_complete_gap_and_contradiction_sets_and_writer_inputs(
    workspace: Path,
) -> None:
    ids = SignalIds()
    facts = prepare_facts(workspace, ids)
    detected = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=facts[0],
                    left=("experience_fact", facts[0]),
                    right=("raw_log", "log_vera_signal_0"),
                )
            ]
        ),
        ids,
    )
    signals = run_stage5(
        workspace, FakeContractRunner([signal_response(list(facts))]), ids
    ).current_signals
    fake = FakeContractRunner(
        [assessment_response(fact_ids=list(facts), signal_ids=[signals[0].id])]
    )
    result = run_stage6(workspace, fake, ids)
    assert result.snapshot is not None
    assert set(result.snapshot.gap_question_ids) == set(detected.created_gap_ids)
    assert set(result.snapshot.contradiction_ids) == set(detected.created_contradiction_ids)
    payload = json.loads(fake.calls[0].serialized_input)
    assert {item["id"] for item in payload["gaps"]} == set(detected.created_gap_ids)
    assert {item["id"] for item in payload["contradictions"]} == set(
        detected.created_contradiction_ids
    )


def test_folded_project_view_replacement_preserves_other_views(workspace: Path) -> None:
    ids, facts, signals = prepare_graph(workspace)
    response = assessment_response(fact_ids=list(facts), signal_ids=list(signals))
    global_result = run_stage6(workspace, FakeContractRunner([response]), ids)
    first = run_stage6(
        workspace,
        FakeContractRunner([response]),
        ids,
        scope="project",
        target="Vera Example Project",
    )
    second = run_stage6(
        workspace,
        FakeContractRunner([response]),
        ids,
        scope="project",
        target="vera example project",
    )
    assert second.replaced_view is not None
    assert second.replaced_view.snapshot_id == first.snapshot_id
    with read_database(workspace) as connection:
        current = list_assessment_snapshots(connection)
    assert {item.id for item in current} == {global_result.snapshot_id, second.snapshot_id}
    assert len({assessment_view_key(item.scope, item.scope_target) for item in current}) == 2
    assert second.snapshot is not None
    assert second.snapshot.scope_target == "vera example project"


def test_global_atlas_and_exp2res_views_coexist_and_only_folded_match_replaces(
    workspace: Path,
) -> None:
    ids = SignalIds()
    facts_by_project: dict[str, str] = {}
    for index, project in enumerate(("Atlas", "Exp2Res")):
        log, items = add_log(
            workspace,
            log_id=f"log_vera_three_views_{index}",
            recorded_at=FIXED_NOW,
            raw_text=f"Vera Example {project} view evidence.",
            occurred=exact_day(15),
            item_specs=((f"evi_vera_three_views_{index}", "manual_claim"),),
            project=project,
        )
        result = run_stage3(
            workspace,
            FakeContractRunner([fact_response([items[0].id])]),
            ids,
            log_id=log.id,
        )
        facts_by_project[project] = result.created[0]
    signals = run_stage5(
        workspace,
        FakeContractRunner([signal_response(list(facts_by_project.values()))]),
        ids,
    ).current_signals
    signal_ids = [signals[0].id]
    global_view = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=list(facts_by_project.values()), signal_ids=signal_ids)]
        ),
        ids,
    )
    atlas_view = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[facts_by_project["Atlas"]], signal_ids=signal_ids)]
        ),
        ids,
        scope="project",
        target="Atlas",
    )
    exp2res_view = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[facts_by_project["Exp2Res"]], signal_ids=signal_ids)]
        ),
        ids,
        scope="project",
        target="Exp2Res",
    )
    assert exp2res_view.superseded_snapshot_ids == ()
    assessment_parent = workspace / "out" / "assessment"
    assessment_parent.mkdir(mode=0o700, exist_ok=True)
    view_sets = {
        snapshot_id: assessment_parent / snapshot_id
        for snapshot_id in (
            global_view.snapshot_id,
            atlas_view.snapshot_id,
            exp2res_view.snapshot_id,
        )
    }
    for path in view_sets.values():
        path.mkdir(mode=0o700)
        (path / "Vera Example stale member").write_text(
            "Vera Example stale member\n", encoding="utf-8"
        )
    replacement = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[facts_by_project["Exp2Res"]], signal_ids=signal_ids)]
        ),
        ids,
        scope="project",
        target="exp2res",
    )
    assert replacement.superseded_snapshot_ids == (exp2res_view.snapshot_id,)
    assert not view_sets[exp2res_view.snapshot_id].exists()
    assert view_sets[global_view.snapshot_id].is_dir()
    assert view_sets[atlas_view.snapshot_id].is_dir()
    with read_database(workspace) as connection:
        current_ids = {item.id for item in list_assessment_snapshots(connection)}
    assert current_ids == {global_view.snapshot_id, atlas_view.snapshot_id, replacement.snapshot_id}


def test_project_input_uses_stored_key_and_supplies_cross_project_context(
    workspace: Path,
) -> None:
    ids = SignalIds()
    created: list[str] = []
    for index, project in enumerate((" Exp2Res ", "Atlas", None)):
        log, items = add_log(
            workspace,
            log_id=f"log_vera_assess_project_{index}",
            recorded_at=FIXED_NOW,
            raw_text=f"Vera Example project evidence {index}.",
            occurred=exact_day(15),
            item_specs=((f"evi_vera_assess_project_{index}", "manual_claim"),),
            project=project,
        )
        result = run_stage3(
            workspace,
            FakeContractRunner([fact_response([items[0].id])]),
            ids,
            log_id=log.id,
        )
        created.extend(result.created)
    run_stage5(
        workspace,
        FakeContractRunner([signal_response(created)]),
        ids,
    )
    with read_database(workspace) as connection:
        rows = connection.execute(
            "SELECT id, project_key FROM experience_facts WHERE superseded_at IS NULL"
        ).fetchall()
        signal_id = connection.execute(
            "SELECT id FROM self_signals WHERE superseded_at IS NULL"
        ).fetchone()[0]
    subject_id = next(row["id"] for row in rows if row["project_key"] == "exp2res")
    fake = FakeContractRunner(
        [assessment_response(fact_ids=[subject_id], signal_ids=[signal_id])]
    )
    run_stage6(workspace, fake, ids, scope="project", target="Exp2Res")
    payload = json.loads(fake.calls[0].serialized_input)
    assert [item["id"] for item in payload["facts"]] == [subject_id]
    assert {item["id"] for item in payload["context_facts"]} == set(created) - {subject_id}


def test_repository_fk_born_state_and_update_trigger(workspace: Path) -> None:
    missing = SelfClaim(
        id="claim_vera_missing",
        created_at=FIXED_NOW,
        snapshot_id="snapshot_vera_missing",
        claim="Vera Example missing owner claim.",
        claim_kind="hypothesis",
        dimension="gap",
        source_signal_ids=[],
        source_fact_ids=[],
        confidence="unknown",
        verification_status="unverified",
    )
    with writer_database(workspace) as connection:
        with pytest.raises(IntegrityFailureError, match="claim_snapshot_missing"):
            insert_self_claim(
                connection, missing, produced_by_run_id="run_missing", generation_id="gen_missing"
            )

    ids, facts, signals = prepare_graph(workspace)
    result = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts), signal_ids=list(signals))]),
        ids,
    )
    claim_id = result.created_claim_ids[0]
    assert result.snapshot is not None and result.generation_id is not None
    invalid_claims = (
        SelfClaim(
            id="claim_vera_born_superseded",
            created_at=FIXED_NOW,
            superseded_at=FIXED_NOW,
            snapshot_id=result.snapshot.id,
            claim="Vera Example born superseded claim.",
            claim_kind="hypothesis",
            dimension="gap",
            source_signal_ids=[],
            source_fact_ids=[],
            confidence="unknown",
            verification_status="unverified",
        ),
        SelfClaim(
            id="claim_vera_born_verified",
            created_at=FIXED_NOW,
            snapshot_id=result.snapshot.id,
            claim="Vera Example born verified claim.",
            claim_kind="hypothesis",
            dimension="gap",
            source_signal_ids=[],
            source_fact_ids=[],
            confidence="unknown",
            verification_status="supported",
        ),
        SelfClaim(
            id="claim_vera_born_counterevidence",
            created_at=FIXED_NOW,
            snapshot_id=result.snapshot.id,
            claim="Vera Example born counterevidence claim.",
            claim_kind="hypothesis",
            dimension="gap",
            source_signal_ids=[],
            source_fact_ids=[],
            confidence="unknown",
            verification_status="unverified",
            counterevidence=[
                CounterevidenceItem(
                    statement="Vera Example contrary source.",
                    source_ref_type="raw_log",
                    source_ref_id="log_vera_signal_0",
                )
            ],
        ),
    )
    with writer_database(workspace) as connection:
        for invalid in invalid_claims:
            with pytest.raises(IntegrityFailureError):
                insert_self_claim(
                    connection,
                    invalid,
                    produced_by_run_id=result.run_id,
                    generation_id=result.generation_id,
                )
        with pytest.raises(IntegrityFailureError, match="snapshot_initial_lifecycle_invalid"):
            insert_assessment_snapshot(
                connection,
                result.snapshot.model_copy(
                    update={"id": "snapshot_vera_born_superseded", "superseded_at": FIXED_NOW}
                ),
                produced_by_run_id=result.run_id,
                generation_id=result.generation_id,
            )
        with pytest.raises(IntegrityFailureError, match="snapshot_initial_verification_invalid"):
            insert_assessment_snapshot(
                connection,
                result.snapshot.model_copy(
                    update={"id": "snapshot_vera_born_verified", "verification_status": "supported"}
                ),
                produced_by_run_id=result.run_id,
                generation_id=result.generation_id,
            )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE self_claims SET verification_status = 'supported' WHERE id = ?",
            (claim_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="self_claim_lifecycle_only"):
            connection.execute(
                "UPDATE self_claims SET claim = 'Vera Example mutation.' WHERE id = ?",
                (claim_id,),
            )
        connection.rollback()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE self_claims SET superseded_at = ? WHERE id = ?",
            (FIXED_NOW.isoformat(), claim_id),
        )
        connection.commit()
    with read_database(workspace) as connection:
        current_claims = list_self_claims_for_snapshot(connection, result.snapshot_id)
        assert all(item.snapshot_id == result.snapshot_id for item in current_claims)
        assert claim_id not in {item.id for item in current_claims}


def test_stage5_replacement_invalidates_claims_snapshot_and_reports_view(
    workspace: Path,
) -> None:
    ids, facts, signals = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts), signal_ids=list(signals))]),
        ids,
    )
    stale_set = plant_assessment_set(workspace, assessed.snapshot_id)
    replaced = run_stage5(
        workspace,
        FakeContractRunner([signal_response(list(facts), statement="Vera Example replacement signal.")]),
        ids,
    )
    assert replaced.superseded_snapshot_ids == (assessed.snapshot_id,)
    assert set(replaced.superseded_claim_ids) == set(assessed.created_claim_ids)
    assert replaced.invalidated_views[0].regeneration_command == "exp2res assess generate"
    assert not stale_set.exists()
    with read_database(workspace) as connection:
        assert list_assessment_snapshots(connection) == ()


def test_stage4_retention_preserves_view_but_changed_generation_invalidates_it(
    workspace: Path,
) -> None:
    ids, facts, signals = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts), signal_ids=list(signals))]),
        ids,
    )
    stale_set = plant_assessment_set(workspace, assessed.snapshot_id)
    empty = json.dumps(
        {"gap_questions": [], "contradictions": [], "warnings": []},
        separators=(",", ":"),
    ).encode()
    retained = run_stage4(workspace, FakeContractRunner([empty]), ids)
    assert retained.retained is True
    assert retained.superseded_snapshot_ids == retained.superseded_claim_ids == ()
    assert stale_set.is_dir()
    changed = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=facts[0],
                    left=("experience_fact", facts[0]),
                    right=("raw_log", "log_vera_signal_0"),
                )
            ]
        ),
        ids,
    )
    assert changed.retained is False
    assert changed.superseded_snapshot_ids == (assessed.snapshot_id,)
    assert changed.invalidated_views[0].snapshot_id == assessed.snapshot_id
    assert not stale_set.exists()


def test_stage3_replacement_and_log_delete_cover_assessment_lifecycle(
    workspace: Path,
) -> None:
    ids, facts, signals = prepare_graph(workspace)
    assessed = run_stage6(
        workspace,
        FakeContractRunner([assessment_response(fact_ids=list(facts), signal_ids=list(signals))]),
        ids,
    )
    stale_set = plant_assessment_set(workspace, assessed.snapshot_id)
    extracted = run_stage3(
        workspace,
        FakeContractRunner([fact_response(["evi_vera_signal_0"])]),
        ids,
        log_id="log_vera_signal_0",
    )
    assert extracted.superseded_snapshot_ids == (assessed.snapshot_id,)
    assert extracted.invalidated_views[0].regeneration_command == "exp2res assess generate"
    assert not stale_set.exists()

    current_fact = extracted.created[0]
    signals2 = run_stage5(
        workspace,
        FakeContractRunner([signal_response([current_fact])]),
        ids,
    ).current_signals
    assessed2 = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[current_fact], signal_ids=[signals2[0].id])]
        ),
        ids,
    )
    deleted = delete_log(workspace, log_id="log_vera_signal_0")
    assert assessed2.snapshot_id in deleted.purged_snapshot_ids
    assert set(assessed2.created_claim_ids).issubset(deleted.purged_claim_ids)
    assert deleted.invalidated_views[0].snapshot_id == assessed2.snapshot_id


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("Vera Example Space", "--project 'Vera Example Space'"),
        ("Vera Example's View", "--project 'Vera Example'\"'\"'s View'"),
    ],
)
def test_project_invalidation_commands_are_posix_quoted(
    target: str, expected: str
) -> None:
    from exp2res.domain.results import invalidated_view

    report = invalidated_view(scope="project", scope_target=target, snapshot_id="snapshot_vera")
    assert expected in report.regeneration_command
