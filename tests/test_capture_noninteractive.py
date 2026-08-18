"""§21.52 non-prompt owner capture and retro uncertainty tests."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading

import pytest
import typer
from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.pipeline.stage1 as stage1_module
import exp2res.services.capture as capture_service
import exp2res.services.detection as detection_service
from exp2res.cli import app
from exp2res.errors import WorkspaceBusyError
from exp2res.services.logs import list_logs, show_log

from fakes import FakeContractRunner
from test_stage3_extraction import SELECTION, budgets
from test_stage4_detection import DetectionIds, detector_response, prepare_fact


runner = CliRunner()
pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]


def invoke_json(
    workspace: Path, arguments: list[str], *, input: str | bytes | None = None
):
    result = runner.invoke(
        app,
        ["--json", "--workspace", str(workspace), *arguments],
        input=input,
    )
    return result, json.loads(result.stdout)


def seed_gap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    ids = DetectionIds()
    fact_id, log_id, _item_id = prepare_fact(workspace, ids)
    payload = detector_response(
        target_id=fact_id,
        left=("experience_fact", fact_id),
        right=("raw_log", log_id),
    )
    fake = FakeContractRunner([payload])
    monkeypatch.setattr(
        detection_service,
        "build_llm_execution",
        lambda _workspace: (SELECTION, budgets(), fake),
    )
    result, envelope = invoke_json(
        workspace, ["--yes", "detections", "generate"]
    )
    assert result.exit_code == 0
    return envelope["result"]["gaps"][0]["id"]


def created_log_id(envelope: dict[str, object]) -> str:
    affected = envelope["affected_ids"]
    assert isinstance(affected, dict)
    created = affected["created"]
    assert isinstance(created, list)
    return next(
        group["ids"][0]
        for group in created
        if group["entity_type"] == "raw_log"
    )


def test_multiline_file_and_stdin_capture_round_trip_with_unchanged_classes(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.52 / §24.56: all non-prompt sources preserve bytes and classes."""

    gap_id = seed_gap(workspace, monkeypatch)
    daily_bytes = b"Vera Example daily first line.\n\nDaily second line.\n"
    daily_file = tmp_path / "Vera Example daily multiline.md"
    daily_file.write_bytes(daily_bytes)
    daily_result, daily_envelope = invoke_json(
        workspace,
        [
            "log",
            "today",
            "--file",
            str(daily_file),
            "--artifact",
            "urn:vera-example:daily",
        ],
    )
    assert daily_result.exit_code == 0

    daily_stdin_bytes = b"Vera Example daily stdin first line.\n\nDaily stdin second line.\n"
    daily_stdin_result, daily_stdin_envelope = invoke_json(
        workspace,
        ["log", "today", "--file", "-"],
        input=daily_stdin_bytes,
    )
    assert daily_stdin_result.exit_code == 0

    retro_file_bytes = b"Vera Example retro file first line.\n\nRetro file second line.\n"
    retro_file = tmp_path / "Vera Example retro multiline.md"
    retro_file.write_bytes(retro_file_bytes)
    retro_file_result, retro_file_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(retro_file),
            "--precision",
            "month",
            "--period",
            "2026-07",
            "--confidence",
            "medium",
        ],
    )
    assert retro_file_result.exit_code == 0

    retro_bytes = b"Vera Example retro first line.\n\nRetro second line.\n"
    retro_result, retro_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            "-",
            "--precision",
            "unknown",
            "--confidence",
            "low",
            "--project",
            "Vera Example Migration",
            "--artifact",
            "urn:vera-example:retro",
        ],
        input=retro_bytes,
    )
    assert retro_result.exit_code == 0

    answer_bytes = b"Vera Example answer first line.\n\nAnswer second line.\n"
    answer_result, answer_envelope = invoke_json(
        workspace,
        [
            "gaps",
            "answer",
            "--gap-id",
            gap_id,
            "--file",
            "-",
            "--artifact",
            "urn:vera-example:answer",
        ],
        input=answer_bytes,
    )
    assert answer_result.exit_code == 0

    daily = show_log(workspace, log_id=created_log_id(daily_envelope))
    daily_stdin = show_log(
        workspace, log_id=created_log_id(daily_stdin_envelope)
    )
    retro_file_log = show_log(
        workspace, log_id=created_log_id(retro_file_envelope)
    )
    retro = show_log(workspace, log_id=created_log_id(retro_envelope))
    answer = show_log(workspace, log_id=created_log_id(answer_envelope))

    assert daily.raw_log.raw_text.encode("utf-8") == daily_bytes
    assert daily.raw_log.external_ref == str(daily_file)
    assert (daily.raw_log.entry_type, daily.raw_log.source_type) == (
        "manual_daily",
        "manual_entry",
    )
    assert daily_stdin.raw_log.raw_text.encode("utf-8") == daily_stdin_bytes
    assert daily_stdin.raw_log.external_ref is None
    assert (daily_stdin.raw_log.entry_type, daily_stdin.raw_log.source_type) == (
        "manual_daily",
        "manual_entry",
    )
    assert retro_file_log.raw_log.raw_text.encode("utf-8") == retro_file_bytes
    assert retro_file_log.raw_log.external_ref == str(retro_file)
    assert (
        retro_file_log.raw_log.entry_type,
        retro_file_log.raw_log.source_type,
    ) == ("manual_retro", "user_memory")
    assert retro.raw_log.raw_text.encode("utf-8") == retro_bytes
    assert retro.raw_log.external_ref is None
    assert (retro.raw_log.entry_type, retro.raw_log.source_type) == (
        "manual_retro",
        "user_memory",
    )
    assert retro.raw_log.occurred.precision == "unknown"
    assert retro.raw_log.occurred.start is None
    assert retro.raw_log.occurred.end is None
    assert answer.raw_log.raw_text.encode("utf-8") == answer_bytes
    assert answer.raw_log.external_ref is None
    assert (answer.raw_log.entry_type, answer.raw_log.source_type) == (
        "gap_answer",
        "manual_entry",
    )
    for bundle in (daily, retro, answer):
        assert [item.strength for item in bundle.evidence_items] == [
            "manual_claim",
            "artifact_reference",
        ]


