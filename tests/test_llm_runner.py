"""Offline §15 runner, validation-retry, and telemetry tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest
from pydantic import Field

from exp2res.domain.models import StrictModel
from exp2res.errors import LLMCancelledError, LLMInvocationError
from exp2res.llm.adapter import invoke_contract
from exp2res.llm.contracts import (
    ContractDefinition,
    ContractWarning,
    strict_output_schema,
)
from exp2res.llm.runner import (
    AttemptTelemetry,
    CallBudgets,
    CodexCLIRunner,
    PreparedCall,
    ProcessOutcome,
    RawResult,
    _read_output,
    run_subprocess,
)
from exp2res.storage.telemetry import reconcile_abandoned_telemetry
from exp2res.storage.workspace import writer_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner


class SampleContractOutput(StrictModel):
    service_id: str
    value: str
    warnings: list[ContractWarning] = Field(default_factory=list)


CONTRACT = ContractDefinition(
    contract_id="test-only-vera-example",
    output_model=SampleContractOutput,
    fixed_instructions="Return the test-only value and warnings fields.",
    schema_revision="test-v1",
    service_owned_fields=frozenset({"service_id"}),
)
INPUT = b'{"subject":"Vera Example synthetic input"}'
VALID = b'{"value":"Vera Example validated","warnings":[]}'


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
        "per_run_call_ceiling": 10,
        "planned_call_count": 1,
        "per_invocation_cost_ceiling": Decimal("1"),
        "per_run_cost_ceiling": Decimal("2"),
        "input_cost_per_million": Decimal("1"),
        "output_cost_per_million": Decimal("2"),
    }
    values.update(overrides)
    return CallBudgets(**values)  # type: ignore[arg-type]


def enrich(payload: dict[str, object]) -> dict[str, object]:
    return {**payload, "service_id": "svc_vera_example"}


def invoke(
    workspace: Path,
    fake: FakeContractRunner,
    *,
    run_id: str,
    call_budgets: CallBudgets | None = None,
):
    return invoke_contract(
        workspace=workspace,
        runner=fake,
        contract=CONTRACT,
        serialized_input=INPUT,
        model_id="gpt-test-vera-example",
        budgets=call_budgets or budgets(),
        run_id=run_id,
        stage="13.test",
        cli_version="0.144.4-test",
        enrich=enrich,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def telemetry(workspace: Path, run_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    connection = sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite")
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            "SELECT * FROM processing_runs WHERE id = ?", (run_id,)
        ).fetchone()
        call = connection.execute(
            "SELECT * FROM llm_calls WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        connection.close()
    assert run is not None and call is not None
    return run, call


def test_fake_runner_valid_round_trip_persists_content_free_exact_hashes(
    workspace: Path,
) -> None:
    """Issue #69: native-schema bytes validate before telemetry completes."""

    fake = FakeContractRunner([VALID])
    result = invoke(workspace, fake, run_id="run_vera_valid")
    run, call = telemetry(workspace, "run_vera_valid")

    assert result.output == SampleContractOutput(
        service_id="svc_vera_example",
        value="Vera Example validated",
        warnings=[],
    )
    assert run["status"] == call["status"] == "completed"
    assert run["provider"] == "codex-cli"
    assert run["model"] == "gpt-test-vera-example"
    assert run["prompt_policy_hash"]
    assert call["input_hash"] == hashlib.sha256(INPUT).hexdigest()
    assert call["output_hash"] == hashlib.sha256(VALID).hexdigest()
    assert call["transport_retries"] == call["schema_retries"] == 0
    metadata = json.loads(run["metadata_json"])
    assert metadata["call_1_exit_code"] == "0"
    assert metadata["call_1_duration_ms"] == "10"
    retained = run["metadata_json"] + " ".join(str(value) for value in call)
    assert "Vera Example synthetic input" not in retained
    assert "Vera Example validated" not in retained

    schema = strict_output_schema(CONTRACT)
    assert "service_id" not in schema["properties"]

    def every_object_is_closed(value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" or "properties" in value:
                assert value.get("additionalProperties") is False
            for child in value.values():
                every_object_is_closed(child)
        elif isinstance(value, list):
            for child in value:
                every_object_is_closed(child)

    every_object_is_closed(schema)


def test_datetime_outputs_validate_through_the_json_boundary(
    workspace: Path,
) -> None:
    """§11: ISO offset datetimes in model JSON validate under strict types."""

    from datetime import timedelta

    class TimedContractOutput(StrictModel):
        service_id: str
        value: str
        occurred_start: datetime
        warnings: list[ContractWarning] = Field(default_factory=list)

    timed_contract = ContractDefinition(
        contract_id="test-only-vera-timed",
        output_model=TimedContractOutput,
        fixed_instructions="Return the timed test-only fields.",
        schema_revision="test-v1",
        service_owned_fields=frozenset({"service_id"}),
    )
    timed_valid = (
        b'{"value":"Vera Example timed","occurred_start":'
        b'"2026-07-01T09:30:00+02:00","warnings":[]}'
    )
    result = invoke_contract(
        workspace=workspace,
        runner=FakeContractRunner([timed_valid]),
        contract=timed_contract,
        serialized_input=INPUT,
        model_id="gpt-test-vera-example",
        budgets=budgets(),
        run_id="run_vera_timed",
        stage="13.test",
        cli_version="0.144.4-test",
        enrich=enrich,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )
    assert result.output.occurred_start == datetime(
        2026, 7, 1, 9, 30, tzinfo=timezone(timedelta(hours=2))
    )
    _run, call = telemetry(workspace, "run_vera_timed")
    assert call["status"] == "completed"
    assert call["schema_retries"] == 0


def test_persist_and_terminal_telemetry_are_one_atomic_unit(
    workspace: Path,
) -> None:
    """§15.10 rule 7: a later failed business row rolls back earlier rows."""

    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE test_business_rows (id TEXT PRIMARY KEY)"
        )

    def persist_two_rows_then_fail(_validated, connection):
        connection.execute(
            "INSERT INTO test_business_rows(id) VALUES ('row_vera_1')"
        )
        raise RuntimeError("Vera Example injected persistence failure")

    with pytest.raises(LLMInvocationError) as caught:
        invoke_contract(
            workspace=workspace,
            runner=FakeContractRunner([VALID]),
            contract=CONTRACT,
            serialized_input=INPUT,
            model_id="gpt-test-vera-example",
            budgets=budgets(),
            run_id="run_vera_atomic",
            stage="13.test",
            cli_version="0.144.4-test",
            enrich=enrich,
            persist_validated=persist_two_rows_then_fail,
            clock=lambda: FIXED_NOW,
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )
    assert caught.value.failure_code == "business_commit_failed"
    run, call = telemetry(workspace, "run_vera_atomic")
    assert run["status"] == "failed"
    assert run["failure_code"] == "business_commit_failed"
    assert call["status"] == "completed"
    assert call["output_hash"] == hashlib.sha256(VALID).hexdigest()
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT COUNT(*) FROM test_business_rows").fetchone()
    assert rows[0] == 0

    def persist_one_row(_validated, connection):
        connection.execute(
            "INSERT INTO test_business_rows(id) VALUES ('row_vera_ok')"
        )
        return ("row_vera_ok",)

    invoke_contract(
        workspace=workspace,
        runner=FakeContractRunner([VALID]),
        contract=CONTRACT,
        serialized_input=INPUT,
        model_id="gpt-test-vera-example",
        budgets=budgets(),
        run_id="run_vera_atomic_ok",
        stage="13.test",
        cli_version="0.144.4-test",
        enrich=enrich,
        persist_validated=persist_one_row,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )
    ok_run, _ok_call = telemetry(workspace, "run_vera_atomic_ok")
    assert ok_run["status"] == "completed"
    assert json.loads(ok_run["output_ids_json"]) == ["row_vera_ok"]
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id FROM test_business_rows"
        ).fetchall()
    assert rows == [("row_vera_ok",)]


