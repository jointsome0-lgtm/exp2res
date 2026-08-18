"""§19 source-local importer acceptance tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

import pytest

from exp2res.config import load_workspace_config
from exp2res.errors import (
    ForbiddenPathError,
    ImportPayloadChangedError,
    ImportPayloadInvalidError,
    ImportPayloadTooLargeError,
    IntegrityFailureError,
    InvalidInputError,
    OperationCancelledError,
)
from exp2res.integrations import CONTRACTS
from exp2res.integrations.records import PayloadRecords
from exp2res.services.capture import new_id
from exp2res.services.imports import ImportOutcome, import_payload
from exp2res.services.source_files import PayloadFile, open_payload_file

from conftest import FIXED_NOW

pytestmark = [pytest.mark.unit, pytest.mark.lifecycle]

MARKER = "Vera Example"
ATLAS_AS_OF = "2026-07-14T20:00:00+02:00"
ATLAS_START = "2026-07-01T00:00:00+02:00"
ATLAS_END = "2026-07-14T20:00:00+02:00"
ATLAS_SUMMARY = (
    "Vera Example studied provenance and verifier-gate design through an "
    "evidence-backed trail."
)
ATLAS_TEXT = (
    f"Atlas snapshot as of {ATLAS_AS_OF}. Summary: {ATLAS_SUMMARY} "
    "Knowledge state: subject provenance; scale atlas_learning_stage; "
    "value studied. Trail: Vera Example verifier-gate design trail from "
    f"{ATLAS_START} to {ATLAS_END} with date_range precision and high "
    "confidence. Evidence reference: atlas:evidence:vera-example-design."
)
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


def ephemeris_record(record_id: str = "vera-ephemeris-0001", **overrides: Any) -> dict:
    record: dict[str, Any] = {
        "source": "ephemeris",
        "record_id": record_id,
        "domain": "activity",
        "occurred": {
            "start": "2026-07-03T10:00:00+02:00",
            "end": None,
            "precision": "exact_datetime",
            "confidence": "high",
        },
        "project": "Vera Example Playbook",
        "text": "Vera Example worked on verifier-gate design.",
    }
    record.update(overrides)
    return record


def atlas_record(**overrides: Any) -> dict:
    record: dict[str, Any] = {
        "source": "atlas",
        "record_id": "atlas:snapshot:2026-07-14T20:00:00+02:00",
        "domain": "knowledge_state",
        "as_of": ATLAS_AS_OF,
        "occurred": {
            "start": ATLAS_START,
            "end": ATLAS_END,
            "precision": "date_range",
            "confidence": "high",
        },
        "text": ATLAS_TEXT,
        "summary": ATLAS_SUMMARY,
        "knowledge_state": [
            {
                "subject": "provenance",
                "scale": "atlas_learning_stage",
                "value": "studied",
            }
        ],
        "trail_segments": [
            {
                "label": "Vera Example verifier-gate design trail",
                "occurred": {
                    "start": ATLAS_START,
                    "end": ATLAS_END,
                    "precision": "date_range",
                    "confidence": "high",
                },
            }
        ],
        "evidence_references": [{"reference": "atlas:evidence:vera-example-design"}],
        "path": None,
        "content_digest": None,
    }
    record.update(overrides)
    return record


def github_record(commit_sha: str = COMMIT_SHA, **overrides: Any) -> dict:
    record: dict[str, Any] = {
        "source": "github",
        "repo": "vera-example/playbook",
        "commit_sha": commit_sha,
        "message": "Vera Example: add verifier-gate schema",
        "files": ["exp2res/pipeline/verify_bullets.py"],
        "url": f"https://example.invalid/vera-example/playbook/commit/{commit_sha}",
        "author": {
            "name": "Vera Example",
            "email": "vera@example.invalid",
            "login": "vera-example",
        },
        "committer": {"name": None, "email": None, "login": None},
        "authored_at": "2026-07-14T09:15:00-04:00",
        "committed_at": "2026-07-14T14:20:00+01:00",
        "owner_attribution": "unknown",
    }
    record.update(overrides)
    return record


def write_payload(directory: Path, name: str, records: Any) -> str:
    path = directory / name
    if isinstance(records, list):
        body = "\n".join(json.dumps(record) for record in records) + "\n"
    else:
        body = json.dumps(records)
    path.write_text(body, encoding="utf-8")
    return str(path)


def payload_with_trailing_garbage() -> bytes:
    """A valid JSONL prefix long enough to outrun one buffered read."""

    valid = "\n".join(
        json.dumps(ephemeris_record(f"vera-ephemeris-{index:04d}"))
        for index in range(200)
    )
    return valid.encode("utf-8") + b"\n" + b"\xff" * 32 + b"\n"


def run_import(workspace: Path, source_system: str, payload: str) -> ImportOutcome:
    return import_payload(
        workspace,
        source_system=source_system,
        payload_path=payload,
        clock=lambda: FIXED_NOW,
    )


def counts(outcome: ImportOutcome) -> tuple[int, int, int]:
    return (
        len(outcome.accepted),
        len(outcome.duplicate),
        len(outcome.rejected),
    )


def database(workspace: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite")
    connection.row_factory = sqlite3.Row
    return connection


def raw_rows(workspace: Path) -> list[sqlite3.Row]:
    with database(workspace) as connection:
        return list(
            connection.execute("SELECT * FROM raw_logs ORDER BY recorded_at, id")
        )


def evidence_rows(workspace: Path) -> list[sqlite3.Row]:
    with database(workspace) as connection:
        return list(connection.execute("SELECT * FROM evidence_items ORDER BY id"))


def derived_counts(workspace: Path) -> tuple[int, int]:
    with database(workspace) as connection:
        facts = connection.execute("SELECT COUNT(*) FROM experience_facts").fetchone()
        claims = connection.execute("SELECT COUNT(*) FROM self_claims").fetchone()
    return facts[0], claims[0]


def test_ephemeris_round_trip_records_identity_and_linked_evidence(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.45 / §21.46; §24.48: one unwrapped §19.1 record, one atomic pair."""
    payload = write_payload(tmp_path, "ephemeris.jsonl", [ephemeris_record()])
    outcome = run_import(workspace, "ephemeris", payload)

    assert counts(outcome) == (1, 0, 0)
    accepted = outcome.accepted[0]
    assert accepted.record_number == 1
    assert accepted.source_record_id == "vera-ephemeris-0001"
    assert accepted.raw_log_id is not None

    rows = raw_rows(workspace)
    assert len(rows) == 1
    assert rows[0]["id"] == accepted.raw_log_id
    assert rows[0]["entry_type"] == "ephemeris_event"
    assert rows[0]["source_type"] == "imported_event"
    assert rows[0]["occurred_start"] == "2026-07-03T10:00:00+02:00"
    assert rows[0]["occurred_end"] is None
    assert rows[0]["temporal_precision"] == "exact_datetime"
    assert rows[0]["raw_text"] == "Vera Example worked on verifier-gate design."
    assert rows[0]["project"] == "Vera Example Playbook"
    # §19.4 rule 2: identity lives in metadata; external_ref keeps only its
    # source-provenance role and this contract reports no locator.
    assert rows[0]["external_ref"] is None
    metadata = json.loads(rows[0]["metadata_json"])
    assert set(metadata) == {"source_system", "source_record_id", "content_hash"}
    assert metadata["source_system"] == "ephemeris"
    assert metadata["source_record_id"] == "vera-ephemeris-0001"
    assert re.fullmatch(r"[0-9a-f]{64}", metadata["content_hash"]) is not None

    items = evidence_rows(workspace)
    assert len(items) == 1
    assert items[0]["raw_log_id"] == accepted.raw_log_id
    assert items[0]["strength"] == "imported_activity_event"
    assert items[0]["path"] is None and items[0]["uri"] is None
    assert derived_counts(workspace) == (0, 0)


