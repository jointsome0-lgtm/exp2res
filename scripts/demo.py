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
import exp2res.services.capture as capture_service
import exp2res.services.stages as stages_service
import exp2res.services.job_descriptions as jd_service
from exp2res.cli import app
from exp2res.exports.companions import (
    AssessmentEvidenceMapDocument,
    BulletPackEvidenceMapDocument,
    SelfClaimsDocument,
    VerificationReportDocument,
)
from exp2res.exports.branch import load_branch_graph, load_current_branch
from exp2res.exports.graph import load_assessment_graph, load_current_snapshot
from exp2res.exports.managed import ResumeManifest, build_branch_manifest
from exp2res.domain.verification import aggregate_verification_status
from exp2res.storage.repository import (
    STAGE10_ANCHOR_ALLOWLIST,
    hydrate_assessment_snapshot,
    list_self_claims_for_snapshot,
)
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import (
    AttemptTelemetry,
    CallBudgets,
    PreparedCall,
    RawResult,
)
from exp2res.pipeline.stage3 import run_fact_extraction
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.pipeline.stage6 import run_assessment_generation
from exp2res.pipeline.stage7 import run_assessment_verification
from exp2res.pipeline.stage8 import run_job_description_parse
from exp2res.pipeline.stage10 import run_bullet_generation
from exp2res.pipeline.stage11 import run_bullet_verification
from exp2res.services.export import (
    export_assessment as real_export_assessment,
    export_bullet_pack as real_export_bullet_pack,
)
from exp2res.storage.workspace import (
    CURRENT_SCHEMA_VERSION,
    initialize_workspace as real_initialize_workspace,
    read_database,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "examples" / "vera" / "corpus"
GOLDEN_TRANSCRIPT = ROOT / "demo" / "transcript.txt"
GOLDEN_CAST = ROOT / "demo.cast"
WORKSPACE_LABEL = "demo/workspace"
FIXED_CLOCK = datetime.fromisoformat("2026-07-15T12:30:00+00:00")
CORPUS_VERSION = "2.1.0"
ENVELOPE_VERSION = 2
EXPORT_MEMBERS = (
    "report.md",
    "report.html",
    "self_claims.json",
    "evidence_map.json",
    "manifest.json",
)
BRANCH_MEMBERS = (
    "bullet_pack.md",
    "evidence_map.json",
    "verification_report.json",
    "manifest.json",
)
DEMO_BRANCH = "docs-examplia"
DEMO_VACANCY = "examples/vera/corpus/jds/jd-docs-engineer-examplia.md"
PRIVATE_HOME_MARKERS = (b"/home/", b"/Users/", b"/root/", b"\\Users\\")
# Canned §15 prose the mirror must actually render, in §16.14's second-person
# and subject-free forms. Pinned here so a corpus rewording fails this check
# rather than silently reducing what the demo proves.
RENDERED_CLAIM = "You currently show an evidence-checking documentation pattern."
RENDERED_GAP = "What external outcome followed your runbook validation?"
RENDERED_CONTRADICTION = "Ingress completion conflict"


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
            "gap": "gap", "contradiction": "contradiction",
            "snapshot": "snapshot", "claim": "claim", "finding": "finding",
            "job_description": "jd", "jd_requirement": "jdreq",
            "branch": "branch", "bullet": "bullet",
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
            "paths=stored locators are canonical real paths; this checkout root displays as . (display alias only)",
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
                stages_service,
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


def _current_snapshot(workspace: Path) -> str:
    with read_database(workspace) as connection:
        row = connection.execute(
            "SELECT id FROM assessment_snapshots WHERE superseded_at IS NULL"
        ).fetchone()
    if row is None:
        raise AssertionError("Vera Example demo has no current assessment snapshot")
    return row[0]


def _current_branch(workspace: Path) -> str:
    with read_database(workspace) as connection:
        row = connection.execute(
            "SELECT id FROM resume_branches WHERE superseded_at IS NULL"
        ).fetchone()
    if row is None:
        raise AssertionError("Vera Example demo has no current bullet-pack branch")
    return row[0]


def _unmatched_demands(workspace: Path, branch: str) -> list[str]:
    """§18: demands the pack's current bullets leave unanswered, from the rows."""

    with read_database(workspace) as connection:
        parsed = connection.execute(
            "SELECT jd.parsed_json FROM job_descriptions AS jd "
            "JOIN resume_branches AS branch ON branch.job_description_id = jd.id "
            "WHERE branch.id = ?",
            (branch,),
        ).fetchone()
        matched = {
            requirement_id
            for (raw,) in connection.execute(
                "SELECT matched_jd_requirements_json FROM resume_bullets "
                "WHERE branch_id = ? AND superseded_at IS NULL",
                (branch,),
            )
            for requirement_id in json.loads(raw)
        }
    if parsed is None:
        raise AssertionError("Vera Example demo branch has no parsed vacancy")
    return [
        item["text"]
        for item in json.loads(parsed[0])["requirements"]
        if item["id"] not in matched
    ]


def run_demo(workspace: Path, *, emit: bool = True) -> bytes:
    workspace = workspace.resolve()
    if workspace.is_relative_to(ROOT.resolve()):
        raise ValueError("Vera Example demo workspace must stay outside the public checkout")
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
                [
                    "log",
                    "today",
                    "--project",
                    "K8s Playbook",
                    "--file",
                    relative,
                ],
            )

    transcript.section("Facts, gaps, and contradictions from the captured logs")
    clock.set("2026-07-11T10:00:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_fact_extraction",
        real_stage=run_fact_extraction,
        response_names=[f"demo-extract-call-{index:02d}.json" for index in range(1, 4)],
        arguments=["extract"],
    )
    clock.set("2026-07-11T10:05:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_detection_generation",
        real_stage=run_detection_generation, response_names=["demo-detection.json"],
        arguments=["detections", "generate"],
    )
    invoke(transcript, workspace, ["gaps", "list"])
    invoke(transcript, workspace, ["contradictions", "show", "--contradiction-id", "contradiction_demo_0001"])
    # §13.13: one view means one current snapshot, so each regeneration
    # supersedes the previous one and takes its published set with it. The
    # blocked act therefore comes first: the mirror the demo leaves published
    # is the last one generated, and the refused overclaim publishes nothing
    # at any point.
    transcript.section("Act 1 — rejected overclaim and first-class export refusal")
    clock.set("2026-07-11T10:15:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_assessment_generation",
        real_stage=run_assessment_generation,
        response_names=["demo-assessment-overclaim.json"],
        arguments=["assess", "generate"],
    )
    blocked_snapshot = _current_snapshot(workspace)
    invoke(transcript, workspace, ["assess", "show", "--snapshot", blocked_snapshot])
    clock.set("2026-07-11T10:17:00+02:00")
    verify_result = _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_assessment_verification",
        real_stage=run_assessment_verification,
        response_names=[
            "demo-verification-rejected.json",
            "demo-verification-narrowed.json",
        ],
        arguments=["assess", "verify", "--snapshot", blocked_snapshot],
        expected={10},
    )
    export_result = invoke(
        transcript, workspace,
        ["export", "assessment", "--snapshot", blocked_snapshot],
        expected={10},
    )
    transcript.note(
        "Act 1 result: verifier exit 10; assessment export exit 10; no blocked export published."
    )

    transcript.section("Act 2 — supported mirror and its published export")
    clock.set("2026-07-11T10:25:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_assessment_generation",
        real_stage=run_assessment_generation,
        response_names=["demo-assessment-supported.json"],
        arguments=["assess", "generate"],
    )
    published_snapshot = _current_snapshot(workspace)
    clock.set("2026-07-11T10:27:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_assessment_verification",
        real_stage=run_assessment_verification,
        response_names=["demo-verification-supported.json"] * 2,
        arguments=["assess", "verify", "--snapshot", published_snapshot],
    )
    invoke(transcript, workspace, ["assess", "show", "--snapshot", published_snapshot])

    def deterministic_export(selected_workspace: Path, *, snapshot_id: str):
        return real_export_assessment(
            selected_workspace, snapshot_id=snapshot_id, clock=clock
        )

    clock.set("2026-07-11T10:30:00+02:00")
    with replaced(cli_module, "export_assessment", deterministic_export):
        invoke(
            transcript, workspace,
            ["export", "assessment", "--snapshot", published_snapshot],
        )

    transcript.note(
        "Claim claim_demo_0003 -> fact fact_demo_0001 -> evidence evi_demo_0001 -> raw log log_demo_0001"
    )
    invoke(transcript, workspace, ["logs", "show", "--log-id", "log_demo_0001"])
    transcript.note(
        "Contradiction contradiction_demo_0001 -> raw logs log_demo_0002 and log_demo_0003"
    )
    invoke(transcript, workspace, ["logs", "show", "--log-id", "log_demo_0002"])
    invoke(transcript, workspace, ["logs", "show", "--log-id", "log_demo_0003"])

    transcript.section("Act 3 — job-targeted verified bullet pack")
    clock.set("2026-07-12T10:00:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=jd_service, stage_name="run_job_description_parse",
        real_stage=run_job_description_parse,
        response_names=["demo-jd-parse.json"],
        arguments=["jd", "add", DEMO_VACANCY],
    )
    invoke(transcript, workspace, ["jd", "list"])
    clock.set("2026-07-12T10:05:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_bullet_generation",
        real_stage=run_bullet_generation, response_names=["demo-bullets.json"],
        arguments=[
            "bullets", "generate",
            "--jd", "jd_demo_0001",
            "--snapshot", published_snapshot,
            "--branch", DEMO_BRANCH,
        ],
    )
    published_branch = _current_branch(workspace)
    clock.set("2026-07-12T10:07:00+02:00")
    _stage_command(
        transcript, workspace, ids, clock,
        service=stages_service, stage_name="run_bullet_verification",
        real_stage=run_bullet_verification,
        response_names=["demo-bullet-verification.json"],
        arguments=["bullets", "verify", "--branch", DEMO_BRANCH],
    )

    def deterministic_bullet_export(selected_workspace: Path, *, branch_name: str):
        return real_export_bullet_pack(
            selected_workspace, branch_name=branch_name, clock=clock
        )

    clock.set("2026-07-12T10:10:00+02:00")
    with replaced(cli_module, "export_bullet_pack", deterministic_bullet_export):
        invoke(transcript, workspace, ["bullets", "export", "--branch", DEMO_BRANCH])
    unmatched = _unmatched_demands(workspace, published_branch)
    transcript.note(
        "Act 3 result: two supported bullets published under "
        f"out/branch/{published_branch}; {len(unmatched)} demands stay unmatched "
        "because the invented corpus reaches none of them:\n"
        + "\n".join(f"  - {demand}" for demand in unmatched)
    )

    state = {
        "persona": "Vera Example",
        "corpus_version": CORPUS_VERSION,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "envelope_version": ENVELOPE_VERSION,
        "blocked_snapshot_id": blocked_snapshot,
        "published_snapshot_id": published_snapshot,
        "published_branch_id": published_branch,
        "blocked_verify_exit": verify_result.exit_code,
        "blocked_export_exit": export_result.exit_code,
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


def branch_bytes(workspace: Path, branch_id: str) -> dict[str, bytes]:
    root = workspace / "out" / "branch" / branch_id
    return {name: (root / name).read_bytes() for name in BRANCH_MEMBERS}


def _verify_branch(
    workspace: Path, connection, branch: str, members: dict[str, bytes]
) -> None:
    """§13.12: the pack's own closure, checked against persisted rows alone."""

    # §13.14: the manifest is a typed claim about which branch generation the
    # published bytes came from, so it is rebuilt from the persisted graph and
    # compared whole. Member hashes alone would accept correct bytes carried by
    # stale identity, provenance, source IDs, or render hash.
    manifest = ResumeManifest.model_validate_json(members["manifest.json"])
    branch_row, branch_value = load_current_branch(connection, branch)
    graph = load_branch_graph(connection, branch_row=branch_row, branch=branch_value)
    expected = build_branch_manifest(
        graph,
        {name: members[name] for name in BRANCH_MEMBERS[:-1]},
        created_at=manifest.created_at,
    )
    if manifest != expected:
        raise AssertionError("Vera Example branch manifest disagrees with persisted state")

    evidence_map = BulletPackEvidenceMapDocument.model_validate_json(
        members["evidence_map.json"]
    )
    report = VerificationReportDocument.model_validate_json(
        members["verification_report.json"]
    )
    # §13.12: both companions name the exported branch itself, so a
    # structurally valid but wrong ID cannot pass as the pack's identity.
    if evidence_map.entity_id != branch or report.branch_id != branch:
        raise AssertionError("Vera Example companions name another branch")
    rendered = [item.bullet_id for item in evidence_map.rendered_bullets]
    if [item.bullet_id for item in report.findings] != rendered:
        raise AssertionError("Vera Example verification report is not pack-ordered")
    if any(item.verification_status != "supported" for item in report.findings):
        raise AssertionError("Vera Example published a bullet without support")

    anchored = connection.execute(
        "SELECT branch.assessment_snapshot_id, snapshot.verification_status "
        "FROM resume_branches AS branch "
        "JOIN assessment_snapshots AS snapshot "
        "ON snapshot.id = branch.assessment_snapshot_id "
        "WHERE branch.id = ? AND branch.superseded_at IS NULL "
        "AND snapshot.superseded_at IS NULL",
        (branch,),
    ).fetchone()
    if anchored is None:
        raise AssertionError("Vera Example published branch or its anchor is not current")
    anchor_snapshot, anchor_status = anchored
    # §16.11: every gated consumer re-reduces the anchor aggregate from its own
    # current claims rather than trusting the stored one, then applies the
    # Stage 10 allowlist to it.
    anchor_members = list_self_claims_for_snapshot(connection, anchor_snapshot)
    if anchor_status != aggregate_verification_status(
        item.verification_status for item in anchor_members
    ):
        raise AssertionError("Vera Example branch anchor aggregate is stale")
    if anchor_status not in STAGE10_ANCHOR_ALLOWLIST:
        raise AssertionError("Vera Example branch anchor is not export-eligible")

    stored = {
        row[0]: row
        for row in connection.execute(
            "SELECT id, verification_status, text, target_section, "
            "matched_jd_requirements_json, source_fact_ids_json, "
            "source_log_ids_json, source_self_claim_ids_json, "
            "unsupported_phrases_json, verifier_reason FROM resume_bullets "
            "WHERE branch_id = ? AND superseded_at IS NULL",
            (branch,),
        )
    }
    # §13.10: the persisted graph owns render order, so the published sequence
    # is compared with it directly. A set comparison would accept three
    # consistently reversed outputs, which every later field check still passes.
    if [item.value.id for item in graph.bullets] != rendered:
        raise AssertionError("Vera Example rendered bullets are not in persisted order")
    if sorted(stored) != sorted(rendered):
        raise AssertionError("Vera Example rendered bullet set is not branch-complete")
    for finding in report.findings:
        row = stored[finding.bullet_id]
        if (
            finding.verification_status,
            list(finding.unsupported_phrases),
            finding.verifier_reason,
        ) != (row[1], json.loads(row[8]), row[9]):
            raise AssertionError(
                f"Vera Example published verdict is stale: {finding.bullet_id}"
            )
    # §13.12: the pack projects the current rows, so every exported bullet
    # field is the persisted one rather than merely a valid-looking value.
    for item in evidence_map.rendered_bullets:
        row = stored[item.bullet_id]
        if (
            item.text,
            item.target_section,
            list(item.matched_jd_requirements),
            list(item.source_fact_ids),
            list(item.source_log_ids),
            list(item.source_self_claim_ids),
        ) != (row[2], row[3], *(json.loads(value) for value in row[4:8])):
            raise AssertionError(
                f"Vera Example exported bullet differs from its row: {item.bullet_id}"
            )

    pack = members["bullet_pack.md"].decode("utf-8")
    # §13.10/§24.51: the pack projects the evidence map's bullets in that exact
    # order, so a reordering renderer is a failure, not a substring match.
    if [line[2:] for line in pack.splitlines() if line.startswith("- ")] != [
        item.text for item in evidence_map.rendered_bullets
    ]:
        raise AssertionError("Vera Example pack does not render the mapped bullet order")
    fact_links = {item.fact_id: item for item in evidence_map.fact_links}
    evidence_links = {
        item.evidence_item_id: item for item in evidence_map.evidence_links
    }
    claim_links = {item.claim_id: item for item in evidence_map.claim_links}

    def verify_fact_closure(fact_id: str) -> None:
        fact = fact_links[fact_id]
        if not fact.evidence_item_ids or not fact.source_log_ids:
            raise AssertionError(f"Vera Example bullet fact closure is incomplete: {fact_id}")
        if connection.execute(
            "SELECT 1 FROM experience_facts WHERE id = ? AND superseded_at IS NULL",
            (fact_id,),
        ).fetchone() is None:
            raise AssertionError(
                f"Vera Example bullet fact is missing or superseded: {fact_id}"
            )
        # §13.12: the exported closure must be the persisted one, so every
        # exported edge is read back from fact_sources and evidence_items
        # rather than merely resolving to rows that happen to exist.
        persisted_evidence = {
            row[0]
            for row in connection.execute(
                "SELECT evidence_item_id FROM fact_sources WHERE fact_id = ?",
                (fact_id,),
            )
        }
        if set(fact.evidence_item_ids) != persisted_evidence:
            raise AssertionError(
                f"Vera Example exported fact evidence differs from fact_sources: {fact_id}"
            )
        persisted_logs = set()
        for evidence_id in fact.evidence_item_ids:
            link = evidence_links[evidence_id]
            stored_log = connection.execute(
                "SELECT raw_log_id FROM evidence_items WHERE id = ?", (evidence_id,)
            ).fetchone()
            if stored_log is None:
                raise AssertionError(
                    f"Vera Example bullet evidence item is missing: {evidence_id}"
                )
            if link.raw_log_id != stored_log[0]:
                raise AssertionError(
                    f"Vera Example exported evidence/log edge is not the persisted "
                    f"one: {evidence_id}"
                )
            if connection.execute(
                "SELECT 1 FROM raw_logs WHERE id = ?", (link.raw_log_id,)
            ).fetchone() is None:
                raise AssertionError(
                    f"Vera Example bullet raw log is missing: {link.raw_log_id}"
                )
            persisted_logs.add(stored_log[0])
        if set(fact.source_log_ids) != persisted_logs:
            raise AssertionError(
                f"Vera Example exported fact logs differ from the persisted "
                f"evidence: {fact_id}"
            )

    for item in evidence_map.rendered_bullets:
        if not item.source_fact_ids:
            raise AssertionError(f"Vera Example bullet has no fact closure: {item.bullet_id}")
        for fact_id in item.source_fact_ids:
            verify_fact_closure(fact_id)
        # §16.11: Stage 10 may only cite current `supported` claims of the
        # branch's anchor, so a claim-guided bullet proves that membership and
        # its own fact closure, not just the bullet's direct facts.
        for claim_id in item.source_self_claim_ids:
            claim = claim_links.get(claim_id)
            if claim is None:
                raise AssertionError(f"Vera Example bullet claim is unmapped: {claim_id}")
            stored_claim = connection.execute(
                "SELECT source_fact_ids_json, counter_fact_ids_json FROM self_claims "
                "WHERE id = ? AND snapshot_id = ? AND superseded_at IS NULL "
                "AND verification_status = 'supported'",
                (claim_id, anchor_snapshot),
            ).fetchone()
            if stored_claim is None:
                raise AssertionError(
                    f"Vera Example bullet claim is not a current supported "
                    f"anchor member: {claim_id}"
                )
            if not claim.source_fact_ids:
                raise AssertionError(f"Vera Example bullet claim has no fact closure: {claim_id}")
            if set(claim.source_fact_ids) != set(
                json.loads(stored_claim[0])
            ) or set(claim.counter_fact_ids) != set(json.loads(stored_claim[1])):
                raise AssertionError(
                    f"Vera Example exported claim provenance is not the persisted "
                    f"one: {claim_id}"
                )
            for fact_id in claim.source_fact_ids:
                verify_fact_closure(fact_id)


def _verify_one(workspace: Path, *, golden: bytes | None) -> tuple[dict[str, bytes], bytes]:
    state = json.loads((workspace / "demo-state.json").read_text(encoding="utf-8"))
    if state["persona"] != "Vera Example" or state["corpus_version"] != CORPUS_VERSION:
        raise AssertionError("Vera Example demo state version pin mismatch")
    if state["schema_version"] != CURRENT_SCHEMA_VERSION or state["envelope_version"] != ENVELOPE_VERSION:
        raise AssertionError("Vera Example demo schema/envelope pin mismatch")
    if (state["blocked_verify_exit"], state["blocked_export_exit"]) != (10, 10):
        raise AssertionError("Vera Example blocked-overclaim exit contract was not observed")

    blocked = state["blocked_snapshot_id"]
    published = state["published_snapshot_id"]
    branch = state["published_branch_id"]
    members = exported_bytes(workspace, published)
    branch_members = branch_bytes(workspace, branch)
    manifest = json.loads(members["manifest.json"])
    recorded = {item["name"]: item["sha256"] for item in manifest["members"]}
    for name in ("report.md", "report.html", "self_claims.json", "evidence_map.json"):
        if recorded.get(name) != hashlib.sha256(members[name]).hexdigest():
            raise AssertionError(f"Vera Example export manifest hash mismatch: {name}")
    evidence_map = AssessmentEvidenceMapDocument.model_validate_json(
        members["evidence_map.json"]
    )
    claims_document = SelfClaimsDocument.model_validate_json(
        members["self_claims.json"]
    )

    with read_database(workspace) as connection:
        # The mirror has one view, so Act 2's regeneration superseded Act 1's
        # rejected snapshot. That snapshot is read as history here: it stays
        # stored, it never reached `out/`, and its supersession is what proves
        # the refused overclaim left nothing behind.
        blocked_row = connection.execute(
            "SELECT * FROM assessment_snapshots WHERE id = ?", (blocked,)
        ).fetchone()
        if blocked_row is None:
            raise AssertionError("Vera Example blocked snapshot is missing")
        blocked_model = hydrate_assessment_snapshot(blocked_row)
        published_row, published_model = load_current_snapshot(connection, published)
        published_graph = load_assessment_graph(
            connection, snapshot_row=published_row, snapshot=published_model
        )
        if blocked_model.superseded_at is None:
            raise AssertionError("Vera Example Act 2 did not replace the blocked view")
        if blocked_model.verification_status != "rejected":
            raise AssertionError("Vera Example blocked snapshot is not rejected")
        if published_model.verification_status != "supported":
            raise AssertionError("Vera Example mirror snapshot is not supported/current")
        if (workspace / "out" / "assessment" / blocked).exists():
            raise AssertionError("Vera Example blocked export was published")
        _verify_branch(workspace, connection, branch, branch_members)

        claim_links = {item.claim_id: item for item in evidence_map.claim_links}
        fact_links = {item.fact_id: item for item in evidence_map.fact_links}
        evidence_links = {
            item.evidence_item_id: item for item in evidence_map.evidence_links
        }
        for claim_id in evidence_map.rendered_claim_ids:
            claim = claim_links[claim_id]
            # §15.4: the claim's own closure is the whole fact set the
            # discarded patterns cited, contrary members included.
            reached_facts = set(claim.source_fact_ids)
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

    if evidence_map.rendered_claim_ids != [
        item.value.id for item in published_graph.claims
    ]:
        raise AssertionError("Vera Example rendered claim set is not graph-complete")
    report = members["report.md"].decode("utf-8")
    # §16.14 keeps the persona's name out of generated prose, so the report
    # is checked against the corpus's own canned claim, gap, and
    # contradiction wording rather than against the ecosystem marker.
    if RENDERED_CLAIM not in report or not claims_document.unknowns:
        raise AssertionError("Vera Example report does not visibly render claim and gap")
    if RENDERED_GAP not in report:
        raise AssertionError("Vera Example report does not visibly render gap question")
    if RENDERED_CONTRADICTION not in report:
        raise AssertionError("Vera Example report does not visibly render contradiction")

    transcript = (workspace / "demo-transcript.txt").read_bytes()
    if (
        any(marker in transcript for marker in PRIVATE_HOME_MARKERS)
        or str(workspace).encode("utf-8") in transcript
    ):
        raise AssertionError("Vera Example transcript exposes an absolute private path")
    if golden is not None and transcript != golden:
        raise AssertionError("Vera Example checked transcript is stale; regenerate it")
    published_bytes = {
        **{f"assessment/{name}": value for name, value in members.items()},
        **{f"branch/{name}": value for name, value in branch_members.items()},
    }
    return published_bytes, transcript


def _verify_cast() -> None:
    lines = GOLDEN_CAST.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise AssertionError("Vera Example asciinema recording is missing or empty")
    header = json.loads(lines[0])
    if header != {
        "version": 2,
        "width": 110,
        "height": 32,
        "timestamp": header.get("timestamp"),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    } or not isinstance(header["timestamp"], int):
        raise AssertionError("Vera Example asciinema metadata pin is invalid")

    events = [json.loads(line) for line in lines[1:]]
    if any(
        not isinstance(event, list)
        or len(event) != 3
        or not isinstance(event[0], (int, float))
        or event[0] < 0
        or event[1] != "o"
        or not isinstance(event[2], str)
        for event in events
    ):
        raise AssertionError("Vera Example asciinema event stream is invalid")
    times = [event[0] for event in events]
    if times != sorted(times):
        raise AssertionError("Vera Example asciinema event times are not monotonic")

    output = "".join(event[2] for event in events).replace("\r\n", "\n")
    expected = "demo/workspace is already reset.\n" + GOLDEN_TRANSCRIPT.read_text(
        encoding="utf-8"
    )
    if output != expected:
        raise AssertionError("Vera Example asciinema recording is stale")
    raw = GOLDEN_CAST.read_bytes()
    if any(marker in raw for marker in PRIVATE_HOME_MARKERS):
        raise AssertionError("Vera Example asciinema recording exposes a private path")


def verify_demo(workspace: Path, *, check_golden: bool = True, determinism: bool = True) -> None:
    golden = GOLDEN_TRANSCRIPT.read_bytes() if check_golden else None
    current_members, current_transcript = _verify_one(workspace, golden=golden)
    _verify_cast()
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
        "manifest hashes, transcript, asciinema recording, and repeated-run byte "
        "determinism verified"
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