def test_real_runner_workspace_has_only_declared_retry_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #69: W/W' contain only typed bytes, schema, and safe diagnostics."""

    codex = tmp_path / "codex"
    bwrap = tmp_path / "bwrap"
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    for binary in (codex, bwrap):
        binary.write_text("Vera Example inert executable\n", encoding="utf-8")
        binary.chmod(0o700)
    (codex_home / "auth.json").write_text(
        '{"fixture":"Vera Example inert auth"}', encoding="utf-8"
    )
    seen: list[tuple[set[str], bytes, list[str], Path]] = []

    def inspect_workspace(command: list[str], *, timeout_seconds: float) -> ProcessOutcome:
        _ = timeout_seconds
        bind_index = command.index("--bind")
        workspace_path = Path(command[bind_index + 1])
        names = {item.name for item in workspace_path.iterdir()}
        diagnostic_path = workspace_path / "validation_errors.json"
        diagnostics = diagnostic_path.read_bytes() if diagnostic_path.exists() else b""
        seen.append((names, diagnostics, command, workspace_path))
        (workspace_path / "output.json").write_bytes(VALID)
        return ProcessOutcome(0, 0.01, b"", False, False)

    monkeypatch.setattr("exp2res.llm.runner.run_subprocess", inspect_workspace)
    runner = CodexCLIRunner(
        codex_binary=codex,
        bwrap_binary=bwrap,
        codex_home=codex_home,
    )
    diagnostics = b'{"errors":[{"location":["extra"],"type":"extra_forbidden"}]}'
    prepared = PreparedCall(
        contract_id=CONTRACT.contract_id,
        serialized_input=INPUT,
        json_schema=json.dumps(strict_output_schema(CONTRACT)).encode("utf-8"),
        model_id="gpt-test-vera-example",
        fixed_instruction="Vera Example fixed test-only instruction",
        budgets=budgets(),
        validation_errors=diagnostics,
        validation_round=1,
    )
    initial = replace(prepared, validation_errors=None, validation_round=0)
    assert runner.run_contract(initial).final_message_bytes == VALID
    result = runner.run_contract(prepared)
    assert result.final_message_bytes == VALID
    assert seen[0][0] == {"input.json", "schema.json"}
    assert seen[0][1] == b""
    assert seen[1][0] == {"input.json", "schema.json", "validation_errors.json"}
    assert seen[1][1] == diagnostics
    assert b"Vera Example validated" not in diagnostics
    command = seen[1][2]
    for flag in (
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "-C",
        "-s",
        "--output-schema",
        "--output-last-message",
        "--model",
    ):
        assert flag in command
    assert command[-1] == prepared.fixed_instruction
    assert all(not workspace_path.exists() for *_rest, workspace_path in seen)


