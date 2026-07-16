"""§11.4/§12 fact model, persistence, hydration, and lifecycle tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError
import pytest

from exp2res.domain.models import (
    ExperienceFact,
    OccurredAt,
    RawLog,
    canonical_project_key,
)
from exp2res.errors import (
    HydrationFailureError,
    IdCollisionError,
    IntegrityFailureError,
)
from exp2res.services.capture import capture_daily
from exp2res.services.logs import show_log
from exp2res.storage.repository import (
    get_experience_fact,
    insert_experience_fact,
    list_experience_facts,
    mark_facts_superseded,
)
from exp2res.storage.telemetry import create_processing_run, finish_processing_run
from exp2res.storage.workspace import read_database, writer_database

from conftest import FIXED_NOW


pytestmark = [pytest.mark.unit, pytest.mark.lifecycle]

FACT_A = "fact_" + "a" * 32
FACT_B = "fact_" + "b" * 32
RUN_A = "run_" + "a" * 32
RUN_B = "run_" + "b" * 32
GEN_A = "gen_" + "a" * 32


def fact_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": FACT_A,
        "created_at": FIXED_NOW,
        "superseded_at": None,
        "claim": "Vera Example built an offline provenance store.",
        "claim_kind": "observed_fact",
        "project": " Exp2Res ",
        "role": "Engineer",
        "company": None,
        "context": "independent_project",
        "ownership_level": "built",
        "action": "Built",
        "object": "an offline provenance store",
        "outcome": "Strict local persistence",
        "skills": ["Schema design"],
        "technologies": ["SQLite"],
        "themes": ["Local-first"],
        "occurred": OccurredAt(
            start=FIXED_NOW,
            end=None,
            precision="exact_day",
            confidence="high",
        ),
        "source_log_ids": ["log_" + "a" * 32],
        "evidence_item_ids": ["evi_" + "a" * 32],
        "confidence": "high",
        "metadata": {},
    }
    values.update(overrides)
    return values


def make_fact(*, raw_log_id: str, evidence_item_id: str, **overrides: object) -> ExperienceFact:
    return ExperienceFact(
        **fact_values(
            source_log_ids=[raw_log_id],
            evidence_item_ids=[evidence_item_id],
            **overrides,
        )
    )


def persist_fact(
    workspace: Path,
    fact: ExperienceFact,
    *,
    run_id: str = RUN_A,
    generation_id: str = GEN_A,
) -> None:
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        create_processing_run(
            connection,
            run_id=run_id,
            stage="13.3",
            started_at=FIXED_NOW,
            provider=None,
            model=None,
            prompt_policy_hash=None,
            input_ids=fact.source_log_ids,
        )
        insert_experience_fact(
            connection,
            fact,
            project_key=(
                None
                if fact.project is None
                else canonical_project_key(fact.project)
            ),
            produced_by_run_id=run_id,
            generation_id=generation_id,
        )
        finish_processing_run(
            connection,
            run_id=run_id,
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=[fact.id],
        )
        connection.commit()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (" Exp2Res ", "exp2res"),
        ("Exp2Re\u0301s", "Exp2Rés"),
        ("Straße", "STRASSE"),
    ],
)
@pytest.mark.invariant
def test_canonical_project_key_equivalence_and_idempotence(
    left: str, right: str
) -> None:
    """§12 rule 14: project identity is NFC, trimmed, case-folded once."""

    key = canonical_project_key(left)
    assert key == canonical_project_key(right)
    assert canonical_project_key(key) == key
    assert canonical_project_key(" \t\n ") == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"created_at": datetime(2026, 7, 15, 12, 30)},
        {"superseded_at": datetime(2026, 7, 15, 12, 30)},
        {"source_log_ids": []},
        {"evidence_item_ids": []},
        {"source_log_ids": ["log_a", "log_a"]},
        {"evidence_item_ids": ["evi_a", "evi_a"]},
        {"project": "   "},
        {"skills": [""]},
        {"technologies": ["x"] * 1_001},
        {"metadata": {"note": "x" * 4_097}},
    ],
    ids=(
        "naive-created",
        "naive-superseded",
        "empty-source-logs",
        "empty-evidence-items",
        "duplicate-source-logs",
        "duplicate-evidence-items",
        "blank-project",
        "empty-string-list-member",
        "oversize-string-list",
        "oversize-metadata",
    ),
)
@pytest.mark.invariant
def test_experience_fact_rejects_strict_shape_and_limit_violations(
    overrides: dict[str, object]
) -> None:
    """§11.4 model policy: fact timestamps, lists, project, and metadata are strict."""

    with pytest.raises(ValidationError):
        ExperienceFact(**fact_values(**overrides))


def test_experience_fact_json_hydration_rejects_invalid_occurred_shape() -> None:
    """§11.1/§11.4: embedded OccurredAt keeps its discriminator rules."""

    payload = ExperienceFact(**fact_values()).model_dump(mode="json")
    payload["occurred"] = {
        "start": FIXED_NOW.isoformat(),
        "end": FIXED_NOW.isoformat(),
        "precision": "exact_day",
        "confidence": "high",
    }
    with pytest.raises(ValidationError):
        ExperienceFact.model_validate_json(json.dumps(payload))


def test_raw_log_model_rejects_blank_canonical_project_label() -> None:
    """§11 model policy: RawLog.project shares the non-blank label boundary."""

    with pytest.raises(ValidationError):
        RawLog(
            id="log_" + "a" * 32,
            recorded_at=FIXED_NOW,
            entry_type="manual_daily",
            source_type="manual_entry",
            occurred=fact_values()["occurred"],
            raw_text="Vera Example invalid project record",
            project=" \t ",
        )


def test_fact_round_trip_derives_ordered_sources_and_preserves_production_identity(
    workspace: Path,
) -> None:
    """§12 rules 8/13/14: fact hydration derives sources and keeps run identity."""

    first = capture_daily(
        workspace,
        raw_text="Vera Example first fact source",
        project="Straße",
        clock=lambda: FIXED_NOW,
    )
    second = capture_daily(
        workspace,
        raw_text="Vera Example second fact source",
        project="Straße",
        clock=lambda: FIXED_NOW.replace(hour=13),
    )
    evidence_ids = sorted(
        [first.evidence_items[0].id, second.evidence_items[0].id],
        key=lambda value: value.encode("utf-8"),
    )
    source_ids = sorted(
        [first.raw_log.id, second.raw_log.id],
        key=lambda value: value.encode("utf-8"),
    )
    fact = ExperienceFact(
        **fact_values(
            project="Straße",
            source_log_ids=source_ids,
            evidence_item_ids=evidence_ids,
        )
    )
    persist_fact(workspace, fact)

    with read_database(workspace) as connection:
        assert get_experience_fact(connection, fact.id) == fact
        assert list_experience_facts(connection) == (fact,)
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
    assert tuple(stored) == ("strasse", RUN_A, GEN_A)
    assert sorted(tuple(row) for row in sources) == sorted(
        (item, "direct") for item in evidence_ids
    )


def test_fact_listing_orders_by_utc_instant_then_id_bytes(workspace: Path) -> None:
    """§12 rule 3: inspection ordering never compares stored offset text."""

    first = capture_daily(
        workspace,
        raw_text="Vera Example earlier UTC fact source",
        clock=lambda: FIXED_NOW,
    )
    second = capture_daily(
        workspace,
        raw_text="Vera Example later UTC fact source",
        clock=lambda: FIXED_NOW.replace(hour=13),
    )
    earlier = make_fact(
        raw_log_id=first.raw_log.id,
        evidence_item_id=first.evidence_items[0].id,
        id=FACT_A,
        created_at=datetime(
            2026, 7, 15, 14, 0, tzinfo=timezone(timedelta(hours=2))
        ),
        project=None,
    )
    later = make_fact(
        raw_log_id=second.raw_log.id,
        evidence_item_id=second.evidence_items[0].id,
        id=FACT_B,
        created_at=FIXED_NOW,
        project=None,
    )
    persist_fact(workspace, later, run_id=RUN_B, generation_id="gen_" + "b" * 32)
    persist_fact(workspace, earlier)
    with read_database(workspace) as connection:
        assert [fact.id for fact in list_experience_facts(connection)] == [
            FACT_A,
            FACT_B,
        ]


def test_fact_hydration_fails_closed_on_project_key_drift_and_zero_sources(
    workspace: Path,
) -> None:
    """§12 rules 2/8/14: corrupt comparison identity or provenance never hydrates."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example hydration corruption source",
        project="Exp2Res",
        clock=lambda: FIXED_NOW,
    )
    fact = make_fact(
        raw_log_id=bundle.raw_log.id,
        evidence_item_id=bundle.evidence_items[0].id,
        project="Exp2Res",
    )
    persist_fact(workspace, fact)
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TRIGGER experience_facts_lifecycle_update_guard")
        connection.execute(
            "UPDATE experience_facts SET project_key = 'drift' WHERE id = ?",
            (fact.id,),
        )
        connection.commit()
    with read_database(workspace) as connection:
        with pytest.raises(HydrationFailureError):
            get_experience_fact(connection, fact.id)

    second = capture_daily(
        workspace,
        raw_text="Vera Example zero-source fact",
        clock=lambda: FIXED_NOW.replace(hour=14),
    )
    second_fact = make_fact(
        raw_log_id=second.raw_log.id,
        evidence_item_id=second.evidence_items[0].id,
        id=FACT_B,
        project=None,
    )
    persist_fact(workspace, second_fact, run_id=RUN_B)
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM fact_sources WHERE fact_id = ?", (FACT_B,))
        connection.commit()
    with read_database(workspace) as connection:
        with pytest.raises(HydrationFailureError):
            get_experience_fact(connection, FACT_B)


