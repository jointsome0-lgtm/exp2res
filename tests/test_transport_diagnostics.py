"""§15.10 rules 9-10 provider-rejection classes and surface diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import exp2res.llm.claude as claude_adapter
import exp2res.llm.codex as codex_adapter
from exp2res.cli import app
from exp2res.llm.registry import ADAPTER_REGISTRY, LLMSelection
from exp2res.llm.runner import AttemptTelemetry, RawResult
from exp2res.storage.workspace import read_database

from fakes import FakeContractRunner
from test_cli_extract import install_fake_execution, seed_lineage


runner = CliRunner()
pytestmark = pytest.mark.contract

# §15.10 rule 10 names a rejection class only from a typed field an adapter
# parsed out of its own runtime's envelope — for the §15.13 CLI adapters, the
# reported HTTP status. Every channel below is invented provider prose, never
# a captured response, and none of it classifies anything.
REJECTION_STATUS_CASES = [
    (400, "transport_request_rejected"),
    (422, "transport_request_rejected"),
    (404, "transport_model_unavailable"),
]
# The status is an envelope field, so only the envelope-reporting adapter can
# reach a rejection class end to end.
CLAUDE_SELECTION = LLMSelection("claude-agent-sdk", "claude-opus-4-8")


def failed(channel: bytes, *, api_error_status: int | None = None) -> RawResult:
    return RawResult(
        final_message_bytes=None,
        exit_code=1,
        duration_seconds=0.01,
        attempts=(AttemptTelemetry(1, 1, 0.01),),
        error_channel=channel,
        api_error_status=api_error_status,
    )


@pytest.mark.parametrize(
    ("status", "expected"), REJECTION_STATUS_CASES, ids=lambda value: str(value)
)
def test_a_typed_status_names_the_rejection_shape(status: int, expected: str) -> None:
    """Issue #151 / §15.10 rule 10: distinct remedy, distinct stable code."""

    code, retryable = claude_adapter.classify_claude_failure(
        failed(b"Vera Example provider prose", api_error_status=status)
    )
    assert code == expected
    # Both classes name a local fix, so neither is worth another attempt.
    assert retryable is False


def test_an_unmapped_typed_status_keeps_the_catch_all() -> None:
    """§15.10 rule 10: an adapter never guesses a narrower code."""

    assert claude_adapter.classify_claude_failure(
        failed(b"Vera Example provider prose", api_error_status=409)
    ) == ("transport_provider_error", False)


@pytest.mark.parametrize(
    "classify",
    [codex_adapter.classify_codex_failure, claude_adapter.classify_claude_failure],
    ids=["codex-cli", "claude-agent-sdk"],
)
def test_a_channel_without_a_typed_status_names_no_rejection_class(classify) -> None:
    """§15.10 rule 10: the mixed channel classifies nothing, in any shape.

    Codex reports free-form stderr and no status, so this is its whole
    rejection behavior; Claude reaches the same path whenever its runtime
    fails before reporting one.
    """

    for channel in (
        b"Vera Example unstructured provider prose",
        b"400 Bad Request: the request was rejected",
        b'{"error":{"type":"invalid_request_error","code":"model_not_found"}}',
    ):
        code, _retryable = classify(failed(channel))
        assert code == "transport_provider_error"


def test_rejection_classes_stay_in_the_provider_transport_exit_class(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§15.10 rule 10: `transport_request_rejected` is class 6, never class 7."""

    seed_lineage(workspace, "rejected")
    install_fake_execution(
        monkeypatch,
        FakeContractRunner([lambda _call: failed(b"", api_error_status=400)]),
        selection=CLAUDE_SELECTION,
    )
    result = runner.invoke(
        app, ["--json", "--workspace", str(workspace), "--yes", "extract"]
    )
    envelope = json.loads(result.stdout)
    assert result.exit_code == 6
    assert envelope["diagnostic_class"] == "transport_request_rejected"
    with read_database(workspace) as connection:
        rows = connection.execute(
            "SELECT status, failure_code FROM processing_runs"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("failed", "transport_request_rejected")]


def test_human_mode_names_the_failing_surface_without_provider_bytes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #151 / §15.10 rule 9: name the surface, echo nothing of it."""

    seed_lineage(workspace, "surface")
    secret = b"Vera Example provider prose sk-vera-example-should-never-appear"
    install_fake_execution(
        monkeypatch, FakeContractRunner([lambda _call: failed(secret)])
    )
    human = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "--yes",
            "extract",
            "--log-id",
            "log_vera_surface",
        ],
    )
    assert human.exit_code == 6
    # The stage value is exactly the one telemetry stores for the run.
    assert "Failing surface: stage 13.3, contract fact-extractor" in human.output
    assert "transport_provider_error" in human.output
    assert "sk-vera-example" not in human.output
    assert "Vera Example provider prose" not in human.output

    # §14.14: the envelope is unchanged — no new field, no diagnostic line —
    # and the run stays inspectable through the run IDs it already reports.
    seed_lineage(workspace, "surfacejson")
    install_fake_execution(
        monkeypatch, FakeContractRunner([lambda _call: failed(secret)])
    )
    machine = runner.invoke(
        app,
        [
            "--json",
            "--workspace",
            str(workspace),
            "--yes",
            "extract",
            "--log-id",
            "log_vera_surfacejson",
        ],
    )
    envelope = json.loads(machine.stdout)
    assert machine.exit_code == 6
    assert "Failing surface" not in machine.output
    assert "failing_stage" not in machine.stdout
    assert len(envelope["run_ids"]) == 1
    with read_database(workspace) as connection:
        stored = connection.execute(
            "SELECT stage FROM processing_runs WHERE id = ?",
            (envelope["run_ids"][0],),
        ).fetchone()
    assert stored[0] == "13.3"