def test_runner_never_follows_an_output_symlink_to_a_host_file(tmp_path: Path) -> None:
    """§29.4: parent-side result collection cannot reopen ambient host data."""

    outside = tmp_path / "Vera Example outside response sentinel"
    outside.write_bytes(b"Vera Example host content must not be read")
    output = tmp_path / "output.json"
    output.symlink_to(outside)
    assert _read_output(output) is None


def test_budget_preflight_stops_before_fake_transport_and_records_code(
    workspace: Path,
) -> None:
    """§15.10: local refusal creates telemetry but invokes no transport."""

    fake = FakeContractRunner([VALID])
    with pytest.raises(LLMInvocationError) as caught:
        invoke(
            workspace,
            fake,
            run_id="run_vera_local_refusal",
            call_budgets=budgets(max_input_bytes=len(INPUT) - 1),
        )
    run, call = telemetry(workspace, "run_vera_local_refusal")
    assert caught.value.failure_code == "budget_exceeded"
    assert fake.calls == []
    assert run["failure_code"] == call["failure_code"] == "budget_exceeded"


@pytest.mark.parametrize(
    "invalid",
    [
        b'{"value":"Vera Example prose","warnings":[],"extra":"denied"}',
        b'{"value":"Vera Example malformed",',
        b'{"service_id":"injected","value":"Vera Example","warnings":[]}',
    ],
    ids=["extra-key", "malformed-json", "service-owned-injection"],
)
def test_invalid_output_retries_once_then_fails_without_business_effect(
    workspace: Path, invalid: bytes
) -> None:
    """§15.1/§15.11: closed invalid responses get exactly one retry."""

    fake = FakeContractRunner([invalid, invalid])
    run_id = "run_" + hashlib.sha256(invalid).hexdigest()[:12]
    with pytest.raises(LLMInvocationError) as caught:
        invoke(workspace, fake, run_id=run_id)
    run, call = telemetry(workspace, run_id)

    assert caught.value.failure_code == "response_validation_failed"
    assert len(fake.calls) == 2
    assert call["schema_retries"] == 1
    assert call["transport_retries"] == 0
    assert run["status"] == call["status"] == "failed"
    assert run["failure_code"] == call["failure_code"] == "response_validation_failed"
    assert call["output_hash"] is None
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 0


