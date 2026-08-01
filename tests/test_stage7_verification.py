"""Offline §13.7 assessment-verification substrate tests."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

import pytest

from exp2res.config import load_workspace_config
from exp2res.errors import (
    AssessmentExportBlockedError,
    IntegrityFailureError,
    LLMInvocationError,
    SnapshotNotCurrentError,
)
from exp2res.llm.registry import ADAPTER_REGISTRY
from exp2res.pipeline.stage7 import run_assessment_verification
from exp2res.services.export import require_export_eligible
from exp2res.services.logs import delete_log
from exp2res.storage.repository import (
    list_self_claims_for_snapshot,
    list_verification_findings,
)
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW, REPOSITORY_ROOT
from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage4_detection import detector_response, run_stage4
from test_stage5_signals import (
    SignalIds,
    prepare_facts,
    prepare_high_facts,
    run_stage5,
    signal_response,
)
from test_stage6_assessment import (
    assessment_response,
    prepare_graph,
    run_stage6,
)


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def verifier_response(
    status: str = "supported",
    *,
    counterevidence: list[dict[str, str]] | None = None,
    include_reason: bool = True,
) -> bytes:
    payload: dict[str, object] = {
        "status": status,
        "unsupported_phrases": (
            []
            if status == "supported"
            else ["unsupported scale wording"]
        ),
        "counterevidence": [] if counterevidence is None else counterevidence,
        "suggested_rewrite": (
            None
            if status == "supported"
            else "The supplied evidence supports a narrower statement."
        ),
    }
    if include_reason:
        payload["reason"] = (
            "The supplied evidence supports the claim."
            if status == "supported"
            else "The supplied evidence requires a non-passing verdict."
        )
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def run_stage7(workspace: Path, fake: FakeContractRunner, ids, snapshot_id: str):
    return run_assessment_verification(
        workspace,
        snapshot_id=snapshot_id,
        selection=SELECTION,
        budgets=budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def generated_snapshot(workspace: Path):
    ids, facts, signals = prepare_graph(workspace)
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
        ),
        ids,
    )
    assert generated.snapshot_id is not None
    return ids, facts, signals, generated


@pytest.mark.parametrize(
    ("statuses", "aggregate"),
    [
        (("supported", "contradicted"), "contradicted"),
        (("supported", "rejected"), "rejected"),
        (("supported", "supported"), "supported"),
    ],
)
def test_mixed_verdicts_commit_findings_and_precedence(
    workspace: Path, statuses: tuple[str, str], aggregate: str
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    fake = FakeContractRunner([verifier_response(item) for item in statuses])
    result = run_stage7(workspace, fake, ids, generated.snapshot_id)
    assert result.snapshot_status == aggregate
    assert tuple(status for _claim, status in result.claim_statuses) == statuses
    assert len(result.findings) == 2
    assert {item.status for item in result.findings} == set(statuses)
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT stage, status FROM processing_runs WHERE id = ?", (result.run_id,)
        ).fetchone()
    assert tuple(run) == ("13.7", "completed")


def test_pass_changes_only_verification_fields_and_claim_prose_trigger_holds(
    workspace: Path,
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    with read_database(workspace) as connection:
        before = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM self_claims WHERE snapshot_id = ?", (generated.snapshot_id,)
            )
        }
    run_stage7(
        workspace,
        FakeContractRunner([verifier_response(), verifier_response()]),
        ids,
        generated.snapshot_id,
    )
    with read_database(workspace) as connection:
        after = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM self_claims WHERE snapshot_id = ?", (generated.snapshot_id,)
            )
        }
    for claim_id in before:
        for key in before[claim_id]:
            if key not in {"verification_status", "counterevidence_json"}:
                assert after[claim_id][key] == before[claim_id][key]
    with writer_database(workspace) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="self_claim_lifecycle_only"):
            connection.execute(
                "UPDATE self_claims SET claim = ? WHERE id = ?",
                ("An illegally rewritten claim.", generated.created_claim_ids[0]),
            )


def test_valid_negative_verdict_consumes_no_schema_retry(workspace: Path) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    result = run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response("unsupported"), verifier_response("supported")]
        ),
        ids,
        generated.snapshot_id,
    )
    with read_database(workspace) as connection:
        retries = connection.execute(
            "SELECT schema_retries FROM llm_calls WHERE run_id = ? ORDER BY call_index",
            (result.run_id,),
        ).fetchall()
    assert [row[0] for row in retries] == [0, 0]


def test_schema_invalid_first_response_retries_once_then_commits(
    workspace: Path,
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    invalid = verifier_response(include_reason=False)
    fake = FakeContractRunner([invalid, verifier_response(), verifier_response()])
    result = run_stage7(workspace, fake, ids, generated.snapshot_id)
    assert result.snapshot_status == "supported"
    assert len(fake.calls) == 3
    assert b"reason" in (fake.calls[1].validation_errors or b"")
    with read_database(workspace) as connection:
        retries = connection.execute(
            "SELECT schema_retries FROM llm_calls WHERE run_id = ? ORDER BY call_index",
            (result.run_id,),
        ).fetchall()
    assert [row[0] for row in retries] == [1, 0]


def test_invalid_after_retry_keeps_prior_complete_pass_and_records_failed_run(
    workspace: Path,
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    prior = run_stage7(
        workspace,
        FakeContractRunner([verifier_response(), verifier_response()]),
        ids,
        generated.snapshot_id,
    )
    invalid = verifier_response(include_reason=False)
    with pytest.raises(LLMInvocationError) as caught:
        run_stage7(
            workspace,
            FakeContractRunner([invalid, invalid]),
            ids,
            generated.snapshot_id,
        )
    assert caught.value.failure_code == "response_validation_failed"
    with read_database(workspace) as connection:
        claims = list_self_claims_for_snapshot(connection, generated.snapshot_id)
        findings = list_verification_findings(connection)
        failed = connection.execute(
            "SELECT status, output_ids_json FROM processing_runs "
            "WHERE stage = '13.7' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert all(item.verification_status == "supported" for item in claims)
    assert tuple(item.id for item in findings) == tuple(
        item.id for item in prior.findings
    )
    assert tuple(failed) == ("failed", "[]")


@pytest.mark.parametrize("mode", ["out_of_bundle", "wrong_type", "duplicate"])
def test_invalid_counterevidence_retries_then_fails(
    workspace: Path, mode: str
) -> None:
    ids, facts, signals, generated = generated_snapshot(workspace)
    if mode == "out_of_bundle":
        entries = [
            {
                "statement": "The contrary source is outside the bundle.",
                "source_ref_type": "experience_fact",
                "source_ref_id": "fact_vera_outside_bundle",
            }
        ]
    elif mode == "wrong_type":
        entries = [
            {
                "statement": "The contrary source uses the wrong type.",
                "source_ref_type": "self_signal",
                "source_ref_id": facts[0],
            }
        ]
    else:
        entry = {
            "statement": "The contrary source is duplicated.",
            "source_ref_type": "self_signal",
            "source_ref_id": signals[0],
        }
        entries = [entry, dict(entry)]
    invalid = verifier_response("unsupported", counterevidence=entries)
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError):
        run_stage7(workspace, fake, ids, generated.snapshot_id)
    assert len(fake.calls) == 2
    with read_database(workspace) as connection:
        assert list_verification_findings(connection) == ()


def _sourceless_snapshot(workspace: Path):
    # Stage 6's existing gap-only path is represented directly by a writer output
    # after preparing a non-empty graph whose claims deliberately cite no source.
    ids, _facts, _signals = prepare_graph(workspace)
    source_free = json.dumps(
        {
            "self_claims": [
                {
                    "claim": "Current evidence leaves this conclusion without a source.",
                    "claim_kind": "narrative_summary",
                    "dimension": "gap",
                    "source_signal_ids": [],
                    "source_fact_ids": [],
                    "confidence": "unknown",
                    "uncertainty": "Evidence is absent for this claim.",
                }
            ],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    generated = run_stage6(
        workspace, FakeContractRunner([source_free]), ids
    )
    assert generated.snapshot_id is not None
    return ids, generated


def test_chainless_supported_retries_then_fails_but_rejected_commits(
    workspace: Path,
) -> None:
    ids, generated = _sourceless_snapshot(workspace)
    invalid = verifier_response("supported")
    with pytest.raises(LLMInvocationError):
        run_stage7(
            workspace,
            FakeContractRunner([invalid, invalid]),
            ids,
            generated.snapshot_id,
        )
    committed = run_stage7(
        workspace,
        FakeContractRunner([verifier_response("rejected")]),
        ids,
        generated.snapshot_id,
    )
    assert committed.snapshot_status == "rejected"
    assert committed.findings[0].status == "rejected"


def test_narrative_gate_fails_before_provider_or_run(workspace: Path) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute(
            "UPDATE assessment_snapshots SET summary = ? WHERE id = ?",
            ("A mismatched narrative summary.", generated.snapshot_id),
        )
    with read_database(workspace) as connection:
        before = connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
    fake = FakeContractRunner([])
    with pytest.raises(IntegrityFailureError, match="snapshot_narrative_gate_failed"):
        run_stage7(workspace, fake, ids, generated.snapshot_id)
    assert fake.calls == []
    with read_database(workspace) as connection:
        assert connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0] == before


def test_stale_contradiction_set_fails_before_provider_or_run(
    workspace: Path,
) -> None:
    ids = SignalIds()
    facts = prepare_high_facts(workspace, ids)[0]
    detected = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=facts[0],
                    left=("experience_fact", facts[0]),
                    right=("raw_log", "log_vera_signal_correction"),
                )
            ]
        ),
        ids,
    )
    signals = run_stage5(
        workspace, FakeContractRunner([signal_response(list(facts), confidence="low")]), ids
    ).current_signals
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=list(facts), signal_ids=[signals[0].id])]
        ),
        ids,
    )
    assert generated.snapshot_id is not None and detected.created_contradiction_ids
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute(
            "UPDATE assessment_snapshots SET contradiction_ids_json = '[]' WHERE id = ?",
            (generated.snapshot_id,),
        )
    with read_database(workspace) as connection:
        before = connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
    fake = FakeContractRunner([])
    with pytest.raises(IntegrityFailureError, match="snapshot_contradiction_set_stale"):
        run_stage7(workspace, fake, ids, generated.snapshot_id)
    assert fake.calls == []
    with read_database(workspace) as connection:
        assert connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0] == before


def test_verifier_input_supplies_the_current_contradiction_set(
    workspace: Path,
) -> None:
    # §13.7 check 14 / §15.5: the snapshot's contradiction set is view
    # context, so a restated detection stays visible to the verifier.
    ids = SignalIds()
    facts = prepare_high_facts(workspace, ids)[0]
    detected = run_stage4(
        workspace,
        FakeContractRunner(
            [
                detector_response(
                    target_id=facts[0],
                    left=("experience_fact", facts[0]),
                    right=("raw_log", "log_vera_signal_correction"),
                )
            ]
        ),
        ids,
    )
    signals = run_stage5(
        workspace, FakeContractRunner([signal_response(list(facts), confidence="low")]), ids
    ).current_signals
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=list(facts), signal_ids=[signals[0].id])]
        ),
        ids,
    )
    assert generated.snapshot_id is not None
    contradiction_id = detected.created_contradiction_ids[0]
    fake = FakeContractRunner([verifier_response(), verifier_response()])
    run_stage7(workspace, fake, ids, generated.snapshot_id)
    assert fake.calls
    for call in fake.calls:
        payload = json.loads(call.serialized_input)
        assert [item["id"] for item in payload["contradictions"]] == [contradiction_id]
    with read_database(workspace) as connection:
        input_ids = json.loads(
            connection.execute(
                "SELECT input_ids_json FROM processing_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
        )
    assert contradiction_id in input_ids


def test_superseded_snapshot_selector_is_distinct(workspace: Path) -> None:
    ids, facts, signals, first = generated_snapshot(workspace)
    second = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=list(facts), signal_ids=list(signals))]
        ),
        ids,
    )
    assert second.snapshot_id != first.snapshot_id
    fake = FakeContractRunner([])
    with pytest.raises(SnapshotNotCurrentError):
        run_stage7(workspace, fake, ids, first.snapshot_id)
    assert fake.calls == []


def test_exact_closure_is_ordered_and_projects_displaced_support(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_ids, displaced_item_id, current_item_id = prepare_high_facts(workspace, ids)
    signals = run_stage5(
        workspace,
        FakeContractRunner(
            [
                signal_response(
                    list(reversed(fact_ids)),
                    counter_fact_ids=[fact_ids[0]],
                    confidence="low",
                )
            ]
        ),
        ids,
    ).current_signals
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [assessment_response(fact_ids=[], signal_ids=[signals[0].id], confidence="low")]
        ),
        ids,
    )
    assert generated.snapshot_id is not None
    fake = FakeContractRunner([verifier_response(), verifier_response()])
    run_stage7(workspace, fake, ids, generated.snapshot_id)
    payload = json.loads(fake.calls[0].serialized_input)
    assert payload["contradictions"] == []
    for field in (
        "source_signals",
        "scope_signals",
        "scope_facts",
        "source_facts",
        "source_evidence_items",
        "source_logs",
        "contradictions",
    ):
        assert [item["id"] for item in payload[field]] == sorted(
            (item["id"] for item in payload[field]), key=lambda item: item.encode("utf-8")
        )
    assert [item["id"] for item in payload["source_facts"]] == sorted(fact_ids)
    assert len(payload["source_facts"]) == len(set(fact_ids))
    assert [item["id"] for item in payload["scope_facts"]] == sorted(fact_ids)
    assert [item["id"] for item in payload["source_signals"]] == [signals[0].id]
    assert [item["id"] for item in payload["scope_signals"]] == [signals[0].id]
    items = {item["id"]: item for item in payload["source_evidence_items"]}
    assert set(items) == {displaced_item_id, current_item_id}
    assert set(items[displaced_item_id]) == {
        "id",
        "raw_log_id",
        "strength",
        "uri",
        "path",
    }
    assert items[displaced_item_id]["strength"] == "design_doc"
    assert items[displaced_item_id]["raw_log_id"] == "log_vera_signal_root"
    assert [item["id"] for item in payload["source_logs"]] == [
        "log_vera_signal_correction"
    ]
    assert payload["source_signals"][0]["counter_fact_ids"] == [fact_ids[0]]


def test_reverification_appends_history_and_overwrites_current_state(
    workspace: Path,
) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    first = run_stage7(
        workspace,
        FakeContractRunner([verifier_response(), verifier_response()]),
        ids,
        generated.snapshot_id,
    )
    second = run_stage7(
        workspace,
        FakeContractRunner(
            [verifier_response("unsupported"), verifier_response("supported")]
        ),
        ids,
        generated.snapshot_id,
    )
    assert second.snapshot_status == "unsupported"
    with read_database(workspace) as connection:
        history = list_verification_findings(connection)
    assert len(history) == len(first.findings) + len(second.findings)
    assert {item.produced_by_run_id for item in history} == {
        first.run_id,
        second.run_id,
    }


def test_findings_are_append_only_until_owner_purge(workspace: Path) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    result = run_stage7(
        workspace,
        FakeContractRunner([verifier_response(), verifier_response()]),
        ids,
        generated.snapshot_id,
    )
    finding_id = result.findings[0].id
    with writer_database(workspace) as connection:
        with pytest.raises(
            sqlite3.IntegrityError, match="verification_finding_immutable"
        ):
            connection.execute(
                "UPDATE verification_findings SET reason = ? WHERE id = ?",
                ("An altered finding reason.", finding_id),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="verification_finding_owner_purge_required",
        ):
            connection.execute(
                "DELETE FROM verification_findings WHERE id = ?", (finding_id,)
            )
    # §11.14: the payload stays immutable even on owner-delete connections;
    # owner deletion may only purge finding rows, never rewrite them.
    with writer_database(workspace, owner_delete=True) as connection:
        with pytest.raises(
            sqlite3.IntegrityError, match="verification_finding_immutable"
        ):
            connection.execute(
                "UPDATE verification_findings SET reason = ? WHERE id = ?",
                ("An altered finding reason.", finding_id),
            )


def test_raw_log_reset_purges_verification_findings(workspace: Path) -> None:
    ids, _facts, _signals, generated = generated_snapshot(workspace)
    result = run_stage7(
        workspace,
        FakeContractRunner([verifier_response(), verifier_response()]),
        ids,
        generated.snapshot_id,
    )
    deleted = delete_log(workspace, log_id="log_vera_signal_0")
    assert deleted.purged_finding_ids == tuple(
        sorted((item.id for item in result.findings), key=lambda item: item.encode("utf-8"))
    )
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM verification_findings"
        ).fetchone()[0] == 0


# One assertion in each of §16.14's two licensed forms, over a graph that
# grounds it: two facts across two distinct raw logs, so "pattern" is a
# §9.4-supported recurrence rather than a single-source generalization, and
# no continuation word invites a §16.7 objection. A stable, well-grounded
# verdict is what makes the twin comparison mean anything.
SECOND_PERSON_CLAIM = "You show a provenance-aware working pattern across the recorded work."
SUBJECT_FREE_TWIN = (
    "A provenance-aware working pattern appears across the recorded work."
)
# A reason-vocabulary scan was tried and removed. It cannot tell a complaint
# from an endorsement: live runs produced "the second person and owner
# reference form are valid, but …" and "the dimension and mirror voice are
# valid, but …", which any such list flags, while a voice rejection phrased
# without the listed words slips past it. The twin comparison below is the
# phrasing-independent check, so the vocabulary adds only false verdicts.
# §15.5 permits a verbatim diagnostic quotation of candidate wording, and a
# quote of the whole sentence necessarily carries its pronoun, so only a
# phrase that is nothing but the pronoun names the form itself. Every other
# voice complaint is caught by the twin comparison below, not here.
BARE_SECOND_PERSON = frozenset({"you", "your", "yours"})

# §16.11 gives each status a meaning but no total order; this is a test-local
# severity scale over those meanings, weakest verdict last, used only to ask
# whether the addressed wording was lowered relative to its twin.
STATUS_SEVERITY = (
    "supported",
    "inferred_but_acceptable",
    "partially_supported",
    "needs_clarification",
    "contradicted",
    "unsupported",
    "rejected",
)


def _export_eligible(status: str) -> bool:
    """Report §16.11's assessment-export gate through its own service seam."""

    try:
        require_export_eligible(status)
    except AssessmentExportBlockedError:
        return False
    return True


