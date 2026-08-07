"""Shared fact-graph builders for the §13.6 assessment and export tests.

These lived beside the §13.5 signal tests until issue #76 removed that stage.
They build the fact substrate every assessment-layer test needs, so they now
sit in their own module rather than inside whichever test file happens to own
the stage that consumes them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json
from pathlib import Path

from exp2res.storage.workspace import writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_stage3_extraction import add_log, exact_day, fact_response, run_stage3


class VeraIds:
    __test__ = False

    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        self.counts[kind] += 1
        return f"{kind}_vera_{self.counts[kind]:04d}"


def multi_fact_response(
    evidence_ids: list[str], *, count: int, confidence: str = "medium"
) -> bytes:
    template = json.loads(
        fact_response(evidence_ids, confidence=confidence).decode("utf-8")
    )["facts"][0]
    facts = []
    for index in range(count):
        candidate = dict(template)
        candidate["claim"] = f"Designed provenance workflow slice {index + 1}."
        facts.append(candidate)
    return json.dumps(
        {"facts": facts, "warnings": []}, separators=(",", ":")
    ).encode("utf-8")


def prepare_facts(
    workspace: Path,
    ids: VeraIds,
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
    workspace: Path, ids: VeraIds
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


def clone_high_facts(
    workspace: Path, source_fact_ids: tuple[str, ...]
) -> tuple[str, ...]:
    """Copy facts at `high` confidence, keeping each clone's own source rows."""

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