def test_validation_retry_carries_diagnostics_only_and_can_recover(
    workspace: Path,
) -> None:
    """§15.1: W' gets diagnostic shape, never the prior response prose."""

    prior = b'{"value":"Vera Example PRIOR RESPONSE PROSE","extra":"denied"}'
    fake = FakeContractRunner([prior, VALID])
    invoke(workspace, fake, run_id="run_vera_retry")
    _run, call = telemetry(workspace, "run_vera_retry")

    assert len(fake.calls) == 2
    first, second = fake.calls
    assert first.validation_errors is None
    assert second.validation_round == 1
    diagnostics = json.loads(second.validation_errors)
    assert list(diagnostics) == ["errors"]
    assert diagnostics["errors"]
    assert set(diagnostics["errors"][0]) == {"location", "type"}
    assert b"PRIOR RESPONSE PROSE" not in second.validation_errors
    assert second.serialized_input == first.serialized_input == INPUT
    assert second.json_schema == first.json_schema
    assert call["schema_retries"] == 1
    assert call["status"] == "completed"


def test_diagnostics_name_declared_fields_and_anonymize_invented_keys(
    workspace: Path,
) -> None:
    """§15.1: retry diagnostics locate declared contract fields by name."""

    missing_value = b'{"warnings":[]}'
    fake = FakeContractRunner([missing_value, VALID])
    invoke(workspace, fake, run_id="run_vera_named_diag")
    diagnostics = json.loads(fake.calls[1].validation_errors)
    locations = [error["location"] for error in diagnostics["errors"]]
    assert ["value"] in locations
    assert all("$field" not in location for location in locations)

    invented = b'{"value":"Vera Example","warnings":[],"Vera Example key":1}'
    fake_invented = FakeContractRunner([invented, VALID])
    invoke(workspace, fake_invented, run_id="run_vera_anon_diag")
    raw = fake_invented.calls[1].validation_errors
    assert b"Vera Example key" not in raw
    diagnostics = json.loads(raw)
    assert ["$field"] in [error["location"] for error in diagnostics["errors"]]