def test_cancellation_names_no_failing_surface(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§15.10 rule 9: an owner interrupt broke no surface."""

    seed_lineage(workspace, "interrupt")

    def interrupt(_call):
        raise KeyboardInterrupt()

    install_fake_execution(monkeypatch, FakeContractRunner([interrupt]))
    human = runner.invoke(
        app, ["--workspace", str(workspace), "--yes", "extract"]
    )
    assert human.exit_code == 9
    assert "Failing surface" not in human.output


def test_no_registered_adapter_names_a_rejection_class_from_the_channel() -> None:
    """§15.10 rule 10: the rule binds every adapter, not just the default."""

    assert set(ADAPTER_REGISTRY) >= {"codex-cli", "claude-agent-sdk"}
    rejection_classes = {"transport_request_rejected", "transport_model_unavailable"}
    channel = b'{"error":{"type":"invalid_request_error","code":"model_not_found"}}'
    for registration in ADAPTER_REGISTRY.values():
        code, _retryable = registration.classify_failure(failed(channel))
        assert code not in rejection_classes


@pytest.mark.parametrize(
    "classify",
    [codex_adapter.classify_codex_failure, claude_adapter.classify_claude_failure],
    ids=["codex-cli", "claude-agent-sdk"],
)
def test_a_retryable_outage_keeps_its_retry_despite_echoed_rejection_wording(
    classify,
) -> None:
    """§15.10 rule 10: the channel is mixed, so an outage marker wins."""

    channel = (
        b'connection reset by peer; last body {"error":{"type":"invalid_request_error"}}'
    )
    assert classify(failed(channel)) == ("transport_provider_error", True)


@pytest.mark.parametrize(
    "classify",
    [codex_adapter.classify_codex_failure, claude_adapter.classify_claude_failure],
    ids=["codex-cli", "claude-agent-sdk"],
)
@pytest.mark.parametrize(
    "prose",
    [
        b"Vera Example wrote: the invalid request form was rejected by review",
        b"Vera Example wrote: an unknown model of collaboration emerged",
        b"Vera Example wrote: an unsupported parameter of the design",
        # An owner who writes about provider errors puts the exact token, and
        # a whole error body, in their own text. Nothing in the channel says
        # who wrote either, which is why none of it can classify.
        b"Vera Example wrote: spent the morning on an invalid_request_error",
        b'Vera Example wrote: pasted {"error":{"code":"model_not_found"}} into notes',
    ],
    ids=[
        "invalid-request-prose",
        "unknown-model-prose",
        "unsupported-parameter-prose",
        "bare-token-prose",
        "echoed-error-body",
    ],
)
def test_owner_prose_in_the_channel_never_names_a_rejection_class(
    classify, prose: bytes
) -> None:
    """§15.10 rule 10: only a typed envelope field names a class."""

    code, _retryable = classify(failed(prose))
    assert code == "transport_provider_error"


def test_orchestration_level_failures_also_name_the_failing_surface(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§15.10 rule 9: an operational failure after the call still names it."""

    from test_stage3_extraction import add_log, exact_day, fact_response
    from conftest import FIXED_NOW

    _log, items = add_log(
        workspace,
        log_id="log_vera_commit_surface",
        recorded_at=FIXED_NOW,
        raw_text="Vera Example commit-surface lineage.",
        occurred=exact_day(15),
        item_specs=(("evi_vera_commit_surface", "manual_claim"),),
        project="Vera Example Project",
    )
    # Two facts sharing one service-assigned ID make the complete-stage commit
    # fail after every call has already validated — `business_commit_failed`
    # is raised by the orchestrator, not inside `invoke_contract`.
    first = json.loads(fact_response([items[0].id]))["facts"][0]
    second = {**first, "claim": "Vera Example designed a second atomic fact."}
    collision = json.dumps(
        {"facts": [first, second], "warnings": []}, separators=(",", ":")
    ).encode("utf-8")
    monkeypatch.setattr(
        "exp2res.services.extraction.new_id",
        lambda kind: "fact_vera_collision" if kind == "fact" else f"{kind}_vera_1",
    )
    install_fake_execution(monkeypatch, FakeContractRunner([collision]))
    human = runner.invoke(
        app, ["--workspace", str(workspace), "--yes", "extract"]
    )
    assert human.exit_code == 7
    assert (
        "Failing surface: stage 13.3, contract fact-extractor "
        "(business_commit_failed)." in human.output
    )
