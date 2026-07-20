"""Offline assessment-export graph, gate, integrity, and service tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from exp2res.errors import (
    AssessmentExportBlockedError,
    IntegrityFailureError,
    InvalidInputError,
    ManagedOutputIncompleteError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
)
from exp2res.services.export import export_assessment
from exp2res.storage.repository import (
    update_assessment_snapshot_verification,
)
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage7_verification import (
    generated_snapshot,
    run_stage7,
    verifier_response,
)


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


@pytest.mark.parametrize(
    ("status", "passes"),
    [
        ("unverified", False),
        ("supported", True),
        ("partially_supported", True),
        ("inferred_but_acceptable", True),
        ("needs_clarification", True),
        ("contradicted", True),
        ("unsupported", False),
        ("rejected", False),
    ],
)
def test_assessment_export_gate_matrix_all_eight_statuses(
    workspace: Path, status: str, passes: bool
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    if status != "unverified":
        responses = [verifier_response(status) for _ in generated.claims]
        run_stage7(
            workspace,
            FakeContractRunner(responses),
            ids,
            generated.snapshot_id,
        )
    if passes:
        result = export_assessment(
            workspace,
            snapshot_id=generated.snapshot_id,
            clock=lambda: FIXED_NOW,
        )
        assert Path(result.manifest_path).is_file()
        assert result.managed_paths == sorted(
            result.managed_paths, key=lambda value: Path(value).name.encode("utf-8")
        )
    else:
        with pytest.raises(AssessmentExportBlockedError) as caught:
            export_assessment(workspace, snapshot_id=generated.snapshot_id)
        assert caught.value.exit_code == 10


def test_selector_errors_precede_export_and_superseded_is_class_two(
    workspace: Path,
) -> None:
    with pytest.raises(InvalidInputError) as malformed:
        export_assessment(workspace, snapshot_id="../Vera Example")
    assert malformed.value.exit_code == 2
    assert not (workspace / "out" / "assessment").exists()

    with pytest.raises(SelectorNotFoundError):
        export_assessment(workspace, snapshot_id="snapshot_vera_missing")

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE assessment_snapshots SET superseded_at = ? WHERE id = ?",
            (FIXED_NOW.isoformat(), generated.snapshot_id),
        )
        connection.commit()
    with pytest.raises(SnapshotNotCurrentError) as superseded:
        export_assessment(workspace, snapshot_id=generated.snapshot_id)
    assert superseded.value.exit_code == 2


def test_fresh_reduction_mismatch_and_narrative_invariant_fail_integrity(
    workspace: Path,
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        update_assessment_snapshot_verification(
            connection,
            snapshot_id=generated.snapshot_id,
            verification_status="partially_supported",
        )
        connection.commit()
    with pytest.raises(IntegrityFailureError, match="snapshot_aggregate_mismatch"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)

    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE assessment_snapshots SET verification_status = 'supported', summary = ? "
            "WHERE id = ?",
            ("Vera Example mismatched summary.", generated.snapshot_id),
        )
        connection.commit()
    with pytest.raises(IntegrityFailureError, match="snapshot_narrative_gate_failed"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)


def test_stale_detection_sets_fail_export_closed(workspace: Path) -> None:
    """§13.12: a current contradiction missing from the snapshot's referenced
    set, or a current unanswered gap it does not reference, is an
    inconsistent input and fails export before publication."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    with read_database(workspace) as connection:
        run_id = connection.execute(
            "SELECT id FROM processing_runs LIMIT 1"
        ).fetchone()[0]
        fact_id = connection.execute(
            "SELECT id FROM experience_facts WHERE superseded_at IS NULL LIMIT 1"
        ).fetchone()[0]
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO contradictions (id, created_at, title, description, "
            "left_ref_type, left_ref_id, right_ref_type, right_ref_id, "
            "metadata_json, produced_by_run_id, generation_id) VALUES "
            "('contradiction_vera_stray', '2026-07-15T12:00:00+00:00', "
            "'Vera Example stray conflict', 'Vera Example stray description.', "
            "'experience_fact', ?, 'experience_fact', ?, '{}', ?, "
            "'generation_vera_stray')",
            (fact_id, fact_id, run_id),
        )
        connection.commit()
    with pytest.raises(
        IntegrityFailureError, match="snapshot_contradiction_set_stale"
    ):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)

    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM contradictions WHERE id = 'contradiction_vera_stray'"
        )
        connection.execute(
            "INSERT INTO gap_questions (id, created_at, target_type, "
            "target_id, question, reason, priority, answered, "
            "produced_by_run_id, generation_id) VALUES "
            "('gap_vera_stray', '2026-07-15T12:00:00+00:00', "
            "'experience_fact', ?, 'Which Vera Example scale applies?', "
            "'missing_scale', 'medium', 0, ?, 'generation_vera_stray')",
            (fact_id, run_id),
        )
        connection.commit()
    with pytest.raises(IntegrityFailureError, match="snapshot_gap_set_stale"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)

    # A current gap answered after synthesis must stay listed so the report
    # renders its answered-since-synthesis marker; an unlisted one is the
    # same inconsistent input and fails closed. A gap answered before
    # synthesis is legitimately unlisted and does not block export.
    with read_database(workspace) as connection:
        snapshot_created_at = connection.execute(
            "SELECT created_at FROM assessment_snapshots WHERE id = ?",
            (generated.snapshot_id,),
        ).fetchone()[0]
    assert "2000-01-01" < snapshot_created_at < "2099-01-01"
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO raw_logs (id, recorded_at, entry_type, source_type, "
            "temporal_precision, temporal_confidence, raw_text, metadata_json) "
            "VALUES ('log_vera_late_answer', '2099-01-01T00:00:00+00:00', "
            "'gap_answer', 'manual_entry', 'unknown', 'unknown', "
            "'Vera Example late answer.', ?)",
            (
                json.dumps(
                    {
                        "question_text": "Which Vera Example scale applies?",
                        "question_reason": "missing_scale",
                    }
                ),
            ),
        )
        connection.execute(
            "UPDATE gap_questions SET answered = 1, "
            "answer_log_id = 'log_vera_late_answer' "
            "WHERE id = 'gap_vera_stray'"
        )
        connection.commit()
    with pytest.raises(IntegrityFailureError, match="snapshot_gap_set_stale"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)

    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE raw_logs SET recorded_at = '2000-01-01T00:00:00+00:00' "
            "WHERE id = 'log_vera_late_answer'"
        )
        connection.commit()
    export_assessment(
        workspace, snapshot_id=generated.snapshot_id, clock=lambda: FIXED_NOW
    )

    # The listed complement: a listed gap whose answer predates synthesis is
    # equally inconsistent (Stage 6 lists only gaps unanswered at synthesis),
    # while a listed answer recorded after synthesis renders the
    # answered-since-synthesis marker and exports.
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE assessment_snapshots SET gap_question_ids_json = ? "
            "WHERE id = ?",
            (json.dumps(["gap_vera_stray"]), generated.snapshot_id),
        )
        connection.commit()
    with pytest.raises(IntegrityFailureError, match="snapshot_gap_set_stale"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)

    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE raw_logs SET recorded_at = '2099-01-01T00:00:00+00:00' "
            "WHERE id = 'log_vera_late_answer'"
        )
        connection.commit()
    export_assessment(
        workspace, snapshot_id=generated.snapshot_id, clock=lambda: FIXED_NOW
    )