def test_enrich_reference_invalidity_stays_in_the_validation_retry_class(
    workspace: Path,
) -> None:
    """§15.1: reference validation inside enrichment gets the one retry."""

    from exp2res.llm.contracts import ContractValidationError

    def referee(payload: dict[str, object]) -> dict[str, object]:
        if payload["value"] != "Vera Example validated":
            raise ContractValidationError(
                b'{"errors":[{"location":["value"],"type":"unresolved_reference"}]}'
            )
        return {**payload, "service_id": "svc_vera_example"}

    wrong_reference = b'{"value":"Vera Example wrong reference","warnings":[]}'
    fake = FakeContractRunner([wrong_reference, VALID])
    result = invoke_contract(
        workspace=workspace,
        runner=fake,
        contract=CONTRACT,
        serialized_input=INPUT,
        model_id="gpt-test-vera-example",
        budgets=budgets(),
        run_id="run_vera_enrich_reference",
        stage="13.test",
        cli_version="0.144.4-test",
        enrich=referee,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )
    _run, call = telemetry(workspace, "run_vera_enrich_reference")
    assert len(fake.calls) == 2
    assert b"unresolved_reference" in fake.calls[1].validation_errors
    assert call["schema_retries"] == 1
    assert call["status"] == "completed"
    assert result.output.value == "Vera Example validated"


def test_timeout_retries_same_call_and_cancellation_is_terminal(
    workspace: Path,
) -> None:
    """§15.10: timeout retries are bounded; cancellation never adopts output."""

    timed_out = RawResult(
        None,
        None,
        0.02,
        (AttemptTelemetry(1, None, 0.02, timed_out=True),),
        timed_out=True,
    )
    fake_timeout = FakeContractRunner([timed_out, timed_out])
    with pytest.raises(LLMInvocationError) as timeout_error:
        invoke(workspace, fake_timeout, run_id="run_vera_timeout")
    _run, timeout_call = telemetry(workspace, "run_vera_timeout")
    assert timeout_error.value.failure_code == "transport_timeout"
    assert timeout_call["transport_retries"] == 1
    assert timeout_call["failure_code"] == "transport_timeout"

    cancelled = RawResult(
        None,
        None,
        0.01,
        (AttemptTelemetry(1, None, 0.01, cancelled=True),),
        cancelled=True,
    )
    fake_cancelled = FakeContractRunner([cancelled])
    with pytest.raises(LLMCancelledError):
        invoke(workspace, fake_cancelled, run_id="run_vera_cancelled")
    cancelled_run, cancelled_call = telemetry(workspace, "run_vera_cancelled")
    assert len(fake_cancelled.calls) == 1
    assert cancelled_run["failure_code"] == cancelled_call["failure_code"] == "cancelled"
    assert cancelled_call["output_hash"] is None


def test_interrupt_during_backoff_records_cancelled_terminals(
    workspace: Path,
) -> None:
    """§15.10 rule 8: Ctrl-C in the retry backoff still finishes rows cancelled."""

    timed_out = RawResult(
        None,
        None,
        0.02,
        (AttemptTelemetry(1, None, 0.02, timed_out=True),),
        timed_out=True,
    )

    def interrupting_sleep(_delay: float) -> None:
        raise KeyboardInterrupt()

    with pytest.raises(LLMCancelledError):
        invoke_contract(
            workspace=workspace,
            runner=FakeContractRunner([timed_out, VALID]),
            contract=CONTRACT,
            serialized_input=INPUT,
            model_id="gpt-test-vera-example",
            budgets=budgets(backoff_lower_seconds=0.01, backoff_upper_seconds=0.01),
            run_id="run_vera_backoff_interrupt",
            stage="13.test",
            cli_version="0.144.4-test",
            enrich=enrich,
            clock=lambda: FIXED_NOW,
            sleeper=interrupting_sleep,
            jitter=lambda lower, _upper: lower,
        )
    run, call = telemetry(workspace, "run_vera_backoff_interrupt")
    assert run["status"] == call["status"] == "failed"
    assert run["failure_code"] == call["failure_code"] == "cancelled"
    assert run["finished_at"] and call["finished_at"]
    assert call["transport_retries"] == 1


