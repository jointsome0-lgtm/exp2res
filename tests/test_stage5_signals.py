"""Offline §13.5 self-signal contract, calibration, and lifecycle tests."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from exp2res.domain.models import SelfSignal
from exp2res.errors import IntegrityFailureError, LLMInvocationError
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.pipeline.stage5 import run_signal_generation
from exp2res.storage.repository import (
    insert_self_signal,
    list_self_signals,
    mark_facts_superseded,
)
from exp2res.storage.telemetry import create_processing_run, finish_processing_run
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import (
    SELECTION,
    TestIds,
    add_log,
    budgets,
    exact_day,
    fact_response,
    run_stage3,
)
from test_stage4_detection import detector_response


pytestmark = pytest.mark.contract


class SignalIds:
    __test__ = False

    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        self.counts[kind] += 1
        return f"{kind}_vera_{self.counts[kind]:04d}"


def signal_response(
    supporting_fact_ids: list[str],
    *,
    counter_fact_ids: list[str] | None = None,
    confidence: str = "medium",
    statement: str = "Vera Example repeatedly designs provenance-aware workflows.",
    warnings: list[dict[str, str]] | None = None,
) -> bytes:
    return json.dumps(
        {
            "signals": [
                {
                    "signal_type": "execution_pattern",
                    "statement": statement,
                    "supporting_fact_ids": supporting_fact_ids,
                    "counter_fact_ids": (
                        [] if counter_fact_ids is None else counter_fact_ids
                    ),
                    "confidence": confidence,
                }
            ],
            "warnings": [] if warnings is None else warnings,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def multi_fact_response(
    evidence_ids: list[str], *, count: int, confidence: str = "medium"
) -> bytes:
    template = json.loads(
        fact_response(evidence_ids, confidence=confidence).decode("utf-8")
    )["facts"][0]
    facts = []
    for index in range(count):
        candidate = dict(template)
        candidate["claim"] = (
            f"Vera Example designed provenance workflow slice {index + 1}."
        )
        facts.append(candidate)
    return json.dumps(
        {"facts": facts, "warnings": []}, separators=(",", ":")
    ).encode("utf-8")


def prepare_facts(
    workspace: Path,
    ids: SignalIds,
    *,
    count: int = 1,
    confidence: str = "medium",
) -> tuple[str, ...]:
    created: list[str] = []
    for index in range(count):
        log, items = add_log(
            workspace,
            log_id=f"log_vera_signal_{index}",
            recorded_at=FIXED_NOW - timedelta(hours=count - index),
            raw_text=f"Vera Example designed workflow slice {index + 1}.",
            occurred=exact_day(15),
            item_specs=((f"evi_vera_signal_{index}", "manual_claim"),),
        )
        extracted = run_stage3(
            workspace,
            FakeContractRunner(
                [fact_response([items[0].id], confidence=confidence)]
            ),
            ids,  # type: ignore[arg-type]
            log_id=log.id,
        )
        created.extend(extracted.created)
    return tuple(created)


def prepare_high_facts(
    workspace: Path, ids: SignalIds
) -> tuple[tuple[str, ...], str, str]:
    root, root_items = add_log(
        workspace,
        log_id="log_vera_signal_root",
        recorded_at=FIXED_NOW - timedelta(hours=2),
        raw_text="Vera Example original design artifact interpretation.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_signal_root", "design_doc"),),
        entry_type="design_doc",
        source_type="imported_artifact",
    )
    correction, correction_items = add_log(
        workspace,
        log_id="log_vera_signal_correction",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example corrected the interpretation and retained support.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_signal_correction", "manual_claim"),),
        corrects_log_id=root.id,
    )
    extracted = run_stage3(
        workspace,
        FakeContractRunner(
            [
                multi_fact_response(
                    [root_items[0].id, correction_items[0].id],
                    count=2,
                    confidence="high",
                )
            ]
        ),
        ids,  # type: ignore[arg-type]
        log_id=correction.id,
    )
    return extracted.created, root_items[0].id, correction_items[0].id


def run_stage5(
    workspace: Path, fake: FakeContractRunner, ids: SignalIds
):
    return run_signal_generation(
        workspace,
        selection=SELECTION,
        budgets=budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


@pytest.mark.lifecycle
def test_empty_workspace_completes_zero_call_run_without_generation(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fake = FakeContractRunner([])
    result = run_stage5(workspace, fake, ids)
    assert result.created_signal_ids == result.superseded_signal_ids == ()
    assert result.generation_id is None
    assert result.current_signals == ()
    assert fake.calls == []
    with read_database(workspace) as connection:
        run = connection.execute(
            "SELECT stage, status FROM processing_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        calls = connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE run_id = ?", (result.run_id,)
        ).fetchone()[0]
    assert tuple(run) == ("13.5", "completed")
    assert calls == 0


@pytest.mark.lifecycle
def test_happy_path_persists_sorted_lists_shared_generation_and_telemetry(
    workspace: Path,
) -> None:
    ids = SignalIds()
    facts = prepare_facts(workspace, ids, count=3)
    fake = FakeContractRunner(
        [
            signal_response(
                [facts[2], facts[0]],
                counter_fact_ids=[facts[2], facts[1]],
                confidence="low",
                warnings=[
                    {"type": "vera_note", "message": "Vera Example note."}
                ],
            )
        ]
    )
    result = run_stage5(workspace, fake, ids)

    assert len(result.created_signal_ids) == 1
    assert result.generation_id is not None
    assert result.warnings[0].type == "vera_note"
    assert len(fake.calls) == 1
    with read_database(workspace) as connection:
        stored = list_self_signals(connection)[0]
        assert stored.supporting_fact_ids == sorted(
            [facts[2], facts[0]], key=str.encode
        )
        assert stored.counter_fact_ids == sorted(
            [facts[2], facts[1]], key=str.encode
        )
        generation = connection.execute(
            "SELECT generation_id FROM self_signals WHERE id = ?", (stored.id,)
        ).fetchone()[0]
        call = connection.execute(
            "SELECT status, input_hash, output_hash FROM llm_calls WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()
        run = connection.execute(
            "SELECT stage, status FROM processing_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
    assert generation == result.generation_id
    assert tuple(call)[0] == "completed" and call[1] and call[2]
    assert tuple(run) == ("13.5", "completed")


def _clone_high_facts(
    workspace: Path, source_fact_ids: tuple[str, ...]
) -> tuple[str, ...]:
    cloned: list[str] = []
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index, source_fact_id in enumerate(source_fact_ids, start=1):
            row = connection.execute(
                "SELECT * FROM experience_facts WHERE id = ?", (source_fact_id,)
            ).fetchone()
            payload = dict(row)
            clone_id = f"fact_vera_high_clone_{index}"
            payload["id"] = clone_id
            payload["confidence"] = "high"
            columns = tuple(payload)
            connection.execute(
                f"INSERT INTO experience_facts({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(payload[column] for column in columns),
            )
            connection.execute(
                """
                INSERT INTO fact_sources(fact_id, evidence_item_id, support_type)
                SELECT ?, evidence_item_id, support_type
                FROM fact_sources WHERE fact_id = ?
                """,
                (clone_id, source_fact_id),
            )
            cloned.append(clone_id)
        connection.commit()
    return tuple(cloned)


@pytest.mark.lifecycle
def test_identical_rerun_still_replaces_the_complete_generation(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_id = prepare_facts(workspace, ids)[0]
    payload = signal_response([fact_id])
    first = run_stage5(workspace, FakeContractRunner([payload]), ids)
    second = run_stage5(workspace, FakeContractRunner([payload]), ids)

    assert first.generation_id != second.generation_id
    assert second.superseded_signal_ids == first.created_signal_ids
    assert second.superseded_generation_ids == (first.generation_id,)
    with read_database(workspace) as connection:
        rows = connection.execute(
            "SELECT id, superseded_at, generation_id FROM self_signals"
        ).fetchall()
    assert len(rows) == 2
    assert sum(row[1] is None for row in rows) == 1


@pytest.mark.invariant
@pytest.mark.parametrize(
    ("confidence", "support_mode", "counter"),
    [
        ("high", "one_medium", False),
        ("high", "two_one_log", False),
        ("medium", "one_low", False),
        ("high", "one_medium", True),
        ("low", "empty", False),
    ],
)
def test_confidence_cap_violations_retry_once_and_commit_no_rows(
    workspace: Path,
    confidence: str,
    support_mode: str,
    counter: bool,
) -> None:
    ids = SignalIds()
    if support_mode == "empty":
        prepare_facts(workspace, ids)
        facts: list[str] = []
    elif support_mode == "two_one_log":
        _log, items = add_log(
            workspace,
            log_id="log_vera_two_facts",
            recorded_at=FIXED_NOW - timedelta(hours=1),
            raw_text="Vera Example supplied two statements from one record.",
            occurred=exact_day(15),
            item_specs=(("evi_vera_two_facts", "manual_claim"),),
        )
        facts = list(
            run_stage3(
                workspace,
                FakeContractRunner(
                    [multi_fact_response([items[0].id], count=2)]
                ),
                ids,  # type: ignore[arg-type]
            ).created
        )
    else:
        facts = list(
            prepare_facts(
                workspace,
                ids,
                confidence="low" if support_mode == "one_low" else "medium",
            )
        )
    counter_ids = facts[:1] if counter else []
    invalid = signal_response(
        facts,
        counter_fact_ids=counter_ids,
        confidence=confidence,
    )
    fake = FakeContractRunner([invalid, invalid])

    with pytest.raises(LLMInvocationError) as caught:
        run_stage5(workspace, fake, ids)
    assert caught.value.failure_code == "response_validation_failed"
    assert len(fake.calls) == 2
    assert b"confidence_above_cap" in (fake.calls[1].validation_errors or b"")
    with read_database(workspace) as connection:
        assert list_self_signals(connection) == ()
        call = connection.execute(
            "SELECT status, schema_retries FROM llm_calls ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert tuple(call) == ("failed", 1)


@pytest.mark.invariant
def test_high_passes_with_two_high_facts_across_two_raw_logs(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_ids, _displaced_item, _current_item = prepare_high_facts(workspace, ids)
    result = run_stage5(
        workspace,
        FakeContractRunner([signal_response(list(fact_ids), confidence="high")]),
        ids,
    )
    assert result.current_signals[0].confidence == "high"


@pytest.mark.invariant
def test_high_with_two_high_facts_from_one_raw_log_retries_then_fails(
    workspace: Path,
) -> None:
    ids = SignalIds()
    _log, items = add_log(
        workspace,
        log_id="log_vera_high_one_source",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example supplied two same-source facts.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_high_one_source", "manual_claim"),),
    )
    medium_facts = tuple(
        run_stage3(
            workspace,
            FakeContractRunner([multi_fact_response([items[0].id], count=2)]),
            ids,  # type: ignore[arg-type]
        ).created
    )
    high_facts = _clone_high_facts(workspace, medium_facts)
    invalid = signal_response(list(high_facts), confidence="high")
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError):
        run_stage5(workspace, fake, ids)
    assert b"confidence_above_cap" in (fake.calls[1].validation_errors or b"")


@pytest.mark.invariant
def test_high_with_counter_fact_retries_despite_independent_high_support(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_ids, _displaced_item, _current_item = prepare_high_facts(workspace, ids)
    invalid = signal_response(
        list(fact_ids),
        counter_fact_ids=[fact_ids[0]],
        confidence="high",
    )
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError):
        run_stage5(workspace, fake, ids)
    assert b"confidence_above_cap" in (fake.calls[1].validation_errors or b"")


@pytest.mark.invariant
@pytest.mark.parametrize("field", ["supporting", "counter"])
def test_out_of_context_fact_reference_retries_then_fails_atomically(
    workspace: Path, field: str
) -> None:
    ids = SignalIds()
    fact_id = prepare_facts(workspace, ids)[0]
    supporting = ["fact_vera_outside"] if field == "supporting" else [fact_id]
    counter = ["fact_vera_outside"] if field == "counter" else []
    invalid = signal_response(supporting, counter_fact_ids=counter, confidence="low")
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError):
        run_stage5(workspace, fake, ids)
    assert len(fake.calls) == 2
    assert b"out_of_context_target" in (fake.calls[1].validation_errors or b"")
    with read_database(workspace) as connection:
        assert list_self_signals(connection) == ()


@pytest.mark.invariant
def test_displaced_support_uses_descriptor_while_current_item_is_complete(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_ids, displaced_item_id, current_item_id = prepare_high_facts(workspace, ids)
    fake = FakeContractRunner(
        [signal_response(list(fact_ids), confidence="high")]
    )
    run_stage5(workspace, fake, ids)
    payload = json.loads(fake.calls[0].serialized_input)
    items = {item["id"]: item for item in payload["evidence_items"]}
    assert set(items[displaced_item_id]) == {
        "id",
        "raw_log_id",
        "strength",
        "uri",
        "path",
    }
    assert {"summary", "title", "created_at"}.isdisjoint(
        items[displaced_item_id]
    )
    assert {"summary", "title", "created_at"}.issubset(items[current_item_id])


@pytest.mark.lifecycle
def test_stage3_replacement_supersedes_signals_but_empty_stage3_does_not(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_id = prepare_facts(workspace, ids)[0]
    generated = run_stage5(
        workspace, FakeContractRunner([signal_response([fact_id])]), ids
    )
    replaced = run_stage3(
        workspace,
        FakeContractRunner([fact_response(["evi_vera_signal_0"])]),
        ids,  # type: ignore[arg-type]
        log_id="log_vera_signal_0",
    )
    assert replaced.superseded_signal_ids == generated.created_signal_ids
    with read_database(workspace) as connection:
        assert list_self_signals(connection) == ()

    empty_workspace = workspace.parent / "empty-stage3"
    empty_workspace.mkdir()
    from exp2res.storage.workspace import initialize_workspace

    initialize_workspace(empty_workspace, clock=lambda: FIXED_NOW)
    signal = SelfSignal(
        id="signal_vera_sourceless",
        created_at=FIXED_NOW,
        signal_type="interest_signal",
        statement="Vera Example has no supplied support yet.",
        supporting_fact_ids=[],
        confidence="unknown",
    )
    with writer_database(empty_workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        create_processing_run(
            connection,
            run_id="run_vera_sourceless",
            stage="13.5",
            started_at=FIXED_NOW,
            provider="fake",
            model="fake",
            prompt_policy_hash="a" * 64,
            input_ids=[],
        )
        insert_self_signal(
            connection,
            signal,
            produced_by_run_id="run_vera_sourceless",
            generation_id="gen_vera_sourceless",
        )
        finish_processing_run(
            connection,
            run_id="run_vera_sourceless",
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=[signal.id],
        )
        connection.commit()
    empty = run_stage3(
        empty_workspace,
        FakeContractRunner([]),
        TestIds(),
    )
    assert empty.superseded_signal_ids == ()
    with read_database(empty_workspace) as connection:
        assert [item.id for item in list_self_signals(connection)] == [signal.id]


@pytest.mark.lifecycle
def test_stage4_retention_keeps_signals_and_replacement_supersedes_them(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_id = prepare_facts(workspace, ids)[0]
    signal = run_stage5(
        workspace, FakeContractRunner([signal_response([fact_id])]), ids
    )
    retained = run_detection_generation(
        workspace,
        selection=SELECTION,
        budgets=budgets(),
        runner=FakeContractRunner(
            [b'{"gap_questions":[],"contradictions":[],"warnings":[]}']
        ),
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )
    assert retained.retained is True
    assert retained.superseded_signal_ids == ()
    with read_database(workspace) as connection:
        assert [item.id for item in list_self_signals(connection)] == list(
            signal.created_signal_ids
        )

    replaced = run_detection_generation(
        workspace,
        selection=SELECTION,
        budgets=budgets(),
        runner=FakeContractRunner(
            [
                detector_response(
                    target_id=fact_id,
                    left=("experience_fact", fact_id),
                    right=("raw_log", "log_vera_signal_0"),
                )
            ]
        ),
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )
    assert replaced.retained is False
    assert replaced.superseded_signal_ids == signal.created_signal_ids
    with read_database(workspace) as connection:
        assert list_self_signals(connection) == ()


@pytest.mark.invariant
def test_insert_self_signal_guards_lifecycle_references_and_commit_cap(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_id = prepare_facts(workspace, ids)[0]

    def candidate(
        *,
        signal_id: str,
        support: list[str],
        confidence: str = "medium",
        superseded: bool = False,
    ) -> SelfSignal:
        return SelfSignal(
            id=signal_id,
            created_at=FIXED_NOW,
            superseded_at=FIXED_NOW if superseded else None,
            signal_type="skill_signal",
            statement="Vera Example demonstrates guarded persistence.",
            supporting_fact_ids=support,
            confidence=confidence,
        )

    cases = (
        (
            candidate(
                signal_id="signal_vera_born_closed",
                support=[fact_id],
                superseded=True,
            ),
            "signal_initial_lifecycle_invalid",
        ),
        (
            candidate(
                signal_id="signal_vera_missing", support=["fact_vera_missing"]
            ),
            "signal_fact_missing",
        ),
        (
            candidate(
                signal_id="signal_vera_above_cap",
                support=[fact_id],
                confidence="high",
            ),
            "signal_confidence_above_cap",
        ),
    )
    with writer_database(workspace) as connection:
        for signal, diagnostic in cases:
            with pytest.raises(IntegrityFailureError, match=diagnostic):
                insert_self_signal(
                    connection,
                    signal,
                    produced_by_run_id="run_vera_0001",
                    generation_id="gen_vera_guard",
                )
        connection.execute("BEGIN IMMEDIATE")
        mark_facts_superseded(connection, [fact_id], FIXED_NOW)
        with pytest.raises(IntegrityFailureError, match="signal_fact_superseded"):
            insert_self_signal(
                connection,
                candidate(
                    signal_id="signal_vera_superseded", support=[fact_id]
                ),
                produced_by_run_id="run_vera_0001",
                generation_id="gen_vera_guard",
            )
        connection.rollback()


@pytest.mark.lifecycle
def test_self_signal_guards_allow_only_supersession_or_owner_delete(
    workspace: Path,
) -> None:
    ids = SignalIds()
    fact_id = prepare_facts(workspace, ids)[0]
    generated = run_stage5(
        workspace, FakeContractRunner([signal_response([fact_id])]), ids
    )
    signal_id = generated.created_signal_ids[0]
    with writer_database(workspace) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="self_signal_lifecycle_only"):
            connection.execute(
                "UPDATE self_signals SET statement = ? WHERE id = ?",
                ("Vera Example forbidden rewrite.", signal_id),
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="self_signal_owner_purge_required"
        ):
            connection.execute("DELETE FROM self_signals WHERE id = ?", (signal_id,))
