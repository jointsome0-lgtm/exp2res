#!/usr/bin/env python3
"""Offline, deterministic Vera Example first-mirror demo for issue #79."""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
from typing import Callable, Iterable, Iterator

from typer.testing import CliRunner

import exp2res.cli as cli_module
import exp2res.services.assessment as assessment_service
import exp2res.services.capture as capture_service
import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
import exp2res.services.signals as signals_service
from exp2res.cli import app
from exp2res.exports.companions import (
    AssessmentEvidenceMapDocument,
    SelfClaimsDocument,
)
from exp2res.exports.graph import load_assessment_graph, load_current_snapshot
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import (
    AttemptTelemetry,
    CallBudgets,
    PreparedCall,
    RawResult,
)
from exp2res.pipeline.stage3 import run_fact_extraction
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.pipeline.stage5 import run_signal_generation
from exp2res.pipeline.stage6 import run_assessment_generation
from exp2res.pipeline.stage7 import run_assessment_verification
from exp2res.services.export import export_assessment as real_export_assessment
from exp2res.storage.workspace import (
    CURRENT_SCHEMA_VERSION,
    initialize_workspace as real_initialize_workspace,
    read_database,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "examples" / "vera" / "corpus"
GOLDEN_TRANSCRIPT = ROOT / "demo" / "transcript.txt"
WORKSPACE_LABEL = "demo/workspace"
FIXED_CLOCK = datetime.fromisoformat("2026-07-15T12:30:00+00:00")
CORPUS_VERSION = "0.3.0"
ENVELOPE_VERSION = 1
EXPORT_MEMBERS = ("report.md", "self_claims.json", "evidence_map.json", "manifest.json")


def default_workspace() -> Path:
    """Use a checkout-specific temp path: public checkouts are never data stores."""

    suffix = hashlib.sha256(str(ROOT.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"exp2res-vera-demo-{suffix}"


class DemoIds:
    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)

    def __call__(self, kind: str) -> str:
        prefixes = {
            "raw_log": "log", "evidence_item": "evi", "fact": "fact",
            "gap": "gap", "contradiction": "contradiction", "signal": "signal",
            "snapshot": "snapshot", "claim": "claim", "finding": "finding",
            "run": "run", "gen": "gen",
        }
        self.counts[kind] += 1
        return f"{prefixes[kind]}_demo_{self.counts[kind]:04d}"


@dataclass
class DemoClock:
    value: datetime = FIXED_CLOCK

    def set(self, value: str) -> None:
        self.value = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        return self.value


class CannedContractRunner:
    """Small production-seam runner; intentionally independent of tests/."""

    def __init__(self, responses: Iterable[bytes]) -> None:
        self._responses = deque(responses)
        self.calls: list[PreparedCall] = []

    def run_contract(self, call: PreparedCall) -> RawResult:
        self.calls.append(call)
        if not self._responses:
            raise AssertionError("Vera Example canned response runner exhausted")
        return RawResult(
            final_message_bytes=self._responses.popleft(),
            exit_code=0,
            duration_seconds=0.01,
            attempts=(AttemptTelemetry(1, 0, 0.01),),
        )

    def assert_consumed(self) -> None:
        if self._responses:
            raise AssertionError("Vera Example canned responses were not fully consumed")


def demo_budgets() -> CallBudgets:
    return CallBudgets(
        transport_attempt_cap=1,
        backoff_lower_seconds=0.0,
        backoff_upper_seconds=0.0,
        invocation_deadline_seconds=10.0,
        max_input_bytes=1_048_576,
        input_token_budget=100_000,
        output_token_budget=8_192,
        planned_output_tokens=2_048,
        model_context_tokens=128_000,
        model_max_output_tokens=8_192,
        per_run_call_ceiling=20,
        per_invocation_cost_ceiling=Decimal("0"),
        per_run_cost_ceiling=Decimal("0"),
        input_cost_per_million=Decimal("0"),
        output_cost_per_million=Decimal("0"),
    )


def _manifest() -> dict[str, object]:
    value = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    if value.get("version") != CORPUS_VERSION or value.get("persona") != "Vera Example":
        raise AssertionError("Vera Example corpus version/persona pin does not match demo")
    return value


def canned(name: str) -> bytes:
    relative = f"llm/{name}"
    manifest = _manifest()
    expected = manifest["files"].get(relative)  # type: ignore[index,union-attr]
    data = (CORPUS / relative).read_bytes()
    if expected is None or hashlib.sha256(data).hexdigest() != expected:
        raise AssertionError(f"Vera Example canned response is not manifest-pinned: {relative}")
    return data


@contextmanager
def replaced(target: object, name: str, value: object) -> Iterator[None]:
    original = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, original)


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


def fixed_stage(real_stage: Callable, ids: DemoIds, clock: DemoClock) -> Callable:
    def deterministic(selected_workspace: Path, **kwargs):
        kwargs["id_factory"] = ids
        kwargs["clock"] = clock
        kwargs["sleeper"] = lambda _seconds: None
        kwargs["jitter"] = lambda lower, _upper: lower
        return real_stage(selected_workspace, **kwargs)

    return deterministic


class Transcript:
    def __init__(self, workspace: Path, *, emit: bool) -> None:
        self.workspace = workspace.resolve()
        self.emit = emit
        self.lines = [
            "Vera Example — deterministic first-mirror demo",
            f"corpus={CORPUS_VERSION} schema={CURRENT_SCHEMA_VERSION} envelope={ENVELOPE_VERSION}",
            "mode=no-cost offline canned responses; network/provider calls=0",
            "workspace=demo/workspace (external temporary workspace; display alias only)",
            "",
        ]

    def sanitize(self, value: str) -> str:
        replacements = (
            (str(self.workspace), WORKSPACE_LABEL),
            (str(ROOT.resolve()), "."),
        )
        for source, target in replacements:
            value = value.replace(source, target)
        return value

    def section(self, title: str) -> None:
        self.lines.extend((f"== {title} ==", ""))

    def command(self, display: list[str], output: str) -> None:
        self.lines.append("$ " + shlex.join(display))
        rendered = self.sanitize(output).strip()
        if rendered:
            self.lines.extend(rendered.splitlines())
        self.lines.append("")

    def note(self, value: str) -> None:
        self.lines.extend((value, ""))

    def bytes(self) -> bytes:
        return ("\n".join(self.lines).rstrip() + "\n").encode("utf-8")

    def finish(self) -> bytes:
        data = self.bytes()
        (self.workspace / "demo-transcript.txt").write_bytes(data)
        if self.emit:
            sys.stdout.buffer.write(data)
        return data


RUNNER = CliRunner()


def _combined_output(result) -> str:
    output = result.output
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    if stderr and stderr not in output:
        output += stderr
    return output


def invoke(
    transcript: Transcript,
    workspace: Path,
    arguments: list[str],
    *,
    expected: set[int] = {0},
    init: bool = False,
):
    if init:
        display = ["exp2res", "init"]
        with working_directory(workspace):
            result = RUNNER.invoke(app, arguments)
    else:
        display = ["exp2res", "--workspace", WORKSPACE_LABEL, *arguments]
        result = RUNNER.invoke(app, ["--workspace", str(workspace), *arguments])
    transcript.command(display, _combined_output(result))
    if result.exit_code not in expected:
        raise AssertionError(
            f"Vera Example demo command failed ({result.exit_code}): "
            f"{shlex.join(display)}\n{_combined_output(result).strip()}"
        )
    return result


def _stage_command(
    transcript: Transcript,
    workspace: Path,
    ids: DemoIds,
    clock: DemoClock,
    *,
    service: object,
    stage_name: str,
    real_stage: Callable,
    response_names: list[str],
    arguments: list[str],
    expected: set[int] = {0},
):
    runner = CannedContractRunner(canned(name) for name in response_names)
    with ExitStack() as stack:
        stack.enter_context(replaced(service, "new_id", ids))
        stack.enter_context(
            replaced(
                service,
                "build_llm_execution",
                lambda _workspace: (
                    LLMSelection("codex-cli", "gpt-5.6-sol"),
                    demo_budgets(),
                    runner,
                ),
            )
        )
        stack.enter_context(
            replaced(service, stage_name, fixed_stage(real_stage, ids, clock))
        )
        result = invoke(transcript, workspace, arguments, expected=expected)
    runner.assert_consumed()
    return result


def _configure_workspace(workspace: Path) -> None:
    path = workspace / ".exp2res" / "config.toml"
    text = path.read_text(encoding="utf-8").replace(
        'timezone = ""', 'timezone = "Europe/Berlin"', 1
    )
    path.write_text(text, encoding="utf-8", newline="")
    path.chmod(0o600)


def _current_snapshot(workspace: Path, scope: str) -> str:
    with read_database(workspace) as connection:
        row = connection.execute(
            "SELECT id FROM assessment_snapshots "
            "WHERE superseded_at IS NULL AND scope = ? ORDER BY id",
            (scope,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"Vera Example demo has no current {scope} snapshot")
    return row[0]


def run_demo(workspace: Path, *, emit: bool = True) -> bytes:
    workspace = workspace.resolve()
    if workspace.exists():
        raise FileExistsError(f"{WORKSPACE_LABEL} already exists; run make demo-reset")
    workspace.mkdir(parents=True, mode=0o700)
    ids, clock = DemoIds(), DemoClock()
    transcript = Transcript(workspace, emit=emit)

    transcript.section("Setup and invented Vera corpus capture")
    with replaced(
        cli_module,
        "initialize_workspace",
        lambda target: real_initialize_workspace(target, clock=clock),
    ):
        invoke(transcript, workspace, ["init"], init=True)
    _configure_workspace(workspace)
    transcript.note("Configured demo/workspace timezone: Europe/Berlin")

    original_capture = capture_service.capture_daily_file

    def deterministic_capture(selected_workspace: Path, **kwargs):
        return original_capture(
            selected_workspace, **kwargs, id_factory=ids, clock=clock
        )

    captures = (
        ("2026-06-02T21:00:00+02:00", "examples/vera/corpus/logs/daily-2026-06-02.md"),
        ("2026-06-25T22:00:00+02:00", "examples/vera/corpus/logs/daily-2026-06-25.md"),
        ("2026-07-02T21:00:00+02:00", "examples/vera/corpus/logs/daily-2026-07-02.md"),
    )
    with replaced(cli_module, "capture_daily_file", deterministic_capture):
        for instant, relative in captures:
            clock.set(instant)
            invoke(
                transcript,
                workspace,
                ["log", "today", "--project", "K8s Playbook", "--file", relative],
            )

    transcript.section("Act 1 — supported first mirror and export")
    clock.set("2026-07-11T10:00:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=extraction_service, stage_name="run_fact_extraction",
        real_stage=run_fact_extraction,
        response_names=[f"demo-extract-call-{index:02d}.json" for index in range(1, 4)],
        arguments=["--yes", "extract"],
    )
    clock.set("2026-07-11T10:05:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=detection_service, stage_name="run_detection_generation",
        real_stage=run_detection_generation, response_names=["demo-detection.json"],
        arguments=["--yes", "detections", "generate"],
    )
    invoke(transcript, workspace, ["gaps", "list"])
    invoke(transcript, workspace, ["contradictions", "show", "--contradiction-id", "contradiction_demo_0001"])
    clock.set("2026-07-11T10:10:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=signals_service, stage_name="run_signal_generation",
        real_stage=run_signal_generation, response_names=["demo-signals.json"],
        arguments=["--yes", "signals", "generate"],
    )
    clock.set("2026-07-11T10:15:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=assessment_service, stage_name="run_assessment_generation",
        real_stage=run_assessment_generation,
        response_names=["demo-assessment-act1.json"],
        arguments=["--yes", "assess", "generate"],
    )
    act1_snapshot = _current_snapshot(workspace, "global")
    clock.set("2026-07-11T10:17:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=assessment_service, stage_name="run_assessment_verification",
        real_stage=run_assessment_verification,
        response_names=["demo-verification-act1-supported.json"] * 2,
        arguments=["--yes", "assess", "verify", "--snapshot", act1_snapshot],
    )
    invoke(transcript, workspace, ["assess", "show", "--snapshot", act1_snapshot])

    def deterministic_export(selected_workspace: Path, *, snapshot_id: str):
        return real_export_assessment(
            selected_workspace, snapshot_id=snapshot_id, clock=clock
        )

    clock.set("2026-07-11T10:20:00+02:00")
    with replaced(cli_module, "export_assessment", deterministic_export):
        invoke(
            transcript, workspace,
            ["export", "assessment", "--snapshot", act1_snapshot],
        )

    transcript.note(
        "Claim claim_demo_0001 -> fact fact_demo_0001 -> evidence evi_demo_0001 -> raw log log_demo_0001"
    )
    invoke(transcript, workspace, ["logs", "show", "--log-id", "log_demo_0001"])
    transcript.note(
        "Contradiction contradiction_demo_0001 -> raw logs log_demo_0002 and log_demo_0003"
    )
    invoke(transcript, workspace, ["logs", "show", "--log-id", "log_demo_0002"])
    invoke(transcript, workspace, ["logs", "show", "--log-id", "log_demo_0003"])

    transcript.section("Act 2 — rejected overclaim and first-class export refusal")
    clock.set("2026-07-11T10:25:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=assessment_service, stage_name="run_assessment_generation",
        real_stage=run_assessment_generation,
        response_names=["demo-assessment-act2-overclaim.json"],
        arguments=[
            "--yes", "assess", "generate", "--scope", "project",
            "--project", "K8s Playbook",
        ],
    )
    act2_snapshot = _current_snapshot(workspace, "project")
    invoke(transcript, workspace, ["assess", "show", "--snapshot", act2_snapshot])
    clock.set("2026-07-11T10:27:00+02:00")
    verify_result = _stage_command(
        transcript, workspace, ids, clock,
        service=assessment_service, stage_name="run_assessment_verification",
        real_stage=run_assessment_verification,
        response_names=[
            "demo-verification-act2-rejected.json",
            "demo-verification-act2-supported.json",
        ],
        arguments=["--yes", "assess", "verify", "--snapshot", act2_snapshot],
        expected={10},
    )
    export_result = invoke(
        transcript, workspace,
        ["export", "assessment", "--snapshot", act2_snapshot],
        expected={10},
    )
    transcript.note(
        "Act 2 result: verifier exit 10; assessment export exit 10; no blocked export published."
    )
    state = {
        "persona": "Vera Example",
        "corpus_version": CORPUS_VERSION,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "envelope_version": ENVELOPE_VERSION,
        "act1_snapshot_id": act1_snapshot,
        "act2_snapshot_id": act2_snapshot,
        "act2_verify_exit": verify_result.exit_code,
        "act2_export_exit": export_result.exit_code,
    }
    (workspace / "demo-state.json").write_text(
        json.dumps(state, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="",
    )
    transcript.note("Demo run complete. Run make demo-verify for closure and determinism proof.")
    return transcript.finish()


def exported_bytes(workspace: Path, snapshot_id: str) -> dict[str, bytes]:
    root = workspace / "out" / "assessment" / snapshot_id
    return {name: (root / name).read_bytes() for name in EXPORT_MEMBERS}


def _verify_one(workspace: Path, *, golden: bytes | None) -> tuple[dict[str, bytes], bytes]:
    state = json.loads((workspace / "demo-state.json").read_text(encoding="utf-8"))
    if state["persona"] != "Vera Example" or state["corpus_version"] != CORPUS_VERSION:
        raise AssertionError("Vera Example demo state version pin mismatch")
    if state["schema_version"] != CURRENT_SCHEMA_VERSION or state["envelope_version"] != 1:
        raise AssertionError("Vera Example demo schema/envelope pin mismatch")
    if (state["act2_verify_exit"], state["act2_export_exit"]) != (10, 10):
        raise AssertionError("Vera Example blocked-overclaim exit contract was not observed")

    act1, act2 = state["act1_snapshot_id"], state["act2_snapshot_id"]
    members = exported_bytes(workspace, act1)
    manifest = json.loads(members["manifest.json"])
    recorded = {item["name"]: item["sha256"] for item in manifest["members"]}
    for name in ("report.md", "self_claims.json", "evidence_map.json"):
        if recorded.get(name) != hashlib.sha256(members[name]).hexdigest():
            raise AssertionError(f"Vera Example export manifest hash mismatch: {name}")
    evidence_map = AssessmentEvidenceMapDocument.model_validate_json(
        members["evidence_map.json"]
    )
    claims_document = SelfClaimsDocument.model_validate_json(
        members["self_claims.json"]
    )

    with read_database(workspace) as connection:
        act1_row, act1_model = load_current_snapshot(connection, act1)
        act2_row, act2_model = load_current_snapshot(connection, act2)
        act1_graph = load_assessment_graph(
            connection, snapshot_row=act1_row, snapshot=act1_model
        )
        load_assessment_graph(connection, snapshot_row=act2_row, snapshot=act2_model)
        if act1_model.verification_status != "supported":
            raise AssertionError("Vera Example Act 1 snapshot is not supported/current")
        if act2_model.verification_status != "rejected":
            raise AssertionError("Vera Example Act 2 snapshot is not rejected/current")
        if (workspace / "out" / "assessment" / act2).exists():
            raise AssertionError("Vera Example blocked Act 2 export was published")

        claim_links = {item.claim_id: item for item in evidence_map.claim_links}
        signal_links = {item.signal_id: item for item in evidence_map.signal_links}
        fact_links = {item.fact_id: item for item in evidence_map.fact_links}
        evidence_links = {
            item.evidence_item_id: item for item in evidence_map.evidence_links
        }
        for claim_id in evidence_map.rendered_claim_ids:
            claim = claim_links[claim_id]
            reached_facts = set(claim.source_fact_ids)
            for signal_id in claim.source_signal_ids:
                signal = signal_links[signal_id]
                reached_facts.update(signal.supporting_fact_ids)
                reached_facts.update(signal.counter_fact_ids)
            if not reached_facts:
                raise AssertionError(f"Vera Example rendered claim has no fact closure: {claim_id}")
            for fact_id in reached_facts:
                fact = fact_links[fact_id]
                if not fact.evidence_item_ids or not fact.source_log_ids:
                    raise AssertionError(f"Vera Example fact closure is incomplete: {fact_id}")
                for evidence_id in fact.evidence_item_ids:
                    link = evidence_links[evidence_id]
                    if link.raw_log_id not in fact.source_log_ids:
                        raise AssertionError("Vera Example evidence/log closure diverged")
                    for table, entity_id in (
                        ("experience_facts", fact_id),
                        ("evidence_items", evidence_id),
                        ("raw_logs", link.raw_log_id),
                    ):
                        if connection.execute(
                            f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,)
                        ).fetchone() is None:
                            raise AssertionError(f"Vera Example closure row missing: {entity_id}")

    if evidence_map.rendered_claim_ids != [item.value.id for item in act1_graph.claims]:
        raise AssertionError("Vera Example rendered claim set is not graph-complete")
    report = members["report.md"].decode("utf-8")
    if "Vera Example" not in report or not claims_document.unknowns:
        raise AssertionError("Vera Example report does not visibly render claim and gap")
    if "ingress completion conflict" not in report:
        raise AssertionError("Vera Example report does not visibly render contradiction")

    transcript = (workspace / "demo-transcript.txt").read_bytes()
    if b"/home/" in transcript or str(workspace).encode("utf-8") in transcript:
        raise AssertionError("Vera Example transcript exposes an absolute private path")
    if golden is not None and transcript != golden:
        raise AssertionError("Vera Example checked transcript is stale; regenerate it")
    return members, transcript


def verify_demo(workspace: Path, *, check_golden: bool = True, determinism: bool = True) -> None:
    golden = GOLDEN_TRANSCRIPT.read_bytes() if check_golden else None
    current_members, current_transcript = _verify_one(workspace, golden=golden)
    if determinism:
        with tempfile.TemporaryDirectory(prefix="exp2res-vera-demo-verify-") as root:
            first, second = Path(root) / "first", Path(root) / "second"
            first_transcript = run_demo(first, emit=False)
            second_transcript = run_demo(second, emit=False)
            first_members, _ = _verify_one(first, golden=golden)
            second_members, _ = _verify_one(second, golden=golden)
            if first_transcript != second_transcript or first_members != second_members:
                raise AssertionError("Vera Example repeated reset/run is not byte-deterministic")
            if current_transcript != first_transcript or current_members != first_members:
                raise AssertionError("Vera Example current demo differs from clean deterministic run")
    print(
        "OK: Vera Example evidence closure, current generations, blocked export, "
        "manifest hashes, transcript, and repeated-run byte determinism verified"
    )


def reset_demo(workspace: Path) -> None:
    workspace = workspace.resolve()
    default = default_workspace().resolve()
    if workspace != default:
        raise AssertionError("refusing non-default Vera Example demo reset target")
    safe_parent = Path(tempfile.gettempdir()).resolve()
    if workspace.parent != safe_parent or not workspace.name.startswith("exp2res-vera-demo-"):
        raise AssertionError("refusing unsafe Vera Example demo reset target")
    if workspace.exists():
        shutil.rmtree(workspace)
        print(f"Removed {WORKSPACE_LABEL} (external temporary workspace).")
    else:
        print(f"{WORKSPACE_LABEL} is already reset.")


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 4} or argv[1] not in {"reset", "run", "verify"}:
        print("usage: python scripts/demo.py {reset|run|verify} [--workspace PATH]", file=sys.stderr)
        return 2
    workspace = default_workspace()
    if len(argv) == 4:
        if argv[2] != "--workspace":
            return 2
        workspace = Path(argv[3])
    try:
        if argv[1] == "reset":
            reset_demo(workspace)
        elif argv[1] == "run":
            run_demo(workspace)
        else:
            verify_demo(workspace)
    except (AssertionError, FileExistsError, FileNotFoundError, KeyError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