def test_interactive_unknown_precision_skips_period_and_stores_null_bounds(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§21.52 / §24.56: precision is asked first and unknown needs no fiction."""

    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    answers = iter(
        [
            "unknown",
            "low",
            "",
            "Vera Example remembers the event but not when it happened.",
        ]
    )
    prompts: list[str] = []

    def prompt(label: str, **_kwargs: object) -> str:
        prompts.append(label)
        return next(answers)

    monkeypatch.setattr(typer, "prompt", prompt)
    result, envelope = invoke_json(workspace, ["log", "retro"])
    assert result.exit_code == 0
    assert prompts == [
        "How precise is this?",
        "How confident are you?",
        "Project/activity?",
        "Describe what you remember.",
    ]
    stored = show_log(workspace, log_id=created_log_id(envelope)).raw_log
    assert stored.occurred.precision == "unknown"
    assert stored.occurred.start is None
    assert stored.occurred.end is None


def test_noninteractive_retro_rejects_unknown_period_and_missing_typed_values(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§21.52 / §24.56: bad typed forms fail instead of prompting or discarding."""

    source = tmp_path / "Vera Example retro.md"
    source.write_text("Vera Example reconstructed record.\n", encoding="utf-8")
    unknown_period, unknown_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "unknown",
            "--period",
            "2026-07",
            "--confidence",
            "low",
        ],
    )
    assert unknown_period.exit_code == 2
    assert unknown_envelope["diagnostic_class"] == "period_not_allowed"

    missing_period, missing_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "date_range",
            "--confidence",
            "medium",
        ],
    )
    assert missing_period.exit_code == 2
    assert missing_envelope["diagnostic_class"] == "input_required"
    assert "What period" not in missing_period.stdout + missing_period.stderr

    malformed_range, range_envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "approximate_range",
            "--period",
            "2026-07",
            "--confidence",
            "medium",
        ],
    )
    assert malformed_range.exit_code == 2
    assert range_envelope["diagnostic_class"] == "invalid_time_shape"
    assert list_logs(workspace) == ()


