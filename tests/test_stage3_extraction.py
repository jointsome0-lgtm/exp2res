"""Offline §13.3 fact-extraction contract, lifecycle, and atomicity tests."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3

import pytest

from exp2res.domain.models import EvidenceItem, OccurredAt, RawLog
from exp2res.errors import (
    LLMCancelledError,
    LLMInvocationError,
    SelectorNotFoundError,
)
from exp2res.llm.registry import (
    ADAPTER_REGISTRY,
    AdapterRegistration,
    LLMSelection,
)
from exp2res.llm.runner import CallBudgets, PreparedCall
from exp2res.pipeline.lineage import plan_lineages
from exp2res.pipeline.stage3 import run_fact_extraction
from exp2res.storage.repository import (
    insert_evidence_item,
    insert_raw_log,
    list_experience_facts,
)
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner


pytestmark = pytest.mark.contract
SELECTION = LLMSelection("codex-cli", "gpt-test-vera-example")


class TestIds:
    __test__ = False

    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        self.counts[kind] += 1
        prefix = {"fact": "fact", "run": "run", "gen": "gen"}[kind]
        return f"{prefix}_vera_{self.counts[kind]:04d}"


def budgets(**overrides: object) -> CallBudgets:
    values: dict[str, object] = {
        "transport_attempt_cap": 2,
        "backoff_lower_seconds": 0.0,
        "backoff_upper_seconds": 0.0,
        "invocation_deadline_seconds": 10.0,
        "max_input_bytes": 1_048_576,
        "input_token_budget": 100_000,
        "output_token_budget": 4_096,
        "planned_output_tokens": 512,
        "model_context_tokens": 128_000,
        "model_max_output_tokens": 8_192,
        "per_run_call_ceiling": 20,
        "planned_call_count": 1,
        "per_invocation_cost_ceiling": Decimal("10"),
        "per_run_cost_ceiling": Decimal("20"),
        "input_cost_per_million": Decimal("1"),
        "output_cost_per_million": Decimal("1"),
    }
    values.update(overrides)
    return CallBudgets(**values)  # type: ignore[arg-type]


def exact_day(day: int, *, confidence: str = "medium") -> OccurredAt:
    return OccurredAt(
        start=datetime(2026, 7, day, tzinfo=timezone.utc),
        end=None,
        precision="exact_day",
        confidence=confidence,
    )


def month(day: int = 1, *, confidence: str = "medium") -> OccurredAt:
    return OccurredAt(
        start=datetime(2026, 7, day, tzinfo=timezone.utc),
        end=None,
        precision="month",
        confidence=confidence,
    )


def temporal_payload(occurred: OccurredAt) -> dict[str, object]:
    return occurred.model_dump(mode="json")


def add_log(
    workspace: Path,
    *,
    log_id: str,
    recorded_at: datetime,
    raw_text: str,
    occurred: OccurredAt,
    item_specs: tuple[tuple[str, str], ...],
    project: str | None = "Vera Example Project",
    corrects_log_id: str | None = None,
    entry_type: str | None = None,
    source_type: str | None = None,
) -> tuple[RawLog, tuple[EvidenceItem, ...]]:
    log = RawLog(
        id=log_id,
        recorded_at=recorded_at,
        entry_type=entry_type or ("correction" if corrects_log_id else "manual_daily"),
        source_type=source_type or "manual_entry",
        occurred=occurred,
        raw_text=raw_text,
        project=project,
        external_ref=None,
        corrects_log_id=corrects_log_id,
        metadata={},
    )
    items = tuple(
        EvidenceItem(
            id=item_id,
            created_at=recorded_at,
            raw_log_id=log_id,
            title="Vera Example source" if strength != "manual_claim" else None,
            summary=f"Vera Example {strength} support.",
            uri=(
                f"https://example.invalid/{item_id}"
                if strength != "manual_claim"
                else None
            ),
            path=(f"vera/{item_id}.md" if strength == "design_doc" else None),
            strength=strength,
            metadata={},
        )
        for item_id, strength in item_specs
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        insert_raw_log(connection, log)
        for item in items:
            insert_evidence_item(connection, item)
        connection.commit()
    return log, items


def fact_response(
    evidence_ids: list[str],
    *,
    confidence: str = "medium",
    occurred: dict[str, object] | None = None,
    extra_fact_fields: dict[str, object] | None = None,
    drop_fact_fields: tuple[str, ...] = (),
    warnings: list[dict[str, str]] | None = None,
) -> bytes:
    fact: dict[str, object] = {
        "claim": "Vera Example designed a provenance-aware local workflow.",
        "claim_kind": "observed_fact",
        "role": None,
        "company": None,
        "context": "independent_project",
        "ownership_level": "designed",
        "action": "designed",
        "object": "a provenance-aware local workflow",
        "outcome": None,
        "skills": ["provenance design"],
        "technologies": ["SQLite"],
        "themes": ["local-first"],
        "occurred": occurred,
        "evidence_item_ids": evidence_ids,
        "confidence": confidence,
    }
    fact.update(extra_fact_fields or {})
    for dropped in drop_fact_fields:
        fact.pop(dropped)
    return json.dumps(
        {
            "facts": [fact],
            "warnings": [] if warnings is None else warnings,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def empty_response() -> bytes:
    return b'{"facts":[],"warnings":[]}'


def run_stage3(
    workspace: Path,
    fake: FakeContractRunner,
    ids: TestIds,
    *,
    log_id: str | None = None,
    call_budgets: CallBudgets | None = None,
):
    return run_fact_extraction(
        workspace,
        log_id=log_id,
        selection=SELECTION,
        budgets=call_budgets or budgets(),
        runner=fake,
        id_factory=ids,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def telemetry_rows(
    workspace: Path, run_id: str
) -> tuple[sqlite3.Row, tuple[sqlite3.Row, ...]]:
    connection = sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite")
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            "SELECT * FROM processing_runs WHERE id = ?", (run_id,)
        ).fetchone()
        calls = tuple(
            connection.execute(
                "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY call_index",
                (run_id,),
            ).fetchall()
        )
    finally:
        connection.close()
    assert run is not None
    return run, calls


@pytest.mark.lifecycle
def test_happy_path_persists_governing_provenance_and_telemetry(
    workspace: Path,
) -> None:
    """§13.3/§12: one validated lineage commits facts and terminal telemetry."""

    governing, items = add_log(
        workspace,
        log_id="log_vera_happy",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example designed a provenance-aware local workflow.",
        occurred=exact_day(15, confidence="high"),
        item_specs=(("evi_vera_happy", "manual_claim"),),
        project=" Exp2Rés ",
    )
    warning = {
        "type": "missing_artifact",
        "message": "Vera Example has no linked artifact in this lineage.",
    }
    fake = FakeContractRunner(
        [fact_response([items[0].id], warnings=[warning])]
    )
    result = run_stage3(workspace, fake, TestIds())

    assert len(result.created) == 1
    assert result.superseded == ()
    assert len(result.generation_ids) == 1
    assert [item.type for item in result.warnings] == ["missing_artifact"]
    with read_database(workspace) as connection:
        facts = list_experience_facts(connection)
        assert len(facts) == 1
        fact = facts[0]
        assert fact.id == result.created[0]
        assert fact.project == governing.project
        assert fact.source_log_ids == [governing.id]
        assert fact.evidence_item_ids == [items[0].id]
        assert fact.occurred == governing.occurred
        stored = connection.execute(
            """
            SELECT project_key, produced_by_run_id, generation_id
            FROM experience_facts WHERE id = ?
            """,
            (fact.id,),
        ).fetchone()
        sources = connection.execute(
            "SELECT evidence_item_id, support_type FROM fact_sources WHERE fact_id = ?",
            (fact.id,),
        ).fetchall()
    assert stored[0] == "exp2rés"
    assert stored[1] == result.run_id
    assert stored[2] == result.generation_ids[0]
    assert [tuple(row) for row in sources] == [(items[0].id, "direct")]

    run, calls = telemetry_rows(workspace, result.run_id)
    assert run["status"] == "completed"
    assert json.loads(run["output_ids_json"]) == list(result.created)
    assert len(calls) == 1 and calls[0]["status"] == "completed"
    assert calls[0]["input_hash"] and calls[0]["output_hash"]


@pytest.mark.lifecycle
def test_rerun_supersedes_prior_generation_at_one_swap_instant(
    workspace: Path,
) -> None:
    """§21.12: unchanged extraction replaces instead of appending current facts."""

    _log, items = add_log(
        workspace,
        log_id="log_vera_rerun",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example designed a local workflow.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_rerun", "manual_claim"),),
    )
    ids = TestIds()
    first = run_stage3(
        workspace, FakeContractRunner([fact_response([items[0].id])]), ids
    )
    second = run_stage3(
        workspace, FakeContractRunner([fact_response([items[0].id])]), ids
    )

    assert first.created != second.created
    assert first.generation_ids != second.generation_ids
    assert second.superseded == first.created
    with read_database(workspace) as connection:
        current = list_experience_facts(connection)
        history = list_experience_facts(connection, current_only=False)
        production = connection.execute(
            """
            SELECT id, superseded_at, generation_id
            FROM experience_facts ORDER BY id
            """
        ).fetchall()
    assert tuple(item.id for item in current) == second.created
    assert {item.id for item in history} == {*first.created, *second.created}
    old = next(row for row in production if row[0] == first.created[0])
    new = next(row for row in production if row[0] == second.created[0])
    assert old[1] == FIXED_NOW.isoformat()
    assert new[1] is None
    assert old[2] != new[2]


@pytest.mark.lifecycle
def test_multi_lineage_failure_keeps_prior_complete_generations(
    workspace: Path,
) -> None:
    """§15.10 rule 7: a later invalid call commits no partial replacement."""

    _first_log, first_items = add_log(
        workspace,
        log_id="log_vera_atomic_a",
        recorded_at=FIXED_NOW - timedelta(hours=2),
        raw_text="Vera Example designed workflow A.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_atomic_a", "manual_claim"),),
    )
    _second_log, second_items = add_log(
        workspace,
        log_id="log_vera_atomic_b",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example designed workflow B.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_atomic_b", "manual_claim"),),
    )
    ids = TestIds()
    seeded = run_stage3(
        workspace,
        FakeContractRunner(
            [
                fact_response([first_items[0].id]),
                fact_response([second_items[0].id]),
            ]
        ),
        ids,
    )
    invalid = fact_response(["evi_vera_out_of_context"])
    fake = FakeContractRunner(
        [fact_response([first_items[0].id]), invalid, invalid]
    )
    with pytest.raises(LLMInvocationError) as caught:
        run_stage3(workspace, fake, ids)
    assert caught.value.failure_code == "response_validation_failed"
    failed_run_id = "run_vera_0002"
    run, calls = telemetry_rows(workspace, failed_run_id)
    assert run["status"] == "failed"
    assert json.loads(run["output_ids_json"]) == []
    assert [call["status"] for call in calls] == ["completed", "failed"]
    assert calls[1]["failure_code"] == "response_validation_failed"
    assert calls[1]["schema_retries"] == 1
    with read_database(workspace) as connection:
        assert tuple(item.id for item in list_experience_facts(connection)) == seeded.created
        assert connection.execute(
            "SELECT COUNT(*) FROM experience_facts WHERE produced_by_run_id = ?",
            (failed_run_id,),
        ).fetchone()[0] == 0


def displaced_lineage(
    workspace: Path,
) -> tuple[RawLog, tuple[EvidenceItem, ...], RawLog, tuple[EvidenceItem, ...]]:
    root, root_items = add_log(
        workspace,
        log_id="log_vera_displaced_root",
        recorded_at=FIXED_NOW - timedelta(hours=3),
        raw_text="Vera Example DISPLACED ROOT PRIVATE PROSE.",
        occurred=month(),
        item_specs=(
            ("evi_vera_displaced_manual", "manual_claim"),
            ("evi_vera_displaced_doc", "design_doc"),
        ),
        entry_type="design_doc",
        source_type="imported_artifact",
    )
    correction, correction_items = add_log(
        workspace,
        log_id="log_vera_correction",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example self-contained corrected workflow statement.",
        occurred=month(),
        item_specs=(("evi_vera_correction_manual", "manual_claim"),),
        corrects_log_id=root.id,
    )
    return root, root_items, correction, correction_items


@pytest.mark.invariant
def test_displacement_projection_excludes_prose_and_manual_item(
    workspace: Path,
) -> None:
    """§13.3 rule 10: displaced prose is absent and support has five fields."""

    root, root_items, correction, _correction_items = displaced_lineage(workspace)
    invalid = fact_response([root_items[0].id])
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError):
        run_stage3(workspace, fake, TestIds())

    payload = fake.calls[0].serialized_input
    assert correction.raw_text.encode() in payload
    assert root.raw_text.encode() not in payload
    decoded = json.loads(payload)
    evidence_ids = {item["id"] for item in decoded["evidence_items"]}
    descriptor_ids = {item["id"] for item in decoded["displaced_support_items"]}
    assert root_items[0].id not in evidence_ids | descriptor_ids
    assert root_items[1].id in descriptor_ids
    descriptor = decoded["displaced_support_items"][0]
    assert set(descriptor) == {"id", "raw_log_id", "strength", "uri", "path"}
    assert len(fake.calls) == 2
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()


@pytest.mark.invariant
@pytest.mark.parametrize(
    "invalid_kind",
    [
        "descriptor_only",
        "out_of_context",
        "service_owned",
        "omitted_claim_kind",
        "omitted_occurred",
    ],
)
def test_reference_and_service_authorship_invalidity_retry_once(
    workspace: Path, invalid_kind: str
) -> None:
    """§15.1/§15.11: invalid scoped references and service fields fail closed."""

    _root, root_items, _correction, correction_items = displaced_lineage(workspace)
    if invalid_kind == "descriptor_only":
        invalid = fact_response([root_items[1].id])
    elif invalid_kind == "out_of_context":
        invalid = fact_response(["evi_vera_unknown"])
    elif invalid_kind == "omitted_claim_kind":
        # An omitted model judgment must retry/fail, never silently
        # validate into the stronger observed_fact.
        invalid = fact_response(
            [correction_items[0].id], drop_fact_fields=("claim_kind",)
        )
    elif invalid_kind == "omitted_occurred":
        # Only an explicit null is the authored inherit-governing decision;
        # a missing key is an absent temporal judgment.
        invalid = fact_response(
            [correction_items[0].id], drop_fact_fields=("occurred",)
        )
    else:
        invalid = fact_response(
            [correction_items[0].id],
            extra_fact_fields={"project": "Vera Example injected"},
        )
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError) as caught:
        run_stage3(workspace, fake, TestIds())
    assert caught.value.failure_code == "response_validation_failed"
    assert len(fake.calls) == 2
    run, calls = telemetry_rows(workspace, "run_vera_0001")
    assert run["status"] == calls[0]["status"] == "failed"
    assert calls[0]["schema_retries"] == 1
    assert calls[0]["output_hash"] is None
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()


@pytest.mark.invariant
@pytest.mark.parametrize(
    ("governing", "candidate"),
    [
        (month(), exact_day(5)),
        (
            month(),
            OccurredAt(
                start=datetime(2026, 7, 5, tzinfo=timezone.utc),
                end=datetime(2026, 7, 7, tzinfo=timezone.utc),
                precision="date_range",
                confidence="medium",
            ),
        ),
        (
            OccurredAt(
                start=datetime(2026, 7, 1, tzinfo=timezone.utc),
                end=datetime(2026, 7, 10, tzinfo=timezone.utc),
                precision="approximate_range",
                confidence="medium",
            ),
            OccurredAt(
                start=datetime(2026, 7, 1, tzinfo=timezone.utc),
                end=datetime(2026, 7, 10, tzinfo=timezone.utc),
                precision="date_range",
                confidence="medium",
            ),
        ),
        (
            month(),
            OccurredAt(
                start=datetime(2026, 6, 30, tzinfo=timezone.utc),
                end=datetime(2026, 8, 2, tzinfo=timezone.utc),
                precision="date_range",
                confidence="medium",
            ),
        ),
        (month(confidence="low"), month(confidence="high")),
    ],
    ids=(
        "unsupported-exact-day",
        "unsupported-two-day-range",
        "approximate-to-exact-upgrade",
        "widening",
        "raised-temporal-confidence",
    ),
)
def test_temporal_upgrades_widening_and_confidence_raise_are_rejected(
    workspace: Path, governing: OccurredAt, candidate: OccurredAt
) -> None:
    """§16.7: anchored support, containment, exactness, and confidence all bind."""

    _log, items = add_log(
        workspace,
        log_id="log_vera_temporal",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example worked during the supplied source placement.",
        occurred=governing,
        item_specs=(("evi_vera_temporal", "manual_claim"),),
    )
    invalid = fact_response(
        [items[0].id], occurred=temporal_payload(candidate)
    )
    fake = FakeContractRunner([invalid, invalid])
    with pytest.raises(LLMInvocationError):
        run_stage3(workspace, fake, TestIds())
    assert len(fake.calls) == 2
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()


@pytest.mark.invariant
def test_selected_effective_placement_can_license_anchored_narrowing(
    workspace: Path,
) -> None:
    """§16.7: an explicit sibling placement licenses only its anchored interval."""

    root, _root_items = add_log(
        workspace,
        log_id="log_vera_temporal_root",
        recorded_at=FIXED_NOW - timedelta(hours=4),
        raw_text="Vera Example root statement later corrected twice.",
        occurred=month(day=15),
        item_specs=(("evi_vera_temporal_root", "manual_claim"),),
    )
    _support, support_items = add_log(
        workspace,
        log_id="log_vera_temporal_support",
        recorded_at=FIXED_NOW - timedelta(hours=2),
        raw_text="Vera Example explicitly states work on July 20.",
        occurred=exact_day(20),
        item_specs=(("evi_vera_temporal_support", "manual_claim"),),
        corrects_log_id=root.id,
    )
    _governing, governing_items = add_log(
        workspace,
        log_id="log_vera_temporal_governing",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example restates the work within the July 15 source month.",
        occurred=month(day=15),
        item_specs=(("evi_vera_temporal_governing", "manual_claim"),),
        corrects_log_id=root.id,
    )
    candidate = exact_day(20)
    result = run_stage3(
        workspace,
        FakeContractRunner(
            [
                fact_response(
                    [support_items[0].id, governing_items[0].id],
                    occurred=temporal_payload(candidate),
                )
            ]
        ),
        TestIds(),
    )
    with read_database(workspace) as connection:
        fact = list_experience_facts(connection)[0]
    assert result.created == (fact.id,)
    assert fact.occurred == candidate
    assert fact.source_log_ids == sorted(
        ["log_vera_temporal_support", "log_vera_temporal_governing"]
    )


@pytest.mark.invariant
def test_confidence_ceiling_and_independent_displaced_support(
    workspace: Path,
) -> None:
    """§9.4: one log caps at medium; two logs plus non-manual support allow high."""

    _log, items = add_log(
        workspace,
        log_id="log_vera_ceiling_single",
        recorded_at=FIXED_NOW - timedelta(hours=4),
        raw_text="Vera Example made one owner assertion.",
        occurred=month(),
        item_specs=(("evi_vera_ceiling_single", "manual_claim"),),
    )
    ids = TestIds()
    invalid_high = fact_response([items[0].id], confidence="high")
    with pytest.raises(LLMInvocationError):
        run_stage3(
            workspace, FakeContractRunner([invalid_high, invalid_high]), ids,
            log_id="log_vera_ceiling_single",
        )
    accepted = run_stage3(
        workspace,
        FakeContractRunner([fact_response([items[0].id], confidence="medium")]),
        ids,
        log_id="log_vera_ceiling_single",
    )
    assert len(accepted.created) == 1

    root, root_items, _correction, correction_items = displaced_lineage(workspace)
    high = run_stage3(
        workspace,
        FakeContractRunner(
            [
                fact_response(
                    [correction_items[0].id, root_items[1].id],
                    confidence="high",
                )
            ]
        ),
        ids,
        log_id=root.id,
    )
    assert len(high.created) == 1
    with read_database(workspace) as connection:
        fact = next(
            item for item in list_experience_facts(connection) if item.id in high.created
        )
    assert fact.confidence == "high"
    assert set(fact.source_log_ids) == {
        "log_vera_displaced_root",
        "log_vera_correction",
    }


@pytest.mark.lifecycle
def test_zero_lineage_workspace_completes_empty_run_without_call(
    workspace: Path,
) -> None:
    """§15.10: the empty complete set still has one completed run."""

    fake = FakeContractRunner([])
    result = run_stage3(workspace, fake, TestIds())
    assert result.created == result.superseded == result.generation_ids == ()
    assert result.warnings == ()
    assert fake.calls == []
    run, calls = telemetry_rows(workspace, result.run_id)
    assert run["status"] == "completed"
    assert json.loads(run["output_ids_json"]) == []
    assert calls == ()


@pytest.mark.unit
def test_log_selector_resolves_correction_to_root_lineage_and_rejects_unknown(
    workspace: Path,
) -> None:
    """§13.3 rule 10: either retained member selects the same planned lineage."""

    root, _root_items, correction, _correction_items = displaced_lineage(workspace)
    with read_database(workspace) as connection:
        by_root = plan_lineages(connection, log_id=root.id)
        by_correction = plan_lineages(connection, log_id=correction.id)
        assert by_root == by_correction
        with pytest.raises(SelectorNotFoundError):
            plan_lineages(connection, log_id="log_vera_missing")
    with pytest.raises(SelectorNotFoundError):
        run_stage3(
            workspace,
            FakeContractRunner([]),
            TestIds(),
            log_id="log_vera_missing",
        )


@pytest.mark.lifecycle
def test_keyboard_interrupt_cancels_call_and_keeps_prior_generation(
    workspace: Path,
) -> None:
    """§15.10 rule 8: cancellation exposes no partial new business state."""

    _first_log, first_items = add_log(
        workspace,
        log_id="log_vera_cancel_a",
        recorded_at=FIXED_NOW - timedelta(hours=2),
        raw_text="Vera Example cancellation lineage A.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_cancel_a", "manual_claim"),),
    )
    _second_log, second_items = add_log(
        workspace,
        log_id="log_vera_cancel_b",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example cancellation lineage B.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_cancel_b", "manual_claim"),),
    )
    ids = TestIds()
    prior = run_stage3(
        workspace,
        FakeContractRunner(
            [
                fact_response([first_items[0].id]),
                fact_response([second_items[0].id]),
            ]
        ),
        ids,
    )

    def interrupt(_call: PreparedCall):
        raise KeyboardInterrupt()

    fake = FakeContractRunner([interrupt])
    with pytest.raises(LLMCancelledError):
        run_stage3(workspace, fake, ids)
    run, calls = telemetry_rows(workspace, "run_vera_0002")
    assert run["failure_code"] == calls[0]["failure_code"] == "cancelled"
    assert run["status"] == calls[0]["status"] == "failed"
    with read_database(workspace) as connection:
        assert tuple(item.id for item in list_experience_facts(connection)) == prior.created


@pytest.mark.lifecycle
def test_per_run_call_ceiling_fails_before_transport(
    workspace: Path,
) -> None:
    """§15.10 rule 5: planned lineage count is preflighted before call 1 transport."""

    for index in range(2):
        add_log(
            workspace,
            log_id=f"log_vera_budget_{index}",
            recorded_at=FIXED_NOW - timedelta(hours=2 - index),
            raw_text=f"Vera Example budget lineage {index}.",
            occurred=exact_day(14 + index),
            item_specs=((f"evi_vera_budget_{index}", "manual_claim"),),
        )
    fake = FakeContractRunner([empty_response(), empty_response()])
    with pytest.raises(LLMInvocationError) as caught:
        run_stage3(
            workspace,
            fake,
            TestIds(),
            call_budgets=budgets(per_run_call_ceiling=1),
        )
    assert caught.value.failure_code == "budget_exceeded"
    assert fake.calls == []
    run, calls = telemetry_rows(workspace, "run_vera_0001")
    assert run["failure_code"] == calls[0]["failure_code"] == "budget_exceeded"
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()


def test_stage_default_token_patterns_come_from_registration(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§29.4: stage paths derive secret classifiers from the selected registration."""

    registered = ADAPTER_REGISTRY["codex-cli"]
    doctored = dataclass_replace(
        registered.declaration,
        token_patterns=(
            *registered.declaration.token_patterns,
            re.compile(rb"VERA-EXAMPLE-ADAPTER-TOKEN-\d{6}"),
        ),
    )
    monkeypatch.setitem(
        ADAPTER_REGISTRY,
        "codex-cli",
        AdapterRegistration(
            adapter_id=registered.adapter_id,
            declaration=doctored,
            build_runner=registered.build_runner,
            classify_failure=registered.classify_failure,
        ),
    )
    add_log(
        workspace,
        log_id="log_vera_secret",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Deployed with VERA-EXAMPLE-ADAPTER-TOKEN-123456 in the pipeline.",
        occurred=exact_day(14),
        item_specs=(("evi_vera_secret", "manual_claim"),),
    )
    fake = FakeContractRunner([empty_response()])
    with pytest.raises(LLMInvocationError) as caught:
        run_stage3(workspace, fake, TestIds())
    assert caught.value.failure_code == "credential_detected"
    assert fake.calls == []
    run, calls = telemetry_rows(workspace, "run_vera_0001")
    assert run["failure_code"] == calls[0]["failure_code"] == "credential_detected"


