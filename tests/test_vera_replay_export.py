"""Vera E6/E10/E11 replay the implemented CLI pipeline through export.

E6 pins the assessment set. E10 and E11 carry the same replay through the
§14.10 bullet commands over the two real corpus vacancies: E10 publishes a
pinned verified pack, and E11 is the honest-mirror path, where learning-grade
evidence never promotes into the vacancy's production demands.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import exp2res.services.assessment as assessment_service
import exp2res.services.bullets as bullets_service
import exp2res.services.capture as capture_service
import exp2res.services.detection as detection_service
import exp2res.services.extraction as extraction_service
import exp2res.services.job_descriptions as jd_service
from exp2res.pipeline.stage3 import run_fact_extraction
from exp2res.pipeline.stage4 import run_detection_generation
from exp2res.pipeline.stage6 import run_assessment_generation
from exp2res.pipeline.stage7 import run_assessment_verification
from exp2res.pipeline.stage8 import run_job_description_parse
from exp2res.pipeline.stage10 import run_bullet_generation
from exp2res.pipeline.stage11 import run_bullet_verification
from exp2res.services.capture import capture_daily
from exp2res.services.job_descriptions import list_job_descriptions
from exp2res.storage.repository import (
    list_resume_bullets_for_branch,
    list_self_claims_for_snapshot,
)
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW, REPOSITORY_ROOT, VERA_CORPUS
from fakes import FakeContractRunner
from assessment_helpers import VeraIds
from test_stage3_extraction import SELECTION, budgets, fact_response
from test_stage6_assessment import assessment_response
from test_stage7_verification import verifier_response
from test_vera_replay_assess import invoke_json


pytestmark = [pytest.mark.contract, pytest.mark.lifecycle]
GOLDENS = REPOSITORY_ROOT / "tests" / "goldens" / "assessment"
MEMBERS = ("report.md", "report.html", "self_claims.json", "evidence_map.json")
BRANCH_GOLDENS = REPOSITORY_ROOT / "tests" / "goldens" / "branch"
BRANCH_MEMBERS = ("bullet_pack.md", "evidence_map.json", "verification_report.json")
DOCS_VACANCY = VERA_CORPUS / "jds" / "jd-docs-engineer-examplia.md"
BACKEND_VACANCY = VERA_CORPUS / "jds" / "jd-junior-backend-clouddocs.md"


def _fixed_stage(real_stage, ids):
    def deterministic(selected_workspace: Path, **kwargs):
        kwargs.pop("id_factory", None)
        kwargs.pop("clock", None)
        return real_stage(
            selected_workspace,
            **kwargs,
            id_factory=ids,
            clock=lambda: FIXED_NOW,
            sleeper=lambda _seconds: None,
            jitter=lambda lower, _upper: lower,
        )

    return deterministic


def _run_cli_stage(
    monkeypatch: pytest.MonkeyPatch,
    service,
    real_stage,
    ids,
    response_bytes: list[bytes],
    workspace: Path,
    command: list[str],
    expected: set[int] = frozenset({0}),
):
    monkeypatch.setattr(service, "new_id", ids)
    monkeypatch.setattr(
        service,
        "build_llm_execution",
        lambda _workspace: (
            SELECTION,
            budgets(),
            FakeContractRunner(response_bytes),
        ),
    )
    stage_name = {
        extraction_service: "run_fact_extraction",
        detection_service: "run_detection_generation",
        assessment_service: (
            "run_assessment_verification"
            if real_stage is run_assessment_verification
            else "run_assessment_generation"
        ),
        jd_service: "run_job_description_parse",
        bullets_service: (
            "run_bullet_verification"
            if real_stage is run_bullet_verification
            else "run_bullet_generation"
        ),
    }[service]
    monkeypatch.setattr(service, stage_name, _fixed_stage(real_stage, ids))
    result, envelope = invoke_json(workspace, ["--yes", *command])
    assert result.exit_code in expected, (result.exit_code, result.stderr, envelope)
    return envelope


def test_vera_e6_cli_export_goldens_and_artifact_lifecycle(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = VeraIds()
    monkeypatch.setattr(capture_service, "new_id", ids)
    captured = capture_daily(
        workspace,
        raw_text=(
            "Vera Example built and validated a provenance-aware local workflow."
        ),
        project="Exp2Res",
        clock=lambda: FIXED_NOW,
        id_factory=ids,
    )

    extracted = _run_cli_stage(
        monkeypatch,
        extraction_service,
        run_fact_extraction,
        ids,
        [fact_response([captured.evidence_items[0].id])],
        workspace,
        ["extract"],
    )
    fact_id = extracted["affected_ids"]["created"][0]["ids"][0]

    detector_payload = json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "experience_fact",
                    "target_id": fact_id,
                    "question": "Which exact scale did you validate?",
                    "reason": "missing_scale",
                    "priority": "medium",
                }
            ],
            "contradictions": [],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    detected = _run_cli_stage(
        monkeypatch,
        detection_service,
        run_detection_generation,
        ids,
        [detector_payload],
        workspace,
        ["detections", "generate"],
    )
    gap_id = detected["result"]["gaps"][0]["id"]

    generated = _run_cli_stage(
        monkeypatch,
        assessment_service,
        run_assessment_generation,
        ids,
        [assessment_response(fact_ids=[fact_id])],
        workspace,
        ["assess", "generate"],
    )
    snapshot_id = next(
        group["ids"][0]
        for group in generated["affected_ids"]["created"]
        if group["entity_type"] == "assessment_snapshot"
    )
    claim_count = next(
        len(group["ids"])
        for group in generated["affected_ids"]["created"]
        if group["entity_type"] == "self_claim"
    )

    _run_cli_stage(
        monkeypatch,
        assessment_service,
        run_assessment_verification,
        ids,
        [verifier_response()] * claim_count,
        workspace,
        ["assess", "verify", "--snapshot", snapshot_id],
    )

    first_result, first = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert first_result.exit_code == 0
    assert first["result"].keys() == {"manifest_path", "managed_paths"}
    final_set = workspace / "out" / "assessment" / snapshot_id
    first_bytes = {name: (final_set / name).read_bytes() for name in MEMBERS}
    first_manifest = json.loads((final_set / "manifest.json").read_text())
    assert first_manifest["manifest_version"] == 6

    second_result, second = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert second_result.exit_code == 0
    assert second["result"] == first["result"]
    second_bytes = {name: (final_set / name).read_bytes() for name in MEMBERS}
    second_manifest = json.loads((final_set / "manifest.json").read_text())
    assert second_bytes == first_bytes
    assert second_manifest["members"] == first_manifest["members"]
    assert {
        row["name"]: row["sha256"] for row in second_manifest["members"]
    } == {
        name: hashlib.sha256(member).hexdigest()
        for name, member in second_bytes.items()
    }
    for name, member in first_bytes.items():
        assert member == (GOLDENS / name).read_bytes()

    answer_source = VERA_CORPUS / "logs" / "daily-2026-06-20.md"
    answered_result, answered = invoke_json(
        workspace,
        [
            "gaps",
            "answer",
            "--gap-id",
            gap_id,
            "--file",
            str(answer_source),
        ],
    )
    assert answered_result.exit_code == 0, (answered_result.stderr, answered)
    assert not final_set.exists()
    reexported_result, _reexported = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert reexported_result.exit_code == 0
    assert b"**Answered since synthesis:** yes" in (final_set / "report.md").read_bytes()
    companion = json.loads((final_set / "self_claims.json").read_text())
    assert companion["unknowns"] == [
        {
            "answered": True,
            "id": gap_id,
            "priority": "medium",
            "question": "Which exact scale did you validate?",
            "reason": "missing_scale",
            "target_id": fact_id,
            "target_type": "experience_fact",
        }
    ]

    # §13.4: a replacement detection set supersedes the current snapshot,
    # so the published set it backed is invalidated and never republished.
    replacement_payload = json.dumps(
        {
            "gap_questions": [
                {
                    "target_type": "experience_fact",
                    "target_id": fact_id,
                    "question": "Which environment did you validate in?",
                    "reason": "missing_context",
                    "priority": "medium",
                }
            ],
            "contradictions": [],
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    replaced = _run_cli_stage(
        monkeypatch,
        detection_service,
        run_detection_generation,
        ids,
        [replacement_payload],
        workspace,
        ["detections", "generate"],
    )
    assert replaced["invalidated_views"][0]["snapshot_id"] == snapshot_id
    assert not final_set.exists()
    stale_result, stale = invoke_json(
        workspace, ["export", "assessment", "--snapshot", snapshot_id]
    )
    assert stale_result.exit_code == 2
    assert stale["diagnostic_class"] == "snapshot_not_current"


def _replay_step(step: str) -> dict:
    contract = json.loads((VERA_CORPUS / "replay.json").read_text(encoding="utf-8"))
    return next(item for item in contract["derived_steps"] if item["step"] == step)


def _jd_response(
    *,
    title: str,
    company: str,
    requirements: list[tuple[str, str, list[str]]],
    domain_signals: list[str],
    red_flags: list[str],
) -> bytes:
    """One §15.9 parse that keeps each demand's wording and modality."""

    return json.dumps(
        {
            "title": title,
            "company": company,
            "parsed": {
                "requirements": [
                    {"kind": kind, "text": text, "keywords": list(keywords)}
                    for kind, text, keywords in requirements
                ],
                "seniority_signals": [],
                "domain_signals": domain_signals,
                "keywords": sorted(
                    {word for _kind, _text, words in requirements for word in words}
                ),
                "red_flags": red_flags,
            },
            "warnings": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")


DOCS_PARSE = _jd_response(
    title="Technical Writer, Platform Documentation",
    company="Examplia GmbH",
    requirements=[
        (
            "required_skill",
            "Two or more years writing developer-facing documentation.",
            ["developer documentation", "technical writing"],
        ),
        (
            "required_skill",
            "Hands-on familiarity with Kubernetes concepts and kubectl workflows.",
            ["Kubernetes", "kubectl"],
        ),
        (
            "required_skill",
            "Comfortable working in Git: branches, pull requests, reviews.",
            ["Git", "pull requests"],
        ),
        (
            "preferred_skill",
            "Python scripting for documentation tooling (link checkers, linters).",
            ["Python", "documentation tooling"],
        ),
        (
            "preferred_skill",
            "Experience producing video tutorials.",
            ["video tutorials"],
        ),
        ("preferred_skill", "On-page SEO basics.", ["SEO"]),
    ],
    domain_signals=["internal developer platform", "platform documentation"],
    red_flags=["The vacancy declares itself a fictional demo posting."],
)

BACKEND_PARSE = _jd_response(
    title="Junior Backend Engineer (Python)",
    company="Cloud Example Systems",
    requirements=[
        (
            "required_skill",
            "Production experience running Python services.",
            ["Python", "production"],
        ),
        (
            "required_skill",
            "Experience operating PostgreSQL in production.",
            ["PostgreSQL", "production"],
        ),
        (
            "required_skill",
            "Participation in an on-call rotation.",
            ["on-call rotation"],
        ),
        ("preferred_skill", "Familiarity with Kubernetes.", ["Kubernetes"]),
        (
            "preferred_skill",
            "Interest in distributed systems.",
            ["distributed systems"],
        ),
    ],
    domain_signals=["backend services", "cloud operations"],
    red_flags=[
        "The vacancy carries a section addressed to agents asking for the "
        "requirements to be ignored; it is vacancy text, never an instruction."
    ],
)


def _bullet(
    text: str,
    *,
    section: str,
    requirements: list[str],
    fact_ids: list[str],
    claim_ids: list[str] | None = None,
    relevance: str = "high",
) -> dict[str, object]:
    return {
        "text": text,
        "target_section": section,
        "target_role_relevance": relevance,
        "matched_jd_requirements": requirements,
        "source_fact_ids": fact_ids,
        "source_self_claim_ids": claim_ids or [],
    }


def _writer_response(bullets: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {"bullets": bullets, "warnings": []}, separators=(",", ":")
    ).encode("utf-8")


def _bullet_finding(
    bullet_id: str,
    *,
    reason: str,
    status: str = "supported",
    phrases: list[str] | None = None,
) -> dict[str, object]:
    return {
        "bullet_id": bullet_id,
        "status": status,
        "unsupported_phrases": phrases or [],
        "suggested_rewrite": None,
        "reason": reason,
    }


def _bullet_verifier_response(findings: list[dict[str, object]]) -> bytes:
    return json.dumps({"findings": findings}, separators=(",", ":")).encode("utf-8")


ANCHOR_LOG = (
    "Vera Example drafted the kubectl troubleshooting runbook and walked every "
    "step on a toy cluster before writing it down. Vera Example also wrote the "
    "small Python script that checks the runbook's links, and put every runbook "
    "change through a reviewed pull request."
)


def _anchor_facts(evidence_id: str) -> bytes:
    """The two facts ANCHOR_LOG states, and nothing the log does not say."""

    return json.dumps(
        {
            "facts": [
                {
                    "claim": (
                        "Drafted a kubectl troubleshooting runbook and walked "
                        "every documented step on a toy cluster first."
                    ),
                    "claim_kind": "observed_fact",
                    "role": None,
                    "company": None,
                    "context": "independent_project",
                    "ownership_level": "built",
                    "action": "drafted",
                    "object": "a kubectl troubleshooting runbook",
                    "outcome": None,
                    "skills": ["technical writing", "kubernetes troubleshooting"],
                    "technologies": ["Kubernetes", "kubectl"],
                    "themes": ["documentation"],
                    "occurred": None,
                    "evidence_item_ids": [evidence_id],
                    "confidence": "medium",
                },
                {
                    "claim": (
                        "Wrote the runbook's Python link checker and took every "
                        "runbook change through a reviewed pull request."
                    ),
                    "claim_kind": "observed_fact",
                    "role": None,
                    "company": None,
                    "context": "independent_project",
                    "ownership_level": "built",
                    "action": "scripted",
                    "object": "a Python link checker for the runbook",
                    "outcome": None,
                    "skills": ["documentation tooling", "code review"],
                    "technologies": ["Python", "Git"],
                    "themes": ["documentation"],
                    "occurred": None,
                    "evidence_item_ids": [evidence_id],
                    "confidence": "medium",
                },
            ],
            "warnings": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _verified_anchor(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, ids: VeraIds
) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    """E4/E5 in miniature: current facts under a `supported` snapshot."""

    monkeypatch.setattr(capture_service, "new_id", ids)
    captured = capture_daily(
        workspace,
        raw_text=ANCHOR_LOG,
        project="K8s Playbook",
        clock=lambda: FIXED_NOW,
        id_factory=ids,
    )
    extracted = _run_cli_stage(
        monkeypatch,
        extraction_service,
        run_fact_extraction,
        ids,
        [_anchor_facts(captured.evidence_items[0].id)],
        workspace,
        ["extract"],
    )
    fact_ids = tuple(extracted["affected_ids"]["created"][0]["ids"])

    generated = _run_cli_stage(
        monkeypatch,
        assessment_service,
        run_assessment_generation,
        ids,
        [assessment_response(fact_ids=list(fact_ids))],
        workspace,
        ["assess", "generate"],
    )
    snapshot_id = next(
        group["ids"][0]
        for group in generated["affected_ids"]["created"]
        if group["entity_type"] == "assessment_snapshot"
    )
    with read_database(workspace) as connection:
        claim_ids = tuple(
            claim.id
            for claim in list_self_claims_for_snapshot(connection, snapshot_id)
        )
    _run_cli_stage(
        monkeypatch,
        assessment_service,
        run_assessment_verification,
        ids,
        [verifier_response()] * len(claim_ids),
        workspace,
        ["assess", "verify", "--snapshot", snapshot_id],
    )
    return fact_ids, snapshot_id, claim_ids


def _add_vacancy(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    ids: VeraIds,
    *,
    source: Path,
    response: bytes,
):
    """§14.10 `jd add` over the corpus vacancy file itself, never a paraphrase."""

    added = _run_cli_stage(
        monkeypatch,
        jd_service,
        run_job_description_parse,
        ids,
        [response],
        workspace,
        ["jd", "add", str(source)],
    )
    job_description_id = added["affected_ids"]["created"][0]["ids"][0]
    stored = next(
        item
        for item in list_job_descriptions(workspace)
        if item.id == job_description_id
    )
    assert stored.raw_text == source.read_text(encoding="utf-8")
    return stored


def _current_bullets(workspace: Path, branch_id: str):
    with read_database(workspace) as connection:
        return list_resume_bullets_for_branch(connection, branch_id)


def test_vera_e10_cli_bullet_pack_matches_pinned_goldens(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E10: the docs vacancy publishes a byte-pinned two-bullet pack."""

    step = _replay_step("E10")
    ids = VeraIds()
    fact_ids, snapshot_id, claim_ids = _verified_anchor(workspace, monkeypatch, ids)
    vacancy = _add_vacancy(
        workspace, monkeypatch, ids, source=DOCS_VACANCY, response=DOCS_PARSE
    )
    requirements = list(vacancy.parsed.requirements)

    generated = _run_cli_stage(
        monkeypatch,
        bullets_service,
        run_bullet_generation,
        ids,
        [
            _writer_response(
                [
                    _bullet(
                        "Drafted and validated a kubectl troubleshooting runbook. "
                        "Every documented step was walked on a toy cluster first.",
                        section="selected_projects",
                        requirements=[requirements[1].id],
                        fact_ids=[fact_ids[0]],
                        claim_ids=[claim_ids[0]],
                    ),
                    _bullet(
                        "Kept the runbook's link checks scripted in Python and "
                        "took every change through a reviewed pull request.",
                        section="skills",
                        requirements=[requirements[2].id, requirements[3].id],
                        fact_ids=[fact_ids[1]],
                    ),
                ]
            )
        ],
        workspace,
        [
            "bullets",
            "generate",
            "--jd",
            vacancy.id,
            "--snapshot",
            snapshot_id,
            "--branch",
            step["branch"],
        ],
    )
    branch_id = next(
        group["ids"][0]
        for group in generated["affected_ids"]["created"]
        if group["entity_type"] == "resume_branch"
    )
    bullets = _current_bullets(workspace, branch_id)
    assert len(bullets) >= step["expect"]["supported_bullets_min"]

    matched = {
        requirement_id
        for bullet in bullets
        for requirement_id in bullet.matched_jd_requirements
    }
    unmatched = [item.text for item in requirements if item.id not in matched]
    assert unmatched == [
        "Two or more years writing developer-facing documentation.",
        "Experience producing video tutorials.",
        "On-page SEO basics.",
    ]
    for token, text in zip(step["expect"]["unmatched_requirements"], unmatched):
        assert token in text

    verified = _run_cli_stage(
        monkeypatch,
        bullets_service,
        run_bullet_verification,
        ids,
        [
            _bullet_verifier_response(
                [
                    _bullet_finding(
                        bullet.id,
                        reason=(
                            "Every material assertion resolves to the linked "
                            "fact, evidence item, and raw log."
                        ),
                    )
                    for bullet in bullets
                ]
            )
        ],
        workspace,
        ["bullets", "verify", "--branch", step["branch"]],
    )
    assert verified["status"] == step["expect"]["status"] == "ok"
    assert all(
        item.verification_status == "supported"
        for item in _current_bullets(workspace, branch_id)
    )

    first_result, first = invoke_json(
        workspace, ["bullets", "export", "--branch", step["branch"]]
    )
    assert first_result.exit_code == 0, (first_result.stderr, first)
    published = workspace / "out" / "branch" / branch_id
    first_bytes = {name: (published / name).read_bytes() for name in BRANCH_MEMBERS}

    second_result, second = invoke_json(
        workspace, ["bullets", "export", "--branch", step["branch"]]
    )
    assert second_result.exit_code == 0
    assert second["result"] == first["result"]
    second_bytes = {name: (published / name).read_bytes() for name in BRANCH_MEMBERS}
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    assert second_bytes == first_bytes
    assert {row["name"]: row["sha256"] for row in manifest["members"]} == {
        name: hashlib.sha256(member).hexdigest()
        for name, member in second_bytes.items()
    }
    for name, member in first_bytes.items():
        assert member == (BRANCH_GOLDENS / name).read_bytes()


def test_vera_e11_cli_learning_evidence_never_reaches_production_claims(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E11: the §16.11 refusal runs end to end and publishes nothing."""

    step = _replay_step("E11")
    ids = VeraIds()
    fact_ids, snapshot_id, claim_ids = _verified_anchor(workspace, monkeypatch, ids)
    vacancy = _add_vacancy(
        workspace, monkeypatch, ids, source=BACKEND_VACANCY, response=BACKEND_PARSE
    )
    demanded = {item.text: item.id for item in vacancy.parsed.requirements}
    blocked_demands = {
        demanded[text]
        for text in (
            "Production experience running Python services.",
            "Experience operating PostgreSQL in production.",
            "Participation in an on-call rotation.",
        )
    }

    generated = _run_cli_stage(
        monkeypatch,
        bullets_service,
        run_bullet_generation,
        ids,
        [
            _writer_response(
                [
                    _bullet(
                        "Worked hands-on with Kubernetes while writing and "
                        "checking cluster runbooks.",
                        section="selected_projects",
                        requirements=[demanded["Familiarity with Kubernetes."]],
                        fact_ids=[fact_ids[0]],
                        claim_ids=[claim_ids[0]],
                    ),
                    _bullet(
                        "Ran production Python services on PostgreSQL through an "
                        "on-call rotation.",
                        section="professional_experience",
                        requirements=sorted(blocked_demands),
                        fact_ids=[fact_ids[0]],
                    ),
                ]
            )
        ],
        workspace,
        [
            "bullets",
            "generate",
            "--jd",
            vacancy.id,
            "--snapshot",
            snapshot_id,
            "--branch",
            step["branch"],
        ],
    )
    assert generated["status"] == step["expect"]["status"] == "ok"
    branch_id = next(
        group["ids"][0]
        for group in generated["affected_ids"]["created"]
        if group["entity_type"] == "resume_branch"
    )
    bullets = _current_bullets(workspace, branch_id)
    overclaim = next(item for item in bullets if item.text.startswith("Ran production"))
    supported = [item for item in bullets if item.id != overclaim.id]
    assert len(supported) >= step["expect"]["supported_bullets_min"]

    verified = _run_cli_stage(
        monkeypatch,
        bullets_service,
        run_bullet_verification,
        ids,
        [
            _bullet_verifier_response(
                [
                    _bullet_finding(
                        overclaim.id,
                        status="rejected",
                        phrases=[
                            "production Python services",
                            "PostgreSQL",
                            "on-call rotation",
                        ],
                        reason=(
                            "The supplied records carry learning-grade runbook "
                            "work and no production operation of any service."
                        ),
                    ),
                    *(
                        _bullet_finding(
                            item.id,
                            reason=(
                                "The runbook work resolves to the linked fact, "
                                "evidence item, and raw log."
                            ),
                        )
                        for item in supported
                    ),
                ]
            )
        ],
        workspace,
        ["bullets", "verify", "--branch", step["branch"]],
        expected={10},
    )
    assert verified["status"] == "blocked"

    stored = {item.id: item for item in _current_bullets(workspace, branch_id)}
    assert stored[overclaim.id].verification_status == "rejected"
    assert all(stored[item.id].verification_status == "supported" for item in supported)
    for claim in step["expect"]["blocked_claims"]:
        assert claim in overclaim.text or claim.split()[0] in overclaim.text
    assert blocked_demands == set(stored[overclaim.id].matched_jd_requirements)
    assert not blocked_demands & {
        requirement_id
        for item in supported
        for requirement_id in stored[item.id].matched_jd_requirements
    }

    blocked_result, blocked = invoke_json(
        workspace, ["bullets", "export", "--branch", step["branch"]]
    )
    assert blocked_result.exit_code == 10
    assert blocked["diagnostic_class"] == "bullet_pack_export_blocked"
    assert not (workspace / "out" / "branch" / branch_id).exists()