@pytest.mark.parametrize(
    ("error_channel", "exit_code", "expected"),
    [
        (b"HTTP 408 request timeout", 1, "transport_timeout"),
        (b"HTTP 429 rate limit", 1, "transport_rate_limited"),
        (b"HTTP 401 authentication failed", 1, "transport_auth_failed"),
        (b"ambiguous delivery lost response", 1, "transport_lost_response"),
        (b"provider rejected request", 1, "transport_provider_error"),
        (b"", 0, "transport_provider_error"),
    ],
    ids=["http-408", "rate-limit", "auth", "lost-response", "provider", "missing-output"],
)
def test_adapter_maps_terminal_error_channel_without_persisting_it(
    workspace: Path, error_channel: bytes, exit_code: int, expected: str
) -> None:
    """§15.10 rule 10: CLI failure classes map deterministically and safely."""

    raw = RawResult(
        None,
        exit_code,
        0.01,
        (AttemptTelemetry(1, exit_code, 0.01),),
        error_channel=error_channel,
    )
    fake = FakeContractRunner([raw])
    run_id = f"run_vera_{expected}_{exit_code}_{len(error_channel)}"
    with pytest.raises(LLMInvocationError) as caught:
        invoke(
            workspace,
            fake,
            run_id=run_id,
            call_budgets=budgets(transport_attempt_cap=1),
        )
    run, call = telemetry(workspace, run_id)
    assert caught.value.failure_code == expected
    assert run["failure_code"] == call["failure_code"] == expected
    if error_channel:
        assert error_channel.decode("ascii", errors="ignore") not in run["metadata_json"]


def test_process_timeout_kills_the_child_process_group(tmp_path: Path) -> None:
    """Issue #69: a deadline kills the foreground group, including descendants."""

    pid_path = tmp_path / "Vera Example child.pid"
    command = [
        "/usr/bin/sh",
        "-c",
        f"sleep 30 & child=$!; echo $child > '{pid_path}'; wait",
    ]
    outcome = run_subprocess(command, timeout_seconds=0.1)
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{child_pid}").exists()


def test_group_kill_reaches_a_descendant_that_outlives_the_leader(
    tmp_path: Path,
) -> None:
    """Issue #69: a leader exiting on SIGTERM never shields a slow descendant."""

    pid_path = tmp_path / "Vera Example survivor.pid"
    command = [
        "/usr/bin/sh",
        "-c",
        # The descendant ignores SIGTERM; the leader exits on it immediately,
        # so only the unconditional group SIGKILL can end the survivor.
        "/usr/bin/sh -c 'trap \"\" TERM; sleep 30' & child=$!; "
        f"echo $child > '{pid_path}'; "
        'trap "exit 0" TERM; wait',
    ]
    outcome = run_subprocess(command, timeout_seconds=0.1)
    assert outcome.timed_out is True
    survivor_pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while Path(f"/proc/{survivor_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{survivor_pid}").exists()


def _plant_abandoned_run(workspace: Path, run_id: str) -> None:
    database = workspace / ".exp2res" / "exp2res.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO processing_runs(id, stage, started_at, status)
            VALUES (?, '13.test', ?, 'running')
            """,
            (run_id, FIXED_NOW.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO llm_calls(
                run_id, call_index, started_at, status,
                transport_retries, schema_retries
            ) VALUES (?, 1, ?, 'running', 0, 0)
            """,
            (run_id, FIXED_NOW.isoformat()),
        )


def test_next_writer_reconciles_only_nonterminal_telemetry(workspace: Path) -> None:
    """§15.10 rule 8: crash leftovers fail as cancelled; terminals stay exact."""

    fake = FakeContractRunner([VALID])
    invoke(workspace, fake, run_id="run_vera_terminal")
    _plant_abandoned_run(workspace, "run_vera_abandoned")
    # The production wiring: every business writer_database entry sweeps
    # abandoned rows before its business operation.
    with writer_database(workspace):
        pass
    terminal_run, terminal_call = telemetry(workspace, "run_vera_terminal")
    abandoned_run, abandoned_call = telemetry(workspace, "run_vera_abandoned")
    assert terminal_run["status"] == terminal_call["status"] == "completed"
    assert terminal_run["failure_code"] is terminal_call["failure_code"] is None
    assert abandoned_run["status"] == abandoned_call["status"] == "failed"
    assert abandoned_run["failure_code"] == abandoned_call["failure_code"] == "cancelled"
    assert abandoned_run["finished_at"] and abandoned_call["finished_at"]