@pytest.mark.lifecycle
def test_business_commit_failure_rolls_back_fact_and_fails_run(
    workspace: Path,
) -> None:
    """The complete-stage seam couples rollback to business_commit_failed."""

    _log, items = add_log(
        workspace,
        log_id="log_vera_commit_failure",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example commit-failure lineage.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_commit_failure", "manual_claim"),),
    )

    class CollidingFactIds(TestIds):
        def __call__(self, kind: str) -> str:
            if kind == "fact":
                return "fact_vera_collision"
            return super().__call__(kind)

    first = json.loads(fact_response([items[0].id]))["facts"][0]
    second = {**first, "claim": "Vera Example designed a second atomic fact."}
    response = json.dumps(
        {"facts": [first, second], "warnings": []},
        separators=(",", ":"),
    ).encode()
    with pytest.raises(LLMInvocationError) as caught:
        run_stage3(
            workspace,
            FakeContractRunner([response]),
            CollidingFactIds(),
        )
    assert caught.value.failure_code == "business_commit_failed"
    run, calls = telemetry_rows(workspace, "run_vera_0001")
    assert run["failure_code"] == "business_commit_failed"
    assert calls[0]["status"] == "completed"
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()


@pytest.mark.lifecycle
def test_interrupt_during_final_swap_rolls_back_and_cancels_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§15.10 rule 8: an interrupt after validation commits no fact."""

    _log, items = add_log(
        workspace,
        log_id="log_vera_swap_interrupt",
        recorded_at=FIXED_NOW - timedelta(hours=1),
        raw_text="Vera Example final-swap cancellation lineage.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_swap_interrupt", "manual_claim"),),
    )
    from exp2res.pipeline import stage3

    original_insert = stage3.insert_experience_fact

    def insert_then_interrupt(*args, **kwargs) -> None:
        original_insert(*args, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(stage3, "insert_experience_fact", insert_then_interrupt)
    with pytest.raises(LLMCancelledError):
        run_stage3(
            workspace,
            FakeContractRunner([fact_response([items[0].id])]),
            TestIds(),
        )
    run, calls = telemetry_rows(workspace, "run_vera_0001")
    assert run["failure_code"] == "cancelled"
    assert calls[0]["status"] == "completed"
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()