def test_repeating_a_committed_import_is_a_counted_duplicate_no_op(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.45; §24.48: rerunning the same payload converges without a cursor."""
    payload = write_payload(tmp_path, "ephemeris.jsonl", [ephemeris_record()])
    first = run_import(workspace, "ephemeris", payload)
    before = [dict(row) for row in raw_rows(workspace)]
    before_items = [dict(row) for row in evidence_rows(workspace)]

    second = run_import(workspace, "ephemeris", payload)
    assert counts(second) == (0, 1, 0)
    duplicate = second.duplicate[0]
    assert duplicate.record_number == 1
    assert duplicate.source_record_id == first.accepted[0].source_record_id
    assert duplicate.raw_log_id is None
    assert [dict(row) for row in raw_rows(workspace)] == before
    assert [dict(row) for row in evidence_rows(workspace)] == before_items


def test_same_identity_with_different_content_is_rejected_not_merged(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.45; §24.48: no conflict class, and no in-place reinterpretation."""
    payload = write_payload(tmp_path, "first.jsonl", [ephemeris_record()])
    run_import(workspace, "ephemeris", payload)
    before = [dict(row) for row in raw_rows(workspace)]

    changed = write_payload(
        tmp_path,
        "changed.jsonl",
        [ephemeris_record(text="Vera Example rewrote the same event.")],
    )
    outcome = run_import(workspace, "ephemeris", changed)
    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].source_record_id == "vera-ephemeris-0001"
    assert outcome.rejected[0].raw_log_id is None
    assert outcome.rejected[0].reason == "content_hash_conflict"
    assert [dict(row) for row in raw_rows(workspace)] == before