def test_open_retro_period_round_trips_null_end_without_marker_or_decay(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """§21.53/§24.57: explicit start/.. survives capture and later reads."""

    source = tmp_path / "Vera Example ongoing work.md"
    source.write_text(
        "Vera Example has worked on the synthetic project since April.\n",
        encoding="utf-8",
    )
    result, envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "approximate_range",
            "--period",
            "2026-04-01/..",
            "--confidence",
            "medium",
        ],
    )
    assert result.exit_code == 0, result.stderr
    log_id = created_log_id(envelope)
    first = show_log(workspace, log_id=log_id).raw_log
    second = show_log(workspace, log_id=log_id).raw_log
    assert first.occurred == second.occurred
    assert first.occurred.start is not None
    assert first.occurred.start.isoformat() == "2026-04-01T00:00:00+00:00"
    assert first.occurred.end is None
    assert first.occurred.precision == "approximate_range"
    with sqlite3.connect(workspace / ".exp2res" / "exp2res.sqlite") as connection:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(raw_logs)")
        ]
        stored = connection.execute(
            """
            SELECT occurred_start, occurred_end, temporal_precision
            FROM raw_logs WHERE id = ?
            """,
            (log_id,),
        ).fetchone()
    assert stored == (
        "2026-04-01T00:00:00+00:00",
        None,
        "approximate_range",
    )
    assert not any("open" in column or "ongoing" in column for column in columns)


@pytest.mark.parametrize(
    ("precision", "period"),
    [
        ("date_range", "2026-04-01/"),
        ("date_range", ".."),
        ("date_range", "../2026-05-01"),
        ("exact_day", "2026-04-01/.."),
        ("approximate_range", "2026-04-01"),
    ],
    ids=[
        "empty-end",
        "bare-open-marker",
        "missing-start",
        "non-range-open",
        "separator-free-range",
    ],
)
def test_invalid_open_period_forms_fail_class_2_without_persistence(
    workspace: Path,
    tmp_path: Path,
    precision: str,
    period: str,
) -> None:
    """§21.53/§24.57: openness is typed only by range start/..."""

    source = tmp_path / "Vera Example invalid open period.md"
    source.write_text("Vera Example invalid open period.\n", encoding="utf-8")
    result, envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            precision,
            "--period",
            period,
            "--confidence",
            "medium",
        ],
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] in {"invalid_time", "invalid_time_shape"}
    assert list_logs(workspace) == ()


@pytest.mark.parametrize(
    ("payload", "diagnostic"),
    [
        (b"Vera Example\n" + b"x" * 1_048_564, "input_too_large"),
        (b"Vera Example invalid UTF-8: \xff\n", "input_not_utf8"),
    ],
    ids=["oversize", "invalid-utf8"],
)
def test_stdin_capture_is_bounded_utf8_and_atomic(
    workspace: Path, payload: bytes, diagnostic: str
) -> None:
    """§11 / §14.2 / §21.52: stdin shares the source-file byte gate."""

    result, envelope = invoke_json(
        workspace,
        ["log", "today", "--file", "-"],
        input=payload,
    )
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] == diagnostic
    assert list_logs(workspace) == ()


@pytest.mark.parametrize(
    ("arguments", "unwanted_prompt"),
    [
        (
            ["--precision", "", "--period", "2026-07-15", "--confidence", "low"],
            "How precise is this?",
        ),
        (
            ["--precision", "exact_day", "--period", "", "--confidence", "low"],
            "What period are we reconstructing?",
        ),
        (
            ["--precision", "exact_day", "--period", "2026-07-15", "--confidence", ""],
            "How confident are you?",
        ),
    ],
    ids=["precision", "period", "confidence"],
)
def test_interactive_retro_rejects_explicitly_empty_typed_options(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    unwanted_prompt: str,
) -> None:
    """§21.52 / §24.56: an empty option is owner input, not an absent one."""

    monkeypatch.setattr(cli_module, "_noninteractive", lambda _controls: False)
    prompts: list[str] = []

    def prompt(label: str, **_kwargs: object) -> str:
        prompts.append(label)
        return "Vera Example reconstructed record."

    monkeypatch.setattr(typer, "prompt", prompt)
    result, envelope = invoke_json(workspace, ["log", "retro", *arguments])
    assert result.exit_code == 2
    assert envelope["diagnostic_class"] in {"invalid_time", "invalid_time_shape"}
    # The supplied value is never replaced by a prompted one.
    assert unwanted_prompt not in prompts
    assert list_logs(workspace) == ()