def _two_source_graph(workspace: Path, ids):
    """Two independent owner records, so a pattern claim has real support."""

    facts = prepare_facts(workspace, ids, count=2)
    signals = run_stage5(
        workspace,
        FakeContractRunner([signal_response(list(facts))]),
        ids,
    ).current_signals
    return facts, tuple(item.id for item in signals)


def _snapshot_with_claim(workspace: Path, ids, facts, signals, claim: str):
    """Generate one snapshot whose single non-summary claim carries `claim`."""

    payload = json.loads(
        assessment_response(fact_ids=list(facts), signal_ids=list(signals))
    )
    payload["self_claims"][0]["claim"] = claim
    generated = run_stage6(
        workspace,
        FakeContractRunner(
            [json.dumps(payload, separators=(",", ":")).encode("utf-8")]
        ),
        ids,
    )
    assert generated.snapshot_id is not None
    return generated


@pytest.mark.live
def test_live_verifier_accepts_the_second_person_owner_reference(
    workspace: Path,
) -> None:
    """§16.14: the form the mirror requires is never a Stage 7 rejection ground.

    Issue #219 reproduction: with §16.14 encoded as a prohibition only, four
    live Stage 7 runs rejected ordinary second-person claim prose, so every
    export and §17 render refused.

    The same claim is verified twice over one evidence graph, once in the
    second person and once in §16.14's other licensed form. Evidence grounds
    apply to both wordings, so a status the second-person run reaches and its
    subject-free twin does not is a voice verdict however it is phrased —
    which no reason-vocabulary denylist alone could establish.
    """

    config_path = workspace / ".exp2res" / "config.toml"
    config_path.write_text(
        '[workspace]\ntimezone = "Etc/UTC"\n\n'
        '[llm]\nadapter = "codex-cli"\nmodel = "gpt-5.6-sol"\n\n'
        "[privacy]\nignore_paths = []\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    config = load_workspace_config(workspace).llm
    selection = config.selection
    runner = ADAPTER_REGISTRY[selection.adapter].build_runner(
        config, REPOSITORY_ROOT
    )
    ids = SignalIds()
    facts, signals = _two_source_graph(workspace, ids)

    def verify(claim: str) -> dict[str, tuple[str, str, tuple[str, ...]]]:
        """Verify one snapshot live; return each claim's finding by its prose."""

        generated = _snapshot_with_claim(workspace, ids, facts, signals, claim)
        with read_database(workspace) as connection:
            prose_by_id = {
                row["id"]: row["claim"]
                for row in connection.execute(
                    "SELECT id, claim FROM self_claims WHERE snapshot_id = ?",
                    (generated.snapshot_id,),
                )
            }
        assert claim in prose_by_id.values(), prose_by_id
        result = run_assessment_verification(
            workspace,
            snapshot_id=generated.snapshot_id,
            selection=selection,
            budgets=budgets(
                transport_attempt_cap=1, invocation_deadline_seconds=300.0
            ),
            runner=runner,
            id_factory=ids,
            clock=lambda: FIXED_NOW,
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )
        return {
            prose_by_id[item.target_id]: (
                item.status,
                item.reason,
                tuple(item.unsupported_phrases),
            )
            for item in result.findings
        }

    addressed = verify(SECOND_PERSON_CLAIM)
    subject_free = verify(SUBJECT_FREE_TWIN)

    # No quoted unsupported phrase may be the bare pronoun: that names the
    # form itself, and nothing else in a candidate can be quoted as just
    # "you".
    for _status, _reason, phrases in addressed.values():
        assert not any(
            item.strip().strip(".,\"'").lower() in BARE_SECOND_PERSON
            for item in phrases
        ), addressed

    # The unnamed ground: the twins assert the same content over the same
    # graph, so every evidence ground reaches both. Any lowering the addressed
    # wording alone receives is therefore a voice verdict however it is
    # phrased — the check the reason vocabulary cannot make.
    target = addressed[SECOND_PERSON_CLAIM]
    twin = subject_free[SUBJECT_FREE_TWIN]
    assert STATUS_SEVERITY.index(target[0]) <= STATUS_SEVERITY.index(
        twin[0]
    ), (target, twin)
    # #219's own consequence, stated in its own terms rather than by rank.
    assert _export_eligible(target[0]) or not _export_eligible(twin[0]), (
        target,
        twin,
    )