def test_invocation_holds_writer_authority_and_sweeps_only_dead_rows(
    workspace: Path,
) -> None:
    """§8.1/§15.10 rule 8: entry sweeps dead rows; a run never sweeps itself."""

    _plant_abandoned_run(workspace, "run_vera_dead")
    with writer_database(workspace, reconcile=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        assert reconcile_abandoned_telemetry(
            connection, finished_at=datetime(2026, 7, 16, tzinfo=timezone.utc)
        ) == (1, 1)
        connection.rollback()
    run, call = telemetry(workspace, "run_vera_dead")
    assert run["status"] == call["status"] == "running"

    # The invocation holds the writer seam door-to-door, so the nonterminal
    # row it observes at entry belongs to a dead writer and is swept before
    # its own run row exists; its own run then completes normally.
    fake = FakeContractRunner([VALID])
    invoke(workspace, fake, run_id="run_vera_second")
    dead_run, dead_call = telemetry(workspace, "run_vera_dead")
    assert dead_run["status"] == dead_call["status"] == "failed"
    assert dead_run["failure_code"] == dead_call["failure_code"] == "cancelled"
    own_run, own_call = telemetry(workspace, "run_vera_second")
    assert own_run["status"] == own_call["status"] == "completed"


def test_other_writers_block_while_an_invocation_holds_authority(
    workspace: Path,
) -> None:
    """§8.1: a live run's writer authority excludes concurrent business writers."""

    from exp2res.errors import WorkspaceBusyError

    with writer_database(workspace):
        with pytest.raises(WorkspaceBusyError):
            with writer_database(workspace, timeout_ms=100):
                pass


def test_multi_call_stage_shares_one_held_writer_connection(
    workspace: Path,
) -> None:
    """The Phase 1 stage shape: one held connection spans planned invocations."""

    from exp2res.storage.telemetry import finish_processing_run

    with writer_database(workspace) as held:
        for index in (1, 2):
            invoke_contract(
                workspace=workspace,
                runner=FakeContractRunner([VALID]),
                contract=CONTRACT,
                serialized_input=INPUT,
                model_id="gpt-test-vera-example",
                budgets=budgets(planned_call_count=2),
                run_id="run_vera_held",
                stage="13.test",
                call_index=index,
                finish_run=False,
                cli_version="0.144.4-test",
                enrich=enrich,
                clock=lambda: FIXED_NOW,
                sleeper=lambda _seconds: None,
                jitter=lambda lower, _upper: lower,
                connection=held,
            )
        held.execute("BEGIN IMMEDIATE")
        finish_processing_run(
            held,
            run_id="run_vera_held",
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=("fact_vera_held",),
        )
        held.commit()
    run, _call = telemetry(workspace, "run_vera_held")
    assert run["status"] == "completed"
    metadata = json.loads(run["metadata_json"])
    assert "call_1_exit_code" in metadata and "call_2_exit_code" in metadata


def multi_invoke(
    workspace: Path,
    fake: FakeContractRunner,
    *,
    run_id: str,
    call_index: int,
    finish_run: bool,
    model_id: str = "gpt-test-vera-example",
):
    return invoke_contract(
        workspace=workspace,
        runner=fake,
        contract=CONTRACT,
        serialized_input=INPUT,
        model_id=model_id,
        budgets=budgets(planned_call_count=2),
        run_id=run_id,
        stage="13.test",
        call_index=call_index,
        finish_run=finish_run,
        cli_version="0.144.4-test",
        enrich=enrich,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def test_multi_call_run_appends_contiguous_calls_and_caller_finishes(
    workspace: Path,
) -> None:
    """§12.15/§15.10 rule 7: one run owns call_index 1..N; the stage finishes it."""

    multi_invoke(
        workspace,
        FakeContractRunner([VALID]),
        run_id="run_vera_multi",
        call_index=1,
        finish_run=False,
    )
    run, call = telemetry(workspace, "run_vera_multi")
    assert run["status"] == "running"
    assert call["status"] == "completed"

    multi_invoke(
        workspace,
        FakeContractRunner([VALID]),
        run_id="run_vera_multi",
        call_index=2,
        finish_run=False,
    )
    connection = sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite")
    connection.row_factory = sqlite3.Row
    try:
        runs = connection.execute(
            "SELECT * FROM processing_runs WHERE id = 'run_vera_multi'"
        ).fetchall()
        calls = connection.execute(
            "SELECT * FROM llm_calls WHERE run_id = 'run_vera_multi' ORDER BY call_index"
        ).fetchall()
    finally:
        connection.close()
    assert len(runs) == 1
    assert [row["call_index"] for row in calls] == [1, 2]
    assert all(row["status"] == "completed" for row in calls)
    assert runs[0]["status"] == "running"
    metadata = json.loads(runs[0]["metadata_json"])
    assert "call_1_exit_code" in metadata and "call_2_exit_code" in metadata

    from exp2res.storage.telemetry import finish_processing_run

    with writer_database(workspace, reconcile=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        finish_processing_run(
            connection,
            run_id="run_vera_multi",
            finished_at=FIXED_NOW,
            status="completed",
            output_ids=("fact_vera",),
        )
        connection.commit()
    run, _call = telemetry(workspace, "run_vera_multi")
    assert run["status"] == "completed"
    assert json.loads(run["output_ids_json"]) == ["fact_vera"]
    merged = json.loads(run["metadata_json"])
    assert "call_1_exit_code" in merged and "call_2_exit_code" in merged


def test_later_call_index_requires_the_same_running_configuration(
    workspace: Path,
) -> None:
    """§12.15: mid-run configuration change or a finished run fails closed."""

    from exp2res.errors import IntegrityFailureError

    invoke(workspace, FakeContractRunner([VALID]), run_id="run_vera_single")
    with pytest.raises(IntegrityFailureError):
        multi_invoke(
            workspace,
            FakeContractRunner([VALID]),
            run_id="run_vera_single",
            call_index=2,
            finish_run=False,
        )

    multi_invoke(
        workspace,
        FakeContractRunner([VALID]),
        run_id="run_vera_config",
        call_index=1,
        finish_run=False,
    )
    with pytest.raises(IntegrityFailureError):
        multi_invoke(
            workspace,
            FakeContractRunner([VALID]),
            run_id="run_vera_config",
            call_index=2,
            finish_run=False,
            model_id="gpt-test-vera-other-model",
        )
    _run, call = telemetry(workspace, "run_vera_config")
    assert call["call_index"] == 1  # no second call row was recorded


def test_call_failure_in_a_multi_call_run_fails_the_whole_run(
    workspace: Path,
) -> None:
    """§15.10 rule 7: a failed planned invocation fails its run terminally."""

    invalid = b'{"value":"Vera Example","warnings":[],"extra":"denied"}'
    multi_invoke(
        workspace,
        FakeContractRunner([VALID]),
        run_id="run_vera_partial",
        call_index=1,
        finish_run=False,
    )
    with pytest.raises(LLMInvocationError):
        multi_invoke(
            workspace,
            FakeContractRunner([invalid, invalid]),
            run_id="run_vera_partial",
            call_index=2,
            finish_run=False,
        )
    run, _call = telemetry(workspace, "run_vera_partial")
    assert run["status"] == "failed"
    assert run["failure_code"] == "response_validation_failed"
    assert json.loads(run["output_ids_json"]) == []
