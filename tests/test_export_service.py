"""Offline assessment-export graph, gate, integrity, and service tests."""

from __future__ import annotations

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