def test_displaced_detection_targets_fail_export(workspace: Path) -> None:
    """§13.3: a listed contradiction referencing a raw log displaced by a
    correction is not a current detection target and fails export closed."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    with read_database(workspace) as connection:
        run_id = connection.execute(
            "SELECT id FROM processing_runs LIMIT 1"
        ).fetchone()[0]
        fact_id = connection.execute(
            "SELECT id FROM experience_facts WHERE superseded_at IS NULL LIMIT 1"
        ).fetchone()[0]
        raw_log_id = connection.execute(
            "SELECT id FROM raw_logs LIMIT 1"
        ).fetchone()[0]
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO raw_logs (id, recorded_at, entry_type, source_type, "
            "temporal_precision, temporal_confidence, raw_text, "
            "corrects_log_id) VALUES "
            "('log_vera_correction', '2026-07-15T13:00:00+00:00', "
            "'correction', 'manual_entry', 'unknown', 'unknown', "
            "'Vera Example corrected restatement.', ?)",
            (raw_log_id,),
        )
        connection.execute(
            "INSERT INTO contradictions (id, created_at, title, description, "
            "left_ref_type, left_ref_id, right_ref_type, right_ref_id, "
            "metadata_json, produced_by_run_id, generation_id) VALUES "
            "('contradiction_vera_displaced', '2026-07-15T12:00:00+00:00', "
            "'Vera Example displaced conflict', "
            "'Vera Example displaced description.', "
            "'experience_fact', ?, 'raw_log', ?, '{}', ?, "
            "'generation_vera_displaced')",
            (fact_id, raw_log_id, run_id),
        )
        connection.execute(
            "UPDATE assessment_snapshots SET contradiction_ids_json = ? "
            "WHERE id = ?",
            (
                json.dumps(["contradiction_vera_displaced"]),
                generated.snapshot_id,
            ),
        )
        connection.commit()
    with pytest.raises(
        IntegrityFailureError, match="contradiction_right_ref_invalid"
    ):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)


def test_claim_from_another_generation_fails_export_as_mixed_graph(
    workspace: Path,
) -> None:
    """§12 rule 13 / #97: a member claim whose production provenance differs
    from the snapshot's is a mixed Stage 6 graph and fails export closed."""

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE self_claims SET generation_id = 'generation_vera_stray' "
            "WHERE id = ?",
            (generated.claims[0].id,),
        )
        connection.commit()
    with pytest.raises(
        IntegrityFailureError, match="snapshot_claim_generation_mismatch"
    ):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)