def test_raw_log_hydration_fails_closed_on_stored_project_key_drift(
    workspace: Path,
) -> None:
    """§12 rule 14: raw-log project provenance and stored identity agree."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example raw project-key drift source",
        project="Exp2Res",
        clock=lambda: FIXED_NOW,
    )
    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE raw_logs SET project_key = 'drift' WHERE id = ?",
            (bundle.raw_log.id,),
        )
        connection.commit()
    with pytest.raises(HydrationFailureError):
        show_log(workspace, log_id=bundle.raw_log.id)


def test_fact_lifecycle_guards_allow_only_one_supersession_and_owner_purge(
    workspace: Path,
) -> None:
    """§11 lifecycle/§12 guards: payload is immutable and cascaded purge is owner-only."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example fact lifecycle source",
        clock=lambda: FIXED_NOW,
    )
    fact = make_fact(
        raw_log_id=bundle.raw_log.id,
        evidence_item_id=bundle.evidence_items[0].id,
        project=None,
    )
    persist_fact(workspace, fact)

    with writer_database(workspace) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="experience_fact_lifecycle_only"):
            connection.execute(
                "UPDATE experience_facts SET claim = 'Vera Example rewrite' WHERE id = ?",
                (fact.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="experience_fact_owner_purge_required"):
            connection.execute("DELETE FROM experience_facts WHERE id = ?", (fact.id,))

    superseded_at = FIXED_NOW.replace(hour=15)
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        mark_facts_superseded(connection, [fact.id], superseded_at)
        connection.commit()
    with read_database(workspace) as connection:
        assert list_experience_facts(connection) == ()
        historical = list_experience_facts(connection, current_only=False)
        assert historical[0].superseded_at == superseded_at
        production = connection.execute(
            "SELECT produced_by_run_id, generation_id FROM experience_facts WHERE id = ?",
            (fact.id,),
        ).fetchone()
    assert tuple(production) == (RUN_A, GEN_A)

    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(IntegrityFailureError):
            mark_facts_superseded(connection, [fact.id], superseded_at)
        connection.rollback()

    with writer_database(workspace, owner_delete=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError, match="fact_source_immutable"):
            connection.execute(
                "UPDATE fact_sources SET support_type = 'corroborating' WHERE fact_id = ?",
                (fact.id,),
            )
        connection.execute("DELETE FROM experience_facts WHERE id = ?", (fact.id,))
        connection.commit()
    with read_database(workspace) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM fact_sources WHERE fact_id = ?", (fact.id,)
        ).fetchone()[0] == 0