def _committed_pair(envelope: dict) -> dict[str, list[str]]:
    return {
        group["entity_type"]: group["ids"]
        for group in envelope["affected_ids"]["created"]
    }


def test_an_interrupt_between_the_commit_and_the_return_names_the_pair(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: the rows are durable before the writer lock is released.

    Releasing the lock and closing the connection is the longest part of the
    post-commit window, and an interrupt landing there used to leave as an
    empty cancellation naming nothing the owner could reconcile against.
    """

    real = stage1_module.writer_database

    @contextmanager
    def interrupt_on_release(*args, **kwargs):
        with real(*args, **kwargs) as connection:
            yield connection
        raise KeyboardInterrupt()

    source = tmp_path / "Vera Example daily.md"
    source.write_bytes(b"Vera Example committed before the interrupt.\n")
    monkeypatch.setattr(stage1_module, "writer_database", interrupt_on_release)
    result, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    monkeypatch.undo()

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = _committed_pair(envelope)
    assert len(created["raw_log"]) == 1
    assert len(created["evidence_item"]) == 1
    stored = list_logs(workspace)
    assert [log.id for log in stored] == created["raw_log"]


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask"), reason="no signal mask on this platform"
)
def test_an_interrupt_while_the_envelope_is_assembled_names_the_pair(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrowest window: committed, returned, not yet reported.

    A real SIGINT, not a raised object: the hand-off from the returned bundle
    to the outcome is one bytecode, so only refusing delivery closes it. The
    signal is held, the envelope is built, and the command still ends
    cancelled.
    """

    source = tmp_path / "Vera Example retro.md"
    source.write_bytes(b"Vera Example committed before assembly.\n")
    real_outcome = cli_module.capture_outcome

    def interrupt_before_reporting(bundle):
        os.kill(os.getpid(), signal.SIGINT)
        return real_outcome(bundle)

    monkeypatch.setattr(cli_module, "capture_outcome", interrupt_before_reporting)
    result, envelope = invoke_json(
        workspace,
        [
            "log",
            "retro",
            "--file",
            str(source),
            "--precision",
            "month",
            "--period",
            "2026-07",
            "--confidence",
            "medium",
        ],
    )
    monkeypatch.undo()

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = _committed_pair(envelope)
    assert [log.id for log in list_logs(workspace)] == created["raw_log"]


def test_an_interrupted_gap_answer_names_the_pair_it_committed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gaps answer` builds its own envelope and owes the same report."""

    gap_id = seed_gap(workspace, monkeypatch)
    before = {log.id for log in list_logs(workspace)}
    source = tmp_path / "Vera Example answer.md"
    source.write_bytes(b"Vera Example answered the gap.\n")

    def interrupt_during_the_managed_cleanup(*args, **kwargs):
        # The answer and its gap transition are durable; §14.7's stale-set
        # removal is the post-commit work this form does on its own.
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        capture_service,
        "remove_managed_sets_for_locked_database",
        interrupt_during_the_managed_cleanup,
    )
    result, envelope = invoke_json(
        workspace,
        ["gaps", "answer", "--gap-id", gap_id, "--file", str(source)],
    )
    monkeypatch.undo()

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = _committed_pair(envelope)
    assert {log.id for log in list_logs(workspace)} - before == set(created["raw_log"])


class _CommitThenInterrupt:
    """A connection whose `commit()` succeeds and then does not return."""

    def __init__(self, connection, *, durable: bool) -> None:
        self._connection = connection
        self._durable = durable

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def commit(self) -> None:
        if self._durable:
            self._connection.commit()
        raise KeyboardInterrupt()


@contextmanager
def _interrupting_writer(real, *, durable: bool):
    @contextmanager
    def opened(*args, **kwargs):
        with real(*args, **kwargs) as connection:
            yield _CommitThenInterrupt(connection, durable=durable)

    yield opened


def test_a_commit_that_does_not_return_reports_the_row_it_stored(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6 asks the database, not the assignment after `commit()`.

    SQLite can make a transaction durable and then fail to return, so a flag
    set on the next line answers the wrong question: the capture would roll
    back nothing and report nothing while the pair stands.
    """

    source = tmp_path / "Vera Example durable.md"
    source.write_bytes(b"Vera Example committed without returning.\n")
    before = {log.id for log in list_logs(workspace)}
    with _interrupting_writer(stage1_module.writer_database, durable=True) as opened:
        monkeypatch.setattr(stage1_module, "writer_database", opened)
        result, envelope = invoke_json(
            workspace, ["log", "today", "--file", str(source)]
        )
    monkeypatch.undo()

    assert result.exit_code == 9
    created = _committed_pair(envelope)
    assert {log.id for log in list_logs(workspace)} - before == set(created["raw_log"])


def test_a_gap_answer_whose_commit_never_ran_names_nothing(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The converse for the form that commits its own transaction.

    The transaction is still open, so the handler below rolls it back. Naming
    its intended IDs would send the owner reconciling against rows that never
    existed.
    """

    gap_id = seed_gap(workspace, monkeypatch)
    before = {log.id for log in list_logs(workspace)}
    source = tmp_path / "Vera Example uncommitted answer.md"
    source.write_bytes(b"Vera Example answered nothing.\n")
    with _interrupting_writer(capture_service.writer_database, durable=False) as opened:
        monkeypatch.setattr(capture_service, "writer_database", opened)
        result, envelope = invoke_json(
            workspace,
            ["gaps", "answer", "--gap-id", gap_id, "--file", str(source)],
        )
    monkeypatch.undo()

    assert result.exit_code == 9
    assert envelope["affected_ids"]["created"] == []
    assert {log.id for log in list_logs(workspace)} == before


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask"), reason="no signal mask on this platform"
)
def test_a_cancellation_outranks_a_teardown_that_also_failed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6: class 9 outranks every class observed alongside it.

    The owner interrupted and the writer teardown then failed on its own. The
    envelope reports the cancellation and the committed pair, not the
    teardown's class with the interrupt silently dropped.
    """

    real = stage1_module.writer_database

    @contextmanager
    def interrupt_then_fail(*args, **kwargs):
        with real(*args, **kwargs) as connection:
            yield connection
        os.kill(os.getpid(), signal.SIGINT)
        raise WorkspaceBusyError()

    source = tmp_path / "Vera Example contended.md"
    source.write_bytes(b"Vera Example committed before the contention.\n")
    before = {log.id for log in list_logs(workspace)}
    monkeypatch.setattr(stage1_module, "writer_database", interrupt_then_fail)
    result, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    monkeypatch.undo()

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    created = _committed_pair(envelope)
    assert {log.id for log in list_logs(workspace)} - before == set(created["raw_log"])


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask"), reason="no signal mask on this platform"
)
def test_a_service_called_outside_a_command_leaves_sigint_deliverable(
    workspace: Path,
) -> None:
    """Only a boundary that will release the signal may hold it.

    Blocking SIGINT is process-global. A service called from a test, a library
    or another tool has no envelope to protect and nothing that would unblock
    what it masked, so the deferral there would make Ctrl-C ineffective for
    the rest of the process.
    """

    capture_service.capture_daily(
        workspace, raw_text="Vera Example called the service directly."
    )

    assert signal.SIGINT not in signal.pthread_sigmask(signal.SIG_BLOCK, set())


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask"), reason="no signal mask on this platform"
)
def test_the_envelope_is_emitted_before_the_signal_is_released(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§14.14 rule 6 owes the owner the report, not only its computation.

    Releasing the signal once the status is classified but before the write
    would let a `KeyboardInterrupt` escape through the emission, and the
    command would exit with durable rows and no envelope naming them — the
    defect the deferral exists to prevent.
    """

    source = tmp_path / "Vera Example emitted.md"
    source.write_bytes(b"Vera Example committed before assembly.\n")
    real_outcome = cli_module.capture_outcome
    real_emit = cli_module._emit
    seen: dict[str, object] = {}

    def interrupt_before_reporting(bundle):
        os.kill(os.getpid(), signal.SIGINT)
        return real_outcome(bundle)

    def recording_emit(envelope, controls, human_result):
        seen["masked"] = signal.SIGINT in signal.pthread_sigmask(
            signal.SIG_BLOCK, set()
        )
        seen["exit_code"] = envelope.exit_code
        return real_emit(envelope, controls, human_result)

    monkeypatch.setattr(cli_module, "capture_outcome", interrupt_before_reporting)
    monkeypatch.setattr(cli_module, "_emit", recording_emit)
    invoke_json(workspace, ["log", "today", "--file", str(source)])
    monkeypatch.undo()

    # Already classified when the write begins, and still held until it ends.
    assert seen["exit_code"] == 9
    assert seen["masked"] is True


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask"), reason="no signal mask on this platform"
)
def test_a_caller_that_blocks_sigint_keeps_its_policy_and_its_signal(
    workspace: Path, tmp_path: Path
) -> None:
    """An embedding's signal policy is the embedding's, not the command's.

    A SIGINT it had already blocked and left pending predates the command, so
    the envelope must not claim it as its own cancellation nor consume it.
    """

    source = tmp_path / "Vera Example embedded.md"
    source.write_bytes(b"Vera Example ran under a supervisor.\n")
    outer = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        os.kill(os.getpid(), signal.SIGINT)
        result, envelope = invoke_json(
            workspace, ["log", "today", "--file", str(source)]
        )

        assert result.exit_code == 0
        assert envelope["status"] == "ok"
        assert signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        assert signal.SIGINT in signal.sigpending()
    finally:
        if signal.SIGINT in signal.sigpending():
            signal.sigwait({signal.SIGINT})
        signal.pthread_sigmask(signal.SIG_SETMASK, outer)


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask"), reason="no signal mask on this platform"
)
def test_the_deferral_is_declined_where_another_thread_can_take_the_signal(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guarantee that cannot be kept is not made.

    A signal mask is per-thread. With another unmasked thread alive a
    process-directed SIGINT is delivered there and still reaches this one as a
    `KeyboardInterrupt` inside the window, so arming would only claim a
    protection the process cannot provide.
    """

    source = tmp_path / "Vera Example threaded.md"
    source.write_bytes(b"Vera Example ran beside another thread.\n")
    real_outcome = cli_module.capture_outcome
    seen: dict[str, object] = {}

    def recording_outcome(bundle):
        seen["masked"] = signal.SIGINT in signal.pthread_sigmask(
            signal.SIG_BLOCK, set()
        )
        return real_outcome(bundle)

    release = threading.Event()
    other = threading.Thread(target=release.wait, daemon=True)
    other.start()
    try:
        monkeypatch.setattr(cli_module, "capture_outcome", recording_outcome)
        result, _envelope = invoke_json(
            workspace, ["log", "today", "--file", str(source)]
        )
        monkeypatch.undo()
    finally:
        release.set()
        other.join(timeout=5)

    assert result.exit_code == 0
    assert seen["masked"] is False


def test_a_capture_interrupted_before_its_commit_names_nothing(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report is owed to a commit, never to an attempt.

    Rule 6 rolls the in-flight transaction back; naming its intended IDs would
    invite the owner to reconcile against rows that never existed.
    """

    source = tmp_path / "Vera Example rolled back.md"
    source.write_bytes(b"Vera Example never committed.\n")

    result, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    assert result.exit_code == 0
    before = {log.id for log in list_logs(workspace)}

    monkeypatch.setattr(
        capture_service,
        "persist_manual_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    result, envelope = invoke_json(
        workspace, ["log", "today", "--file", str(source)]
    )
    monkeypatch.undo()

    assert result.exit_code == 9
    assert envelope["status"] == "cancelled"
    assert envelope["affected_ids"]["created"] == []
    assert {log.id for log in list_logs(workspace)} == before