def test_out_of_chain_counterevidence_target_joins_manifest_sources_only(
    workspace: Path,
) -> None:
    """§13.14: counterevidence grounding rows read for validation join
    source_ids and the render-input hash without widening the closed
    §13.12 evidence-map link projection."""

    import json

    from test_stage5_signals import (
        SignalIds,
        prepare_facts,
        run_stage5,
        signal_response,
    )
    from test_stage6_assessment import assessment_response, run_stage6

    ids = SignalIds()
    cited_fact, scope_fact = prepare_facts(workspace, ids, count=2)
    signal_result = run_stage5(
        workspace,
        FakeContractRunner([signal_response([cited_fact])]),
        ids,
    )
    signal_ids = [item.id for item in signal_result.current_signals]
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[cited_fact], signal_ids=signal_ids)]
        ),
        ids,
    )
    counterevidence = [
        {
            "statement": "Vera Example holds one contrary scope fact.",
            "source_ref_type": "experience_fact",
            "source_ref_id": scope_fact,
        }
    ]
    run_stage7(
        workspace,
        FakeContractRunner(
            [
                verifier_response("contradicted", counterevidence=counterevidence),
                *(verifier_response() for _ in generated.claims[1:]),
            ]
        ),
        ids,
        generated.snapshot_id,
    )
    result = export_assessment(
        workspace, snapshot_id=generated.snapshot_id, clock=lambda: FIXED_NOW
    )
    manifest = json.loads(Path(result.manifest_path).read_bytes())
    assert scope_fact in manifest["source_ids"]["experience_fact_ids"]
    # §16.1 cascade: the grounding fact's own chain rows were read and join
    # the manifest source lists.
    with read_database(workspace) as connection:
        chain = connection.execute(
            "SELECT fs.evidence_item_id, ei.raw_log_id FROM fact_sources fs "
            "JOIN evidence_items ei ON ei.id = fs.evidence_item_id "
            "WHERE fs.fact_id = ?",
            (scope_fact,),
        ).fetchall()
    assert chain
    for evidence_id, raw_log_id in chain:
        assert evidence_id in manifest["source_ids"]["evidence_item_ids"]
        assert raw_log_id in manifest["source_ids"]["raw_log_ids"]
    evidence_map = json.loads(
        (Path(result.manifest_path).parent / "evidence_map.json").read_bytes()
    )
    assert scope_fact not in [
        link["fact_id"] for link in evidence_map["fact_links"]
    ]