def test_fact_insert_requires_valid_run_and_maps_identity_collision(
    workspace: Path,
) -> None:
    """§12 rules 10/11/13: run FK is mandatory and fact IDs never overwrite."""

    bundle = capture_daily(
        workspace,
        raw_text="Vera Example fact identity source",
        clock=lambda: FIXED_NOW,
    )
    fact = make_fact(
        raw_log_id=bundle.raw_log.id,
        evidence_item_id=bundle.evidence_items[0].id,
        project=None,
    )
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(IntegrityFailureError):
            insert_experience_fact(
                connection,
                fact,
                project_key=None,
                produced_by_run_id="run_" + "f" * 32,
                generation_id=GEN_A,
            )
        connection.rollback()

    persist_fact(workspace, fact)
    with writer_database(workspace) as connection:
        connection.execute("BEGIN IMMEDIATE")
        mismatched = make_fact(
            raw_log_id=bundle.raw_log.id,
            evidence_item_id=bundle.evidence_items[0].id,
            id=FACT_B,
            project=None,
        )
        with pytest.raises(IntegrityFailureError):
            insert_experience_fact(
                connection,
                mismatched,
                project_key="unexpected",
                produced_by_run_id=RUN_A,
                generation_id=GEN_A,
            )
        with pytest.raises(IdCollisionError):
            insert_experience_fact(
                connection,
                fact,
                project_key=None,
                produced_by_run_id=RUN_A,
                generation_id=GEN_A,
            )
        connection.rollback()