def test_repeated_identity_inside_one_file_rejects_only_the_repeat(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.45; §24.48: one rejected record never withdraws an accepted one."""
    payload = write_payload(
        tmp_path,
        "mixed.jsonl",
        [
            ephemeris_record(),
            ephemeris_record(text="Vera Example supplied different content."),
        ],
    )
    outcome = run_import(workspace, "ephemeris", payload)

    assert counts(outcome) == (1, 0, 1)
    assert outcome.accepted[0].record_number == 1
    assert outcome.accepted[0].raw_log_id is not None
    assert outcome.rejected[0].record_number == 2
    assert outcome.rejected[0].raw_log_id is None
    rows = raw_rows(workspace)
    assert len(rows) == 1
    assert rows[0]["raw_text"] == "Vera Example worked on verifier-gate design."


def test_identical_content_under_distinct_identities_stays_independent(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.45: identity is the sole duplicate key."""
    payload = write_payload(
        tmp_path,
        "twins.jsonl",
        [ephemeris_record("vera-ephemeris-0001"), ephemeris_record("vera-ephemeris-0002")],
    )
    outcome = run_import(workspace, "ephemeris", payload)

    assert counts(outcome) == (2, 0, 0)
    assert [record.record_number for record in outcome.accepted] == [1, 2]
    identifiers = {record.raw_log_id for record in outcome.accepted}
    assert len(identifiers) == 2
    assert len(raw_rows(workspace)) == 2
    assert len(evidence_rows(workspace)) == 2


@pytest.mark.parametrize(
    ("name", "invalid", "reason"),
    [
        (
            "wrapping-envelope",
            {
                "contract_version": "1",
                "source_system": "ephemeris",
                "body": ephemeris_record("vera-ephemeris-0009"),
            },
            "record_invalid",
        ),
        (
            "record-supplied-hash",
            ephemeris_record("vera-ephemeris-0009", content_hash="0" * 64),
            "record_invalid",
        ),
        (
            "record-supplied-version",
            ephemeris_record("vera-ephemeris-0009", contract_version="1"),
            "record_invalid",
        ),
        ("foreign-source-record", github_record(), "record_source_mismatch"),
        (
            "structurally-invalid",
            ephemeris_record("vera-ephemeris-0009", text=""),
            "record_invalid",
        ),
        (
            "knowledge-state-member",
            ephemeris_record(
                "vera-ephemeris-0009",
                knowledge_state=[
                    {
                        "subject": "provenance",
                        "scale": "atlas_learning_stage",
                        "value": "studied",
                    }
                ],
            ),
            "record_invalid",
        ),
        (
            "float-value",
            ephemeris_record("vera-ephemeris-0009", occurred_hours=1.5),
            "record_float_value",
        ),
        ("not-an-object", ["vera-ephemeris-0009"], "record_not_object"),
    ],
)
def test_one_invalid_record_never_withdraws_the_valid_one(
    workspace: Path, tmp_path: Path, name: str, invalid: Any, reason: str
) -> None:
    """§21.45 / §21.46; §24.48: per-record classification in file order."""
    path = tmp_path / f"{name}.jsonl"
    path.write_text(
        json.dumps(ephemeris_record()) + "\n" + json.dumps(invalid) + "\n",
        encoding="utf-8",
    )
    outcome = run_import(workspace, "ephemeris", str(path))

    assert counts(outcome) == (1, 0, 1)
    assert outcome.accepted[0].record_number == 1
    assert outcome.rejected[0].record_number == 2
    assert outcome.rejected[0].raw_log_id is None
    assert outcome.rejected[0].reason == reason
    rows = raw_rows(workspace)
    assert len(rows) == 1
    assert json.loads(rows[0]["metadata_json"])["source_record_id"] == (
        "vera-ephemeris-0001"
    )


def test_rejected_record_identity_is_reported_when_it_is_itself_valid(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 5: source_record_id is null only for an unusable identity."""
    payload = write_payload(
        tmp_path,
        "identities.jsonl",
        [
            ephemeris_record("vera-ephemeris-0009", text=""),
            # Rejected by the parse-time scan, not by the model: its identity
            # is still perfectly usable and must still be reported.
            ephemeris_record("vera-ephemeris-0010", occurred_hours=1.5),
            ephemeris_record(record_id=""),
            ephemeris_record(record_id=17),
        ],
    )
    outcome = run_import(workspace, "ephemeris", payload)

    assert counts(outcome) == (0, 0, 4)
    assert [record.source_record_id for record in outcome.rejected] == [
        "vera-ephemeris-0009",
        "vera-ephemeris-0010",
        None,
        None,
    ]
    assert raw_rows(workspace) == []


def test_invalid_json_line_is_one_rejected_record_not_a_payload_failure(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rules 4–5: a JSONL line boundary establishes its record."""
    path = tmp_path / "broken.jsonl"
    path.write_text(
        json.dumps(ephemeris_record()) + "\n{not json}\n\n", encoding="utf-8"
    )
    outcome = run_import(workspace, "ephemeris", str(path))

    assert counts(outcome) == (1, 0, 1)
    assert outcome.rejected[0].record_number == 2
    assert outcome.rejected[0].reason == "record_not_json"


def test_duplicate_json_object_keys_reject_the_record(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 3: one payload may not carry two shapes for one hash."""
    path = tmp_path / "duplicate-keys.jsonl"
    line = json.dumps(ephemeris_record())
    doubled = line[:-1] + ', "text": "Vera Example second value"}'
    path.write_text(doubled + "\n", encoding="utf-8")
    outcome = run_import(workspace, "ephemeris", str(path))

    assert counts(outcome) == (0, 0, 1)
    assert raw_rows(workspace) == []


def test_a_duplicate_key_elsewhere_still_reports_the_record_identity(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 5: only the identity's own defect nulls source_record_id."""
    path = tmp_path / "duplicate-key-identities.jsonl"
    unrelated = json.dumps(ephemeris_record("vera-ephemeris-0021"))
    unrelated = unrelated[:-1] + ', "text": "Vera Example second value"}'
    nested = json.dumps(ephemeris_record("vera-ephemeris-0022")).replace(
        '"confidence": "high"', '"confidence": "high", "confidence": "low"'
    )
    doubled_identity = json.dumps(ephemeris_record("vera-ephemeris-0023"))
    doubled_identity = (
        doubled_identity[:-1] + ', "record_id": "vera-ephemeris-0024"}'
    )
    path.write_text(
        "\n".join([unrelated, nested, doubled_identity]) + "\n", encoding="utf-8"
    )
    outcome = run_import(workspace, "ephemeris", str(path))

    assert counts(outcome) == (0, 0, 3)
    assert [record.reason for record in outcome.rejected] == ["record_not_json"] * 3
    assert [record.source_record_id for record in outcome.rejected] == [
        "vera-ephemeris-0021",
        "vera-ephemeris-0022",
        # Two competing record_id values are an invalid identity, so neither
        # is picked and the report stays null.
        None,
    ]
    assert raw_rows(workspace) == []


def test_payload_over_the_object_limit_fails_before_persistence(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.45; §24.48: §11's object cap, with no second batch-size limit."""
    records = [
        ephemeris_record(f"vera-ephemeris-{index:05d}") for index in range(5_001)
    ]
    payload = write_payload(tmp_path, "oversize.jsonl", records)
    with pytest.raises(ImportPayloadTooLargeError) as failure:
        run_import(workspace, "ephemeris", payload)

    assert failure.value.diagnostic_class == "import_payload_too_large"
    assert failure.value.exit_code == 2
    assert raw_rows(workspace) == []


def test_a_rejected_record_still_counts_toward_the_object_limit(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 4: the object cap is the payload bound, not a per-record one.

    The float sits before the bulk, so a scan that stopped at the record's
    first defect would let everything behind it through uncounted.
    """
    bulk = [{"vera_example": index} for index in range(6_000)]
    hidden = ephemeris_record("vera-ephemeris-0009", occurred_hours=1.5, bulk=bulk)
    payload = write_payload(
        tmp_path, "hidden-bulk.jsonl", [ephemeris_record(), hidden, hidden]
    )
    with pytest.raises(ImportPayloadTooLargeError):
        run_import(workspace, "ephemeris", payload)

    assert raw_rows(workspace) == []


def test_a_multi_record_payload_is_read_one_record_at_a_time() -> None:
    """§19.4 rule 4: the object cap bounds objects, never resident bytes.

    A conforming JSONL file can spend that cap on records whose text fields
    alone exhaust memory, so neither the payload nor its decoded records may
    be held whole.
    """

    produced: list[int] = []

    class Source:
        def lines(self) -> Any:
            for index in range(4):
                produced.append(index)
                yield json.dumps(ephemeris_record(f"vera-ephemeris-{index:04d}"))

        def text(self) -> str:
            raise AssertionError("a multi-record payload is never read whole")

        def identity(self) -> tuple[int, int]:
            return (1, 2)

        def digest(self) -> str:
            return "vera-example-digest"

    records = PayloadRecords(Source(), contract=CONTRACTS["ephemeris"])
    assert records.total == 4

    produced.clear()
    seen = 0
    for _ in records:
        seen += 1
        # The source has produced exactly the lines consumed so far. A parser
        # returning a materialized tuple would have produced all four before
        # the first record reached the loop.
        assert produced == list(range(seen))
    assert seen == 4


def test_a_payload_is_decoded_as_it_is_read(workspace: Path, tmp_path: Path) -> None:
    """§19.4 rule 4: one pass reads the file, so its decode is incremental."""

    path = tmp_path / "late-garbage.jsonl"
    path.write_bytes(payload_with_trailing_garbage())
    with open_payload_file(str(path), config=load_workspace_config(workspace)) as (
        payload,
        root,
    ):
        assert root == tmp_path
        lines = payload.lines()
        # A whole-file decode would have raised before yielding anything.
        assert json.loads(next(lines))["record_id"] == "vera-ephemeris-0000"
        with pytest.raises(InvalidInputError) as failure:
            for _ in lines:
                pass
    assert failure.value.diagnostic_class == "input_not_utf8"


def test_a_payload_rewritten_between_passes_is_refused(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 4 partitions one payload, so every pass must see the same one.

    The open descriptor fixes the inode, not the bytes, so a rewrite in place
    would otherwise let one pass establish boundaries a later pass no longer
    finds.
    """

    path = tmp_path / "rewritten.jsonl"
    original = [ephemeris_record(f"vera-ephemeris-{index:04d}") for index in range(3)]
    write_payload(tmp_path, "rewritten.jsonl", original)
    with open_payload_file(str(path), config=load_workspace_config(workspace)) as (
        payload,
        _,
    ):
        records = PayloadRecords(payload, contract=CONTRACTS["ephemeris"])
        assert records.total == 3
        write_payload(tmp_path, "rewritten.jsonl", original[:1])
        with pytest.raises(ImportPayloadChangedError) as failure:
            iter(records)

    assert failure.value.diagnostic_class == "import_payload_changed"
    assert failure.value.exit_code == 2
    assert raw_rows(workspace) == []


def test_a_payload_rewritten_during_the_first_pass_is_refused() -> None:
    """The baseline is taken before the boundaries, not after.

    A rewrite that lands mid-scan and settles before it returns would
    otherwise become the baseline every later check agrees with, leaving
    `total` describing bytes no pass ever saw whole.
    """

    state = {"identity": (1, 2)}

    class Source:
        def lines(self) -> Any:
            for index in range(3):
                if index == 1:
                    state["identity"] = (9, 9)
                yield json.dumps(ephemeris_record(f"vera-ephemeris-{index:04d}"))

        def text(self) -> str:
            raise AssertionError("a multi-record payload is never read whole")

        def identity(self) -> tuple[int, int]:
            return state["identity"]

        def digest(self) -> str:
            return "vera-example-digest"

    with pytest.raises(ImportPayloadChangedError):
        PayloadRecords(Source(), contract=CONTRACTS["ephemeris"])


def test_a_payload_rewritten_while_the_writer_lock_is_awaited_is_refused() -> None:
    """The wait for the §8.1 writer lock sits between the two checks.

    `__iter__` runs before the lock, so a rewrite landing during the wait
    would otherwise be read, classified, and committed before the exhausted
    replay noticed.
    """

    state = {"identity": (1, 2)}

    class Source:
        def lines(self) -> Any:
            for index in range(3):
                yield json.dumps(ephemeris_record(f"vera-ephemeris-{index:04d}"))

        def text(self) -> str:
            raise AssertionError("a multi-record payload is never read whole")

        def identity(self) -> tuple[int, int]:
            return state["identity"]

        def digest(self) -> str:
            return "vera-example-digest"

    records = PayloadRecords(Source(), contract=CONTRACTS["ephemeris"])
    replay = iter(records)
    state["identity"] = (9, 9)
    with pytest.raises(ImportPayloadChangedError):
        next(replay)


def test_a_metadata_only_change_is_not_a_payload_change(
    workspace: Path, tmp_path: Path
) -> None:
    """Changing a payload's mode or owner does not change the payload.

    The refusal rests on size and modification time, neither of which a mode
    change touches, and on the digest of what each pass read, which it
    touches even less.
    """

    path = tmp_path / "chmod.jsonl"
    write_payload(
        tmp_path,
        "chmod.jsonl",
        [ephemeris_record(f"vera-ephemeris-{index:04d}") for index in range(2)],
    )
    with open_payload_file(str(path), config=load_workspace_config(workspace)) as (
        payload,
        _,
    ):
        records = PayloadRecords(payload, contract=CONTRACTS["ephemeris"])
        os.chmod(path, 0o600)
        assert [record.record_number for record in records] == [1, 2]


def test_a_rewrite_leaving_no_metadata_trace_is_caught_by_content(
    workspace: Path, tmp_path: Path
) -> None:
    """The digest is what makes the answer exact rather than merely cheap.

    Same length, modification time restored: nothing in the stat differs, so
    only comparing what the two passes actually read finds the substitution.
    """

    path = tmp_path / "substituted.jsonl"
    write_payload(
        tmp_path,
        "substituted.jsonl",
        [ephemeris_record(f"vera-ephemeris-{index:04d}") for index in range(2)],
    )
    with open_payload_file(str(path), config=load_workspace_config(workspace)) as (
        payload,
        _,
    ):
        records = PayloadRecords(payload, contract=CONTRACTS["ephemeris"])
        before = os.stat(path)
        write_payload(
            tmp_path,
            "substituted.jsonl",
            [ephemeris_record(f"vera-ephemeris-{index:04d}") for index in range(8, 10)],
        )
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert payload.identity() == (before.st_size, before.st_mtime_ns)

        replay = iter(records)
        with pytest.raises(ImportPayloadChangedError):
            for _ in replay:
                pass


def test_a_torn_payload_still_reports_its_complete_result(
    workspace: Path, tmp_path: Path
) -> None:
    """§14.14 rule 5: every classified boundary carries the full typed result.

    The substitution keeps every line's length, so the replay reads a mixture
    of both versions, classifies every boundary it was told to expect, and
    refuses on the digests. The result it carries is complete on its own
    terms rather than the null a record left unreached would earn.
    """

    def payload_records(project: str) -> list[dict]:
        return [
            ephemeris_record(f"vera-ephemeris-{index:04d}", project=project)
            for index in range(200)
        ]

    path = tmp_path / "torn.jsonl"
    write_payload(tmp_path, "torn.jsonl", payload_records("Vera Example Playbook"))
    rewritten = [False]

    def rewriting_factory(kind: str) -> str:
        if not rewritten[0]:
            rewritten[0] = True
            write_payload(
                tmp_path, "torn.jsonl", payload_records("Vera Example Notebook")
            )
        return new_id(kind)

    with pytest.raises(ImportPayloadChangedError) as failure:
        import_payload(
            workspace,
            source_system="ephemeris",
            payload_path=str(path),
            clock=lambda: FIXED_NOW,
            id_factory=rewriting_factory,
        )

    assert counts(failure.value.import_outcome) == (200, 0, 0)
    assert failure.value.import_classified is True
    assert len(raw_rows(workspace)) == 200


def test_a_payload_rewritten_during_the_import_is_reported(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 4 keeps committed records reportable behind that refusal.

    A rewrite that lands after the replay begins is past preventing, so the
    exhausted replay reconfirms rather than passing a torn payload off as a
    partition.
    """

    path = tmp_path / "rewritten.jsonl"
    original = [ephemeris_record(f"vera-ephemeris-{index:04d}") for index in range(3)]
    write_payload(tmp_path, "rewritten.jsonl", original)
    with open_payload_file(str(path), config=load_workspace_config(workspace)) as (
        payload,
        _,
    ):
        records = PayloadRecords(payload, contract=CONTRACTS["ephemeris"])
        replay = iter(records)
        assert next(replay).record_number == 1
        write_payload(tmp_path, "rewritten.jsonl", original + original)
        with pytest.raises(ImportPayloadChangedError):
            for _ in replay:
                pass


def test_a_signal_during_payload_teardown_reports_the_committed_records(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the payload closes inside the progress-carrying handlers.

    Teardown runs after the last record has committed, so a signal landing in
    it must reach the same cancellation report as one landing in the writer
    lock's teardown, not the CLI's generic envelope.
    """

    payload = write_payload(tmp_path, "teardown.jsonl", [ephemeris_record()])

    def interrupt(self: PayloadFile) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(PayloadFile, "release", interrupt)
    with pytest.raises(OperationCancelledError) as failure:
        run_import(workspace, "ephemeris", payload)

    assert counts(failure.value.import_outcome) == (1, 0, 0)
    assert failure.value.import_classified is True
    assert len(raw_rows(workspace)) == 1


def test_a_payload_that_is_not_utf8_commits_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 4: incremental decoding keeps this a payload-level refusal.

    The offending bytes sit past the first read, so a decode failure is found
    only after many records have already been established. None of them may
    reach the database on the way to it.
    """

    path = tmp_path / "late-garbage.jsonl"
    path.write_bytes(payload_with_trailing_garbage())
    with pytest.raises(InvalidInputError) as failure:
        run_import(workspace, "ephemeris", str(path))

    assert failure.value.diagnostic_class == "input_not_utf8"
    assert raw_rows(workspace) == []


def test_interrupted_file_rerun_converges_as_duplicates(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.45: the only retry unit is the same file, with no resume cursor."""
    records = [ephemeris_record(f"vera-ephemeris-{index:04d}") for index in range(4)]
    partial = write_payload(tmp_path, "partial.jsonl", records[:2])
    run_import(workspace, "ephemeris", partial)

    whole = write_payload(tmp_path, "whole.jsonl", records)
    outcome = run_import(workspace, "ephemeris", whole)
    assert counts(outcome) == (2, 2, 0)
    assert [record.record_number for record in outcome.duplicate] == [1, 2]
    assert [record.record_number for record in outcome.accepted] == [3, 4]
    assert len(raw_rows(workspace)) == 4

    replay = run_import(workspace, "ephemeris", whole)
    assert counts(replay) == (0, 4, 0)
    assert len(raw_rows(workspace)) == 4


def test_atlas_snapshot_round_trip_creates_one_pair_and_no_claim(
    workspace: Path, tmp_path: Path
) -> None:
    """§21.3 / §21.46; §24.49: a studied-grade snapshot promotes nothing."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    document = artifacts / "atlas-snapshot.txt"
    document.write_text("Vera Example snapshot document.\n", encoding="utf-8")
    digest = "a" * 64
    payload = write_payload(
        tmp_path,
        "atlas.json",
        atlas_record(path="artifacts/atlas-snapshot.txt", content_digest=digest),
    )
    outcome = run_import(workspace, "atlas", payload)

    assert counts(outcome) == (1, 0, 0)
    rows = raw_rows(workspace)
    assert len(rows) == 1
    assert rows[0]["entry_type"] == "atlas_snapshot"
    assert rows[0]["source_type"] == "imported_artifact"
    assert rows[0]["raw_text"] == ATLAS_TEXT
    assert rows[0]["occurred_start"] == ATLAS_START
    assert rows[0]["occurred_end"] == ATLAS_END
    assert rows[0]["external_ref"] is None

    items = evidence_rows(workspace)
    assert len(items) == 1
    assert items[0]["strength"] == "knowledge_state_snapshot"
    assert items[0]["summary"] == ATLAS_SUMMARY
    # §29.4 rule 15: the persisted locator is the canonical real path this
    # acquisition resolved, not the payload's relative spelling.
    assert items[0]["path"] == str(document.resolve())
    assert json.loads(items[0]["metadata_json"]) == {"content_digest": digest}
    assert derived_counts(workspace) == (0, 0)


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("summary-not-in-text", {"summary": "Vera Example unrendered summary."}),
        (
            "subject-not-in-text",
            {
                "knowledge_state": [
                    {
                        "subject": "kafka",
                        "scale": "atlas_learning_stage",
                        "value": "studied",
                    }
                ]
            },
        ),
        (
            "rewritten-bound-spelling",
            {
                "trail_segments": [
                    {
                        "label": "Vera Example verifier-gate design trail",
                        "occurred": {
                            "start": "2026-06-30T23:00:00+01:00",
                            "end": ATLAS_END,
                            "precision": "date_range",
                            "confidence": "high",
                        },
                    }
                ]
            },
        ),
        (
            "reference-not-in-text",
            {"evidence_references": [{"reference": "atlas:evidence:absent"}]},
        ),
        ("as-of-not-in-text", {"as_of": "2026-07-14T21:00:00+03:00"}),
        (
            "snapshot-bound-not-in-text",
            {
                "occurred": {
                    "start": "2026-06-01T00:00:00+02:00",
                    "end": ATLAS_END,
                    "precision": "date_range",
                    "confidence": "high",
                }
            },
        ),
    ],
)
def test_atlas_text_fidelity_failures_are_invalid_acquisition(
    workspace: Path, tmp_path: Path, name: str, overrides: dict
) -> None:
    """§19.2: the persisted projection carries every accepted source value."""
    payload = write_payload(tmp_path, f"{name}.json", atlas_record(**overrides))
    outcome = run_import(workspace, "atlas", payload)

    assert counts(outcome) == (0, 0, 1)
    assert raw_rows(workspace) == []


def test_a_snapshot_wider_than_its_trail_is_accepted_when_the_text_says_so(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.2's snapshot-wide bullet is satisfiable, not merely restrictive.

    The snapshot may legitimately span more than any segment; what the rule
    requires is that the source text render the wider bounds it claims.
    """

    earlier = "2026-06-01T00:00:00+02:00"
    payload = write_payload(
        tmp_path,
        "wide-snapshot.json",
        atlas_record(
            occurred={
                "start": earlier,
                "end": ATLAS_END,
                "precision": "date_range",
                "confidence": "high",
            },
            text=f"{ATLAS_TEXT} Snapshot span: {earlier} to {ATLAS_END}.",
        ),
    )
    outcome = run_import(workspace, "atlas", payload)

    assert counts(outcome) == (1, 0, 0)
    assert raw_rows(workspace)[0]["occurred_start"] == earlier


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        (
            "unknown-precision",
            {
                "occurred": {
                    "start": None,
                    "end": None,
                    "precision": "unknown",
                    "confidence": "unknown",
                }
            },
        ),
        (
            "open-ended-snapshot",
            {
                "occurred": {
                    "start": ATLAS_START,
                    "end": None,
                    "precision": "date_range",
                    "confidence": "high",
                }
            },
        ),
        (
            "segment-outside-snapshot",
            {
                "trail_segments": [
                    {
                        "label": "Vera Example verifier-gate design trail",
                        "occurred": {
                            "start": "2026-06-01T00:00:00+02:00",
                            "end": ATLAS_END,
                            "precision": "date_range",
                            "confidence": "high",
                        },
                    }
                ]
            },
        ),
        ("as-of-before-upper-bound", {"as_of": "2026-07-10T20:00:00+02:00"}),
        ("missing-required-nullable-path", {}),
        ("digest-without-path", {"content_digest": "b" * 64}),
        ("empty-knowledge-state", {"knowledge_state": []}),
    ],
)
def test_atlas_contract_violations_reject_before_persistence(
    workspace: Path, tmp_path: Path, name: str, overrides: dict
) -> None:
    """§19.2: temporal, digest-pairing, and knowledge-state requirements."""
    record = atlas_record(**overrides)
    if name == "missing-required-nullable-path":
        record.pop("path")
    payload = write_payload(tmp_path, f"{name}.json", record)
    outcome = run_import(workspace, "atlas", payload)

    assert counts(outcome) == (0, 0, 1)
    assert raw_rows(workspace) == []


@pytest.mark.parametrize(
    "locator",
    ["/etc/hostname", "../outside.txt", "missing.txt", "secrets/token.txt", ".env"],
    ids=["absolute", "escape", "unresolved", "denied-directory", "denied-name"],
)
def test_atlas_payload_locator_outside_selection_is_rejected(
    workspace: Path, tmp_path: Path, locator: str
) -> None:
    """§29.4 rule 8: only a relative locator beneath the payload root."""
    payload_root = tmp_path / "payload"
    payload_root.mkdir()
    (tmp_path / "outside.txt").write_text("Vera Example outside.\n", encoding="utf-8")
    secrets = payload_root / "secrets"
    secrets.mkdir()
    (secrets / "token.txt").write_text("Vera Example stand-in.\n", encoding="utf-8")
    (payload_root / ".env").write_text("VERA_EXAMPLE=1\n", encoding="utf-8")

    payload = write_payload(
        payload_root, "atlas.json", atlas_record(path=locator, content_digest=None)
    )
    outcome = run_import(workspace, "atlas", payload)

    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].reason.startswith("payload_locator_")
    assert raw_rows(workspace) == []


def test_atlas_duplicate_and_conflicting_replays(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rules 2 and 4 hold for a single-record source as well."""
    payload = write_payload(tmp_path, "atlas.json", atlas_record())
    assert counts(run_import(workspace, "atlas", payload)) == (1, 0, 0)
    assert counts(run_import(workspace, "atlas", payload)) == (0, 1, 0)

    changed = write_payload(
        tmp_path,
        "atlas-changed.json",
        atlas_record(text=ATLAS_TEXT + " Vera Example appended a sentence."),
    )
    outcome = run_import(workspace, "atlas", changed)
    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].reason == "content_hash_conflict"
    assert len(raw_rows(workspace)) == 1


NULL_IDENTITY = {"name": None, "email": None, "login": None}
STRING_IDENTITY = {
    "name": "Vera Example",
    "email": "vera@example.invalid",
    "login": "vera",
}


@pytest.mark.parametrize(
    ("sha", "attribution", "identity", "strength"),
    [
        (f"{index:040x}", attribution, identity, strength)
        for index, (attribution, identity, strength) in enumerate(
            (
                ("owner", NULL_IDENTITY, "commit_or_pr"),
                ("owner", STRING_IDENTITY, "commit_or_pr"),
                ("not_owner", NULL_IDENTITY, "artifact_reference"),
                ("not_owner", STRING_IDENTITY, "artifact_reference"),
                ("unknown", NULL_IDENTITY, "artifact_reference"),
                ("unknown", STRING_IDENTITY, "artifact_reference"),
                (None, NULL_IDENTITY, "artifact_reference"),
                (None, STRING_IDENTITY, "artifact_reference"),
            ),
            start=1,
        )
    ],
    ids=[
        "owner-null",
        "owner-strings",
        "not-owner-null",
        "not-owner-strings",
        "unknown-null",
        "unknown-strings",
        "omitted-null",
        "omitted-strings",
    ],
)
def test_github_attribution_matrix_maps_only_owner_to_commit_evidence(
    workspace: Path,
    tmp_path: Path,
    sha: str,
    attribution: str | None,
    identity: dict,
    strength: str,
) -> None:
    """§21.45; §24.49: identity strings never select or alter attribution."""
    record = github_record(sha, author=identity, committer=identity)
    if attribution is None:
        record.pop("owner_attribution")
    else:
        record["owner_attribution"] = attribution
    payload = write_payload(tmp_path, "github.json", record)
    outcome = run_import(workspace, "github", payload)

    assert counts(outcome) == (1, 0, 0)
    assert outcome.accepted[0].source_record_id == f"vera-example/playbook@{sha}"
    rows = raw_rows(workspace)
    assert rows[0]["entry_type"] == "github_commit"
    assert rows[0]["source_type"] == "imported_artifact"
    assert rows[0]["raw_text"] == "Vera Example: add verifier-gate schema"
    # §19.3 anchors the upstream commit instant and §12 rule 3 preserves its
    # supplied offset; authored_at never replaces it.
    assert rows[0]["occurred_start"] == "2026-07-14T14:20:00+01:00"
    assert rows[0]["temporal_precision"] == "exact_datetime"
    assert rows[0]["temporal_confidence"] == "high"
    assert rows[0]["external_ref"] == record["url"]

    items = evidence_rows(workspace)
    assert len(items) == 1
    assert items[0]["strength"] == strength
    assert items[0]["uri"] == record["url"]


@pytest.mark.parametrize(
    ("url", "accepted"),
    [
        # §29.4 rule 18 keeps no scheme allowlist, and RFC 3986 permits a
        # one-character scheme; only the `//` distinguishes it from a Windows
        # drive path, which stays unsupported.
        ("x://host.invalid/vera-example/playbook/commit", True),
        ("c:/vera-example/playbook/commit", False),
        ("file:///vera-example/playbook/commit", False),
    ],
)
def test_github_url_is_held_to_the_remote_locator_form(
    workspace: Path, tmp_path: Path, url: str, accepted: bool
) -> None:
    """§29.4 rule 18: an absolute non-`file` URI, whatever its scheme."""
    payload = write_payload(tmp_path, "url.json", github_record(url=url))
    outcome = run_import(workspace, "github", payload)

    assert counts(outcome) == ((1, 0, 0) if accepted else (0, 0, 1))
    if not accepted:
        assert outcome.rejected[0].reason == "github_url_not_remote"


def test_github_omitted_attribution_hashes_as_explicit_unknown(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.3: omission materializes `unknown` before canonical hashing."""
    omitted = github_record()
    omitted.pop("owner_attribution")
    payload = write_payload(tmp_path, "omitted.json", omitted)
    assert counts(run_import(workspace, "github", payload)) == (1, 0, 0)

    explicit = write_payload(
        tmp_path, "explicit.json", github_record(owner_attribution="unknown")
    )
    outcome = run_import(workspace, "github", explicit)
    assert counts(outcome) == (0, 1, 0)
    assert len(raw_rows(workspace)) == 1


@pytest.mark.parametrize(
    ("name", "record"),
    [
        ("abbreviated", github_record("0123456")),
        ("overlong", github_record(COMMIT_SHA + "ab")),
        ("uppercase", github_record(COMMIT_SHA.upper())),
        ("non-hexadecimal", github_record("z" * 40)),
    ],
)
def test_github_malformed_commit_sha_rejects_without_identity(
    workspace: Path, tmp_path: Path, name: str, record: dict
) -> None:
    """§19.3; §24.49: rejected before §19.4 duplicate classification."""
    payload = write_payload(tmp_path, f"{name}.json", record)
    outcome = run_import(workspace, "github", payload)

    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].source_record_id is None
    assert raw_rows(workspace) == []
    assert evidence_rows(workspace) == []


@pytest.mark.parametrize(
    ("name", "repo"),
    [
        ("no-separator", "playbook"),
        ("empty-owner", "/playbook"),
        ("empty-name", "vera-example/"),
        ("extra-segment", "vera-example/playbook/extra"),
        ("empty", ""),
    ],
)
def test_github_repo_must_be_an_owner_name_identity(
    workspace: Path, tmp_path: Path, name: str, repo: str
) -> None:
    """§19.3: `repo` is half the derived, non-normalized idempotency key."""
    payload = write_payload(tmp_path, f"{name}.json", github_record(repo=repo))
    outcome = run_import(workspace, "github", payload)

    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].reason == "record_invalid"
    # An identity that is itself invalid is exactly §19.4 rule 5's null case.
    assert outcome.rejected[0].source_record_id is None
    assert raw_rows(workspace) == []


def test_github_supplied_identity_field_is_an_undeclared_field(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.3: no adapter value may supply or override the derived identity."""
    payload = write_payload(
        tmp_path, "supplied.json", github_record(record_id="vera-example-supplied")
    )
    outcome = run_import(workspace, "github", payload)

    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].source_record_id == f"vera-example/playbook@{COMMIT_SHA}"
    assert raw_rows(workspace) == []


@pytest.mark.parametrize(
    ("name", "key", "corrupt"),
    [
        ("non-hexadecimal-hash", "content_hash", "Vera Example not a digest"),
        ("uppercase-hash", "content_hash", "A" * 64),
        ("empty-identity", "source_record_id", ""),
    ],
)
def test_retained_import_metadata_outside_its_typed_shape_fails_closed(
    workspace: Path, tmp_path: Path, name: str, key: str, corrupt: str
) -> None:
    """§11 rules 30 and 39: this scan hydrates the import identity keys.

    The empty identity is the case with teeth: it matches no computed
    identity, so without this the record would import a second time as
    `accepted` against corrupt retained state.
    """
    payload = write_payload(tmp_path, f"{name}.jsonl", [ephemeris_record()])
    run_import(workspace, "ephemeris", payload)
    rows = raw_rows(workspace)
    assert len(rows) == 1

    metadata = json.loads(rows[0]["metadata_json"])
    metadata[key] = corrupt
    with database(workspace) as connection:
        # §5.3's automation-immutability trigger is exactly what stops a
        # writer from producing this state; the test has to defeat it to
        # stand in for a workspace corrupted outside Exp2Res.
        connection.execute("DROP TRIGGER raw_logs_automation_update_guard")
        connection.execute(
            "UPDATE raw_logs SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False), rows[0]["id"]),
        )
        connection.commit()

    with pytest.raises(IntegrityFailureError):
        run_import(workspace, "ephemeris", payload)
    assert len(raw_rows(workspace)) == 1


def test_two_retained_rows_claiming_one_identity_fail_closed(
    workspace: Path, tmp_path: Path
) -> None:
    """§11 rule 39: an ambiguous retained verdict is not one an import picks."""
    payload = write_payload(tmp_path, "twins.jsonl", [ephemeris_record()])
    run_import(workspace, "ephemeris", payload)
    rows = raw_rows(workspace)
    assert len(rows) == 1

    metadata = json.loads(rows[0]["metadata_json"])
    twin = dict(metadata, content_hash="b" * 64)
    assert twin["content_hash"] != metadata["content_hash"]
    with database(workspace) as connection:
        # A second row under one identity is state the importer cannot write:
        # it stands in for a workspace corrupted outside Exp2Res.
        connection.execute(
            """
            INSERT INTO raw_logs (
                id, recorded_at, entry_type, source_type, occurred_start,
                occurred_end, temporal_precision, temporal_confidence,
                raw_text, project, project_key, external_ref,
                corrects_log_id, metadata_json
            )
            SELECT 'raw_log_vera_example_twin', recorded_at, entry_type,
                   source_type, occurred_start, occurred_end,
                   temporal_precision, temporal_confidence, raw_text, project,
                   project_key, external_ref, corrects_log_id, ?
            FROM raw_logs WHERE id = ?
            """,
            (json.dumps(twin, ensure_ascii=False), rows[0]["id"]),
        )
        connection.commit()

    with pytest.raises(IntegrityFailureError):
        run_import(workspace, "ephemeris", payload)
    assert len(raw_rows(workspace)) == 2


def test_atlas_bound_whose_uncertainty_interval_overflows_is_rejected(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 4: a record's own defect never aborts the whole import.

    A bound this close to `datetime.max` overflows when §16.7's precision
    width is added, so §11 rule 54 refuses it as an ordinary validation
    failure — the raw `OverflowError` is not a `ValueError` and would escape
    Pydantic instead of becoming this record's rejection.
    """
    record = atlas_record(
        as_of="9999-12-31T23:59:59+00:00",
        occurred={
            "start": "9999-12-31T00:00:00+00:00",
            "end": None,
            "precision": "exact_day",
            "confidence": "high",
        },
        trail_segments=[],
    )
    payload = write_payload(tmp_path, "overflow.json", record)
    outcome = run_import(workspace, "atlas", payload)

    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].reason == "record_invalid"
    assert outcome.rejected[0].source_record_id == record["record_id"]
    assert raw_rows(workspace) == []


def test_atlas_as_of_without_a_utc_instant_is_rejected(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 5: an established record boundary always gets an outcome.

    This edge needs no precision width — the comparison instant alone has no
    UTC form — so §11 rule 54 refuses it on the field itself, before any
    hashing or containment check reaches it.
    """
    record = atlas_record(as_of="0001-01-01T00:00:00+14:00")
    payload = write_payload(tmp_path, "as-of.json", record)
    outcome = run_import(workspace, "atlas", payload)

    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].reason == "record_invalid"
    assert outcome.rejected[0].source_record_id == record["record_id"]
    assert raw_rows(workspace) == []


def test_a_datetime_with_no_utc_form_rejects_only_its_own_record(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 4: the records behind a defect still get their outcome.

    §11 rule 54 refuses an offset-aware datetime with no UTC instant, which
    §19.4 rule 3's canonical hash would otherwise need. The failure lands
    after earlier records have committed, so it must not abort the run.
    """
    edge = {
        "start": "0001-01-01T00:00:00+14:00",
        "end": None,
        "precision": "exact_datetime",
        "confidence": "high",
    }
    payload = write_payload(
        tmp_path,
        "utc-edge.jsonl",
        [
            ephemeris_record("vera-ephemeris-0031"),
            ephemeris_record("vera-ephemeris-0032", occurred=edge),
            ephemeris_record("vera-ephemeris-0033"),
        ],
    )
    outcome = run_import(workspace, "ephemeris", payload)

    assert counts(outcome) == (2, 0, 1)
    assert outcome.rejected[0].record_number == 2
    assert outcome.rejected[0].reason == "record_invalid"
    assert outcome.rejected[0].source_record_id == "vera-ephemeris-0032"
    assert [record.source_record_id for record in outcome.accepted] == [
        "vera-ephemeris-0031",
        "vera-ephemeris-0033",
    ]
    assert len(raw_rows(workspace)) == 2


def test_github_identity_past_the_structural_bound_is_rejected(
    workspace: Path, tmp_path: Path
) -> None:
    """§11 rule 30 bounds the derived identity, not only its `repo` half."""
    oversized = "vera-example-" + "o" * 15_987 + "/" + "n" * 343
    payload = write_payload(
        tmp_path, "oversized.json", github_record(repo=oversized)
    )
    outcome = run_import(workspace, "github", payload)

    assert counts(outcome) == (0, 0, 1)
    assert outcome.rejected[0].reason == "record_invalid"
    # The oversized string is not a value the result envelope could carry.
    assert outcome.rejected[0].source_record_id is None
    assert raw_rows(workspace) == []


def test_single_record_payload_without_json_has_no_record_boundary(
    workspace: Path, tmp_path: Path
) -> None:
    """§19.4 rule 5: too early for a record means `result = null`."""
    path = tmp_path / "atlas.json"
    path.write_text("Vera Example not JSON\n", encoding="utf-8")
    with pytest.raises(ImportPayloadInvalidError) as failure:
        run_import(workspace, "atlas", str(path))

    assert failure.value.diagnostic_class == "import_payload_invalid"
    assert raw_rows(workspace) == []


def test_denied_payload_path_never_reaches_record_classification(
    workspace: Path, tmp_path: Path
) -> None:
    """§29.4 rules 4 and 12: the payload itself passes the acquisition gate."""
    denied = tmp_path / ".env"
    denied.write_text("Vera Example synthetic secret stand-in\n", encoding="utf-8")
    with pytest.raises(ForbiddenPathError):
        run_import(workspace, "ephemeris", str(denied))
    assert raw_rows(workspace) == []