def test_out_of_chain_counterevidence_signal_cascades_its_fact_chain(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grounding signal outside the closure resolves signal → facts →
    evidence → raw logs; every read row joins source_ids while the closed
    evidence-map link projection stays claim-closure-only."""

    import json

    from test_stage5_signals import SignalIds, prepare_facts, run_stage5
    from test_stage6_assessment import assessment_response, run_stage6

    ids = SignalIds()
    cited_fact, scope_fact = prepare_facts(workspace, ids, count=2)
    two_signals = json.dumps(
        {
            "signals": [
                {
                    "signal_type": "execution_pattern",
                    "statement": "Vera Example repeatedly cites the first fact.",
                    "supporting_fact_ids": [cited_fact],
                    "counter_fact_ids": [],
                    "confidence": "medium",
                },
                {
                    "signal_type": "execution_pattern",
                    "statement": "Vera Example holds an uncited contrary pattern.",
                    "supporting_fact_ids": [scope_fact],
                    "counter_fact_ids": [],
                    "confidence": "medium",
                },
            ],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signal_result = run_stage5(workspace, FakeContractRunner([two_signals]), ids)
    by_statement = {
        item.statement: item.id for item in signal_result.current_signals
    }
    cited_signal = by_statement["Vera Example repeatedly cites the first fact."]
    uncited_signal = by_statement["Vera Example holds an uncited contrary pattern."]
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [
                assessment_response(
                    fact_ids=[cited_fact], signal_ids=[cited_signal]
                )
            ]
        ),
        ids,
    )
    counterevidence = [
        {
            "statement": "Vera Example shows a contrary recurring pattern.",
            "source_ref_type": "self_signal",
            "source_ref_id": uncited_signal,
        }
    ]
    run_stage7(
        workspace,
        FakeContractRunner(
            [
                verifier_response("contradicted", counterevidence=counterevidence),
                *(verifier_response() for _ in generated.claims[1:]),
            ]
        ),
        ids,
        generated.snapshot_id,
    )
    result = export_assessment(
        workspace, snapshot_id=generated.snapshot_id, clock=lambda: FIXED_NOW
    )
    manifest = json.loads(Path(result.manifest_path).read_bytes())
    assert uncited_signal in manifest["source_ids"]["self_signal_ids"]
    assert scope_fact in manifest["source_ids"]["experience_fact_ids"]
    with read_database(workspace) as connection:
        chain = connection.execute(
            "SELECT fs.evidence_item_id, ei.raw_log_id FROM fact_sources fs "
            "JOIN evidence_items ei ON ei.id = fs.evidence_item_id "
            "WHERE fs.fact_id = ?",
            (scope_fact,),
        ).fetchall()
    assert chain
    for evidence_id, raw_log_id in chain:
        assert evidence_id in manifest["source_ids"]["evidence_item_ids"]
        assert raw_log_id in manifest["source_ids"]["raw_log_ids"]
    evidence_map = json.loads(
        (Path(result.manifest_path).parent / "evidence_map.json").read_bytes()
    )
    assert uncited_signal not in [
        link["signal_id"] for link in evidence_map["signal_links"]
    ]
    assert scope_fact not in [
        link["fact_id"] for link in evidence_map["fact_links"]
    ]

    # Doped hydration on the grounding fact only: a stored raw-log set that
    # disagrees with its evidence-derived chain fails export closed.
    import exp2res.exports.graph as graph_module

    real_get = graph_module.get_experience_fact

    def phantom_log_on_grounding_fact(connection, fact_id):
        fact = real_get(connection, fact_id)
        if fact is not None and fact.id == scope_fact:
            return fact.model_copy(
                update={
                    "source_log_ids": [*fact.source_log_ids, "log_vera_phantom"]
                }
            )
        return fact

    monkeypatch.setattr(
        graph_module, "get_experience_fact", phantom_log_on_grounding_fact
    )
    with pytest.raises(
        IntegrityFailureError, match="fact_raw_log_closure_incomplete"
    ):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)
    monkeypatch.setattr(graph_module, "get_experience_fact", real_get)

    # A corrupted graph — the grounding signal's fact superseded without the
    # lifecycle supersession of the snapshot — fails export closed.
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE experience_facts SET superseded_at = ? WHERE id = ?",
            (FIXED_NOW.isoformat(), scope_fact),
        )
        connection.commit()
    with pytest.raises(
        IntegrityFailureError, match="export_source_reference_invalid"
    ):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)


def test_out_of_closure_detection_references_join_manifest_sources(
    workspace: Path,
) -> None:
    """Gap targets and contradiction references outside the claim closure
    fold into source_ids and the render-input bundle like counterevidence
    grounding rows, while the closed link projections stay closure-only."""

    import json

    from test_stage4_detection import detector_response, run_stage4
    from test_stage5_signals import (
        SignalIds,
        prepare_facts,
        run_stage5,
        signal_response,
    )
    from test_stage6_assessment import assessment_response, run_stage6

    ids = SignalIds()
    cited_fact, detected_fact = prepare_facts(workspace, ids, count=2)
    run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=detected_fact,
                    left=("experience_fact", cited_fact),
                    right=("experience_fact", detected_fact),
                )
            ]
        ),
        ids,
    )
    signal_result = run_stage5(
        workspace, FakeContractRunner([signal_response([cited_fact])]), ids
    )
    signal_ids = [item.id for item in signal_result.current_signals]
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[cited_fact], signal_ids=signal_ids)]
        ),
        ids,
    )
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    result = export_assessment(
        workspace, snapshot_id=generated.snapshot_id, clock=lambda: FIXED_NOW
    )
    manifest = json.loads(Path(result.manifest_path).read_bytes())
    assert detected_fact in manifest["source_ids"]["experience_fact_ids"]
    with read_database(workspace) as connection:
        chain = connection.execute(
            "SELECT fs.evidence_item_id, ei.raw_log_id FROM fact_sources fs "
            "JOIN evidence_items ei ON ei.id = fs.evidence_item_id "
            "WHERE fs.fact_id = ?",
            (detected_fact,),
        ).fetchall()
    assert chain
    for evidence_id, raw_log_id in chain:
        assert evidence_id in manifest["source_ids"]["evidence_item_ids"]
        assert raw_log_id in manifest["source_ids"]["raw_log_ids"]
    evidence_map = json.loads(
        (Path(result.manifest_path).parent / "evidence_map.json").read_bytes()
    )
    assert detected_fact not in [
        link["fact_id"] for link in evidence_map["fact_links"]
    ]

    # §14.7 shape check: an answered marker backed by a raw log that is not a
    # real gap-answer record fails export closed instead of rendering.
    with read_database(workspace) as connection:
        gap_id = connection.execute(
            "SELECT id FROM gap_questions WHERE superseded_at IS NULL"
        ).fetchone()[0]
        manual_log_id = connection.execute(
            "SELECT id FROM raw_logs WHERE entry_type != 'gap_answer' LIMIT 1"
        ).fetchone()[0]
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE gap_questions SET answered = 1, answer_log_id = ? "
            "WHERE id = ?",
            (manual_log_id, gap_id),
        )
        connection.commit()
    with pytest.raises(IntegrityFailureError, match="gap_answer_log_invalid"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)


def test_fact_provenance_disagreement_fails_export_closure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13.12: per-fact equality with the persisted relations, not subsets.

    Hydration derives both ID lists from fact_sources, so disagreement is
    unrepresentable through the storage layer; the doctored hydration stands
    in for a corrupted or future divergent producer.
    """

    import exp2res.exports.graph as graph_module

    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    real_get = graph_module.get_experience_fact

    def phantom_evidence(connection, fact_id):
        fact = real_get(connection, fact_id)
        return fact.model_copy(
            update={
                "evidence_item_ids": [
                    *fact.evidence_item_ids,
                    "evidence_vera_phantom",
                ]
            }
        )

    monkeypatch.setattr(graph_module, "get_experience_fact", phantom_evidence)
    with pytest.raises(IntegrityFailureError, match="fact_evidence_closure"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)

    def phantom_log(connection, fact_id):
        fact = real_get(connection, fact_id)
        return fact.model_copy(
            update={"source_log_ids": [*fact.source_log_ids, "log_vera_phantom"]}
        )

    monkeypatch.setattr(graph_module, "get_experience_fact", phantom_log)
    with pytest.raises(IntegrityFailureError, match="fact_raw_log_closure"):
        export_assessment(workspace, snapshot_id=generated.snapshot_id)
    assert not (
        workspace / "out" / "assessment" / generated.snapshot_id
    ).exists()


def test_repeated_service_export_keeps_fixed_members_identical_and_writes_no_rows(
    workspace: Path,
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    with read_database(workspace) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "processing_runs",
                "raw_logs",
                "evidence_items",
                "experience_facts",
                "self_signals",
                "self_claims",
                "assessment_snapshots",
            )
        }
    first = export_assessment(
        workspace, snapshot_id=generated.snapshot_id, clock=lambda: FIXED_NOW
    )
    fixed_names = {"report.md", "self_claims.json", "evidence_map.json"}
    first_bytes = {
        Path(path).name: Path(path).read_bytes()
        for path in first.managed_paths
        if Path(path).name in fixed_names
    }
    second = export_assessment(workspace, snapshot_id=generated.snapshot_id)
    second_bytes = {
        Path(path).name: Path(path).read_bytes()
        for path in second.managed_paths
        if Path(path).name in fixed_names
    }
    assert first_bytes == second_bytes
    with read_database(workspace) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    assert after == before


def test_unreconciled_preamble_residual_stops_before_rendering(
    workspace: Path,
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response() for _ in generated.claims]),
        ids,
        generated.snapshot_id,
    )
    assessment_parent = workspace / "out" / "assessment"
    assessment_parent.mkdir(mode=0o700)
    target = workspace.parent / "Vera Example residual target"
    target.mkdir()
    candidate = assessment_parent / (
        f".exp2res-candidate-{generated.snapshot_id}-{'e' * 32}"
    )
    candidate.symlink_to(target, target_is_directory=True)
    with pytest.raises(ManagedOutputIncompleteError) as caught:
        export_assessment(workspace, snapshot_id=generated.snapshot_id)
    assert caught.value.residual_paths == (str(candidate),)
    assert not (assessment_parent / generated.snapshot_id).exists()

