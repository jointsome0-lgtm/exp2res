"""§13.11 single-pass bullet verification for one verified bullet pack."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence, cast

from pydantic import BaseModel, ValidationError

from exp2res.domain.enums import VerificationStatus
from exp2res.domain.models import (
    EvidenceItem,
    ExperienceFact,
    JobDescription,
    RawLog,
    ResumeBranch,
    ResumeBullet,
    SelfClaim,
    VerificationFinding,
)
from exp2res.errors import (
    IntegrityFailureError,
    OperationCancelledError,
    SelectorNotFoundError,
)
from exp2res.exports.managed import branch_set_paths, remove_branch_sets
from exp2res.llm.contracts import (
    ContractValidationError,
    validation_diagnostics,
)
from exp2res.llm.registry import LLMSelection
from exp2res.llm.resume_verifier import (
    RESUME_VERIFIER_CONTRACT,
    ResumeVerifierInput,
    ResumeVerifierOutput,
    VerifierJobDescription,
)
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    BULLET_EXPORT_ALLOWLIST,
    bullet_log_closure,
    current_branch_by_folded_name,
    get_assessment_snapshot,
    get_experience_fact,
    get_job_description,
    get_raw_log,
    insert_verification_finding,
    list_resume_bullets_for_branch,
    list_self_claims_for_snapshot,
    list_verification_findings,
    update_resume_bullet_verification,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    report_managed_residuals,
    withdraw_managed_residuals,
    writer_database,
)

from .evidence_context import project_evidence_context
from .orchestration import (
    PlannedCall,
    run_complete_stage,
    unfinished_stale_paths,
)
from .stage10 import validated_branch_name


@dataclass(frozen=True)
class Stage11Result:
    run_id: str
    branch_id: str
    branch_name: str
    findings: tuple[VerificationFinding, ...]
    bullet_statuses: tuple[tuple[str, VerificationStatus], ...]
    export_blocked: bool
    residual_paths: tuple[str, ...]


@dataclass(frozen=True)
class _VerifierBundle:
    input_payload: ResumeVerifierInput
    bullet_ids: tuple[str, ...]
    input_ids: tuple[str, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _current_bullets(
    connection: sqlite3.Connection, branch_id: str
) -> tuple[ResumeBullet, ...]:
    bullets = list_resume_bullets_for_branch(connection, branch_id, current_only=True)
    if not bullets:
        # Stage 10 never commits a branch without a bullet — an empty writer
        # array persists neither — so a current branch with no current bullet
        # is damaged state, not a verifiable pack.
        raise IntegrityFailureError("branch_bullet_set_empty")
    require_one_generation(connection, branch_id)
    return bullets


def require_one_generation(connection: sqlite3.Connection, branch_id: str) -> None:
    """Fail closed on a pack assembled from more than one Stage 10 batch.

    §12 rule 13 makes a branch and its bullets one jointly swapped batch, and
    `validate_bullet_production` establishes that at insert. The columns
    carrying it are storage-only — §11 hydration drops them — so a restored or
    migrated row from another run stays invisible to the loaded objects while
    making the pack span two swaps, with the supersession half-current.
    """

    mismatched = connection.execute(
        """
        SELECT COUNT(*) AS mismatched
        FROM resume_bullets AS bullet
        JOIN resume_branches AS branch ON branch.id = bullet.branch_id
        WHERE bullet.branch_id = ?
          AND bullet.superseded_at IS NULL
          AND (
            bullet.produced_by_run_id IS NOT branch.produced_by_run_id
            OR bullet.generation_id IS NOT branch.generation_id
          )
        """,
        (branch_id,),
    ).fetchone()["mismatched"]
    if mismatched:
        raise IntegrityFailureError("bullet_generation_mismatch")


def require_current_anchor(
    connection: sqlite3.Connection, branch: ResumeBranch
) -> None:
    """Fail closed on a branch whose §18 assessment anchor no longer resolves.

    `validate_branch_production` establishes a present, current anchor at
    insert and §13.13 rule 4 supersedes the branch with its snapshot, so a
    current branch pointing at a missing or superseded one is restored,
    migrated, or damaged state. `_build_bundle` alone would not see it: a
    facts-only pack cites no self-claim, so the snapshot's member lookup comes
    back legitimately empty and the dead anchor stays invisible through the
    call and into a persisted verdict §18 could not honour.
    """

    if (
        get_assessment_snapshot(
            connection, branch.assessment_snapshot_id, current_only=False
        )
        is None
    ):
        raise IntegrityFailureError("branch_snapshot_missing")
    if (
        get_assessment_snapshot(connection, branch.assessment_snapshot_id) is None
    ):
        raise IntegrityFailureError("branch_snapshot_superseded")


def require_consistent_bullets(
    connection: sqlite3.Connection,
    bullets: Sequence[ResumeBullet],
    job_description: JobDescription,
) -> None:
    """Fail closed on a bullet whose own typed references disagree (§15.7).

    Both relations are §12 rule 10 invariants that `insert_resume_bullet`
    establishes and no current-row update may change, so reaching either
    failure means the stored graph disagrees with itself — restored, migrated,
    or damaged state. Checking them here keeps that state from being charged
    to a provider and from receiving a semantic verdict §18 would then have to
    refuse at export.
    """

    requirement_ids = [
        requirement.id for requirement in job_description.parsed.requirements
    ]
    for bullet in bullets:
        for requirement_id in bullet.matched_jd_requirements:
            if requirement_ids.count(requirement_id) != 1:
                raise IntegrityFailureError("bullet_requirement_unresolved")
        # §18: `source_log_ids` equals — not contains — the closure reached
        # through this bullet's own facts. A displaced record contributes its
        # identity here exactly like a retained one; only the *object* is
        # withheld, in `_build_bundle` below.
        closure = bullet_log_closure(connection, bullet.source_fact_ids)
        if tuple(bullet.source_log_ids) != closure:
            raise IntegrityFailureError("bullet_log_closure_mismatch")


def _build_bundle(
    connection: sqlite3.Connection,
    *,
    branch: ResumeBranch,
    bullets: Sequence[ResumeBullet],
    job_description: JobDescription,
) -> _VerifierBundle:
    """Assemble §15.7's provenance context under §13.3 rule 10.

    Each array is exactly the duplicate-free set the supplied bullets name.
    `source_logs` is narrower by construction: a displaced record's identity
    stays visible in every bullet's `source_log_ids`, but its object is never
    hydrated, so displacement withholds prose without withholding provenance.
    This contract serializes no `EvidenceItem` at all.
    """

    fact_ids = {fact_id for bullet in bullets for fact_id in bullet.source_fact_ids}
    source_facts: list[ExperienceFact] = []
    for fact_id in sorted(fact_ids, key=_id_key):
        fact = get_experience_fact(connection, fact_id)
        if fact is None:
            raise IntegrityFailureError("bullet_fact_missing")
        if fact.superseded_at is not None:
            raise IntegrityFailureError("bullet_fact_superseded")
        source_facts.append(fact)

    evidence_context = project_evidence_context(
        connection, source_facts, missing_diagnostic="bullet_evidence_missing"
    )
    retained_log_ids = {
        item.raw_log_id for item in evidence_context if isinstance(item, EvidenceItem)
    }
    source_logs: list[RawLog] = []
    for log_id in sorted(retained_log_ids, key=_id_key):
        raw_log = get_raw_log(connection, log_id)
        if raw_log is None:
            raise IntegrityFailureError("bullet_source_log_missing")
        source_logs.append(raw_log)

    claim_ids = {
        claim_id for bullet in bullets for claim_id in bullet.source_self_claim_ids
    }
    # §18: a cited claim must be a current member of the branch's own anchor
    # snapshot, so the snapshot's membership is both the lookup and the check.
    members = {
        claim.id: claim
        for claim in list_self_claims_for_snapshot(
            connection, branch.assessment_snapshot_id, current_only=True
        )
    }
    source_claims: list[SelfClaim] = []
    for claim_id in sorted(claim_ids, key=_id_key):
        claim = members.get(claim_id)
        if claim is None:
            raise IntegrityFailureError("bullet_claim_outside_branch_snapshot")
        if claim.verification_status != "supported":
            # §18 admits only a supported cited claim, and Stage 10 checked the
            # same thing at insert; membership alone would let a claim whose
            # status changed underneath ground a `supported` bullet the pack
            # gate would then have to refuse.
            raise IntegrityFailureError("bullet_claim_not_supported")
        source_claims.append(claim)

    input_payload = ResumeVerifierInput(
        resume_bullets=list(bullets),
        source_facts=source_facts,
        source_logs=source_logs,
        source_self_claims=source_claims,
        job_description=VerifierJobDescription(
            id=job_description.id, parsed=job_description.parsed
        ),
    )
    # §12.13 telemetry names every transited entity, including the evidence
    # items the closure walked through even though no item object transits.
    input_ids = tuple(
        sorted(
            {
                branch.id,
                job_description.id,
                *(
                    requirement.id
                    for requirement in job_description.parsed.requirements
                ),
                *(bullet.id for bullet in bullets),
                *(fact.id for fact in source_facts),
                *(item.id for item in evidence_context),
                *(log_id for bullet in bullets for log_id in bullet.source_log_ids),
                *(claim.id for claim in source_claims),
            },
            key=_id_key,
        )
    )
    return _VerifierBundle(
        input_payload=input_payload,
        bullet_ids=tuple(bullet.id for bullet in bullets),
        input_ids=input_ids,
    )


def _enrich_for(
    bullet_ids: Sequence[str],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """§15.7: exactly one finding per supplied bullet, and no other ID."""

    expected = set(bullet_ids)

    def enrich(decoded: dict[str, Any]) -> dict[str, Any]:
        try:
            output = ResumeVerifierOutput.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
            )
        except ValidationError as error:
            raise ContractValidationError(
                validation_diagnostics(RESUME_VERIFIER_CONTRACT, error.errors())
            ) from None

        errors: list[dict[str, object]] = []
        seen: set[str] = set()
        for index, finding in enumerate(output.findings):
            if finding.bullet_id in seen:
                errors.append(
                    {"loc": ("findings", index, "bullet_id"), "type": "duplicate_target"}
                )
            elif finding.bullet_id not in expected:
                errors.append(
                    {
                        "loc": ("findings", index, "bullet_id"),
                        "type": "out_of_context_target",
                    }
                )
            seen.add(finding.bullet_id)
        if expected - seen:
            errors.append({"loc": ("findings",), "type": "incomplete_finding_set"})
        if errors:
            raise ContractValidationError(
                validation_diagnostics(RESUME_VERIFIER_CONTRACT, errors)
            )
        return decoded

    return enrich


def _resolve_for(
    *,
    run_id: str,
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> Callable[[BaseModel], object]:
    def resolve(validated: BaseModel) -> object:
        output = cast(ResumeVerifierOutput, validated)
        return tuple(
            VerificationFinding(
                id=id_factory("finding"),
                created_at=clock(),
                produced_by_run_id=run_id,
                target_type="resume_bullet",
                target_id=item.bullet_id,
                status=item.status,
                reason=item.reason,
                unsupported_phrases=list(item.unsupported_phrases),
                suggested_rewrite=item.suggested_rewrite,
            )
            for item in output.findings
        )

    return resolve


def _verifier_state(
    bullets: Sequence[ResumeBullet],
) -> tuple[tuple[str, str, tuple[str, ...], str | None], ...]:
    """The three denormalized fields whose change invalidates the branch set."""

    return tuple(
        (
            bullet.id,
            bullet.verification_status,
            tuple(bullet.unsupported_phrases),
            bullet.verifier_reason,
        )
        for bullet in bullets
    )


def _result(
    *,
    run_id: str,
    branch: ResumeBranch,
    findings: tuple[VerificationFinding, ...],
    statuses: tuple[tuple[str, VerificationStatus], ...],
    blocked: bool,
    residuals: tuple[str, ...],
) -> Stage11Result:
    return Stage11Result(
        run_id=run_id,
        branch_id=branch.id,
        branch_name=branch.name,
        findings=findings,
        bullet_statuses=statuses,
        export_blocked=blocked,
        residual_paths=residuals,
    )


def _recovered_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    branch: ResumeBranch,
    residuals: tuple[str, ...],
) -> Stage11Result:
    """Re-read the committed pass for a §14.14 rule 6 cancelled envelope.

    The interrupt may have landed before or after any of the ordinary reads,
    so this repeats them instead of reusing partial locals; a read that fails
    against the interrupted connection degrades to the empty projection rather
    than replacing the cancellation with its own error.
    """

    try:
        findings = tuple(
            sorted(
                list_verification_findings(connection, run_id=run_id),
                key=lambda item: _id_key(item.id),
            )
        )
        statuses = tuple(
            (bullet.id, bullet.verification_status)
            for bullet in list_resume_bullets_for_branch(
                connection, branch.id, current_only=True
            )
        )
    except Exception:
        findings, statuses = (), ()
    return _result(
        run_id=run_id,
        branch=branch,
        findings=findings,
        statuses=statuses,
        blocked=any(
            status not in BULLET_EXPORT_ALLOWLIST for _bullet_id, status in statuses
        ),
        residuals=residuals,
    )


def run_bullet_verification(
    workspace: Path,
    *,
    branch_name: str,
    selection: LLMSelection,
    budgets: CallBudgets,
    runner: ContractRunner,
    id_factory: Callable[[str], str] = new_id,
    clock: Callable[[], datetime] | None = None,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    cli_version: str = "test-double",
    capability_check: Callable[[], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] | None = None,
    token_patterns: Iterable[Pattern[bytes]] | None = None,
    resolved_credentials: Iterable[bytes] = (),
) -> Stage11Result:
    """Run the one whole-branch verifier call and commit its complete state."""

    now = clock or (lambda: datetime.now(timezone.utc))
    branch_name = validated_branch_name(branch_name)
    with writer_database(
        workspace, timeout_ms=timeout_ms, reconcile=True
    ) as connection:
        branch = current_branch_by_folded_name(connection, branch_name)
        if branch is None:
            raise SelectorNotFoundError()
        bullets = _current_bullets(connection, branch.id)
        # §13.12/§18: both consumers recover the vacancy through the branch's
        # persisted association, and a missing one fails verification.
        job_description = get_job_description(connection, branch.job_description_id)
        if job_description is None:
            raise IntegrityFailureError("branch_job_description_missing")
        require_current_anchor(connection, branch)
        require_consistent_bullets(connection, bullets, job_description)

        prior_state = _verifier_state(bullets)
        bundle = _build_bundle(
            connection,
            branch=branch,
            bullets=bullets,
            job_description=job_description,
        )

        run_id = id_factory("run")
        planned = (
            PlannedCall(
                input_payload=bundle.input_payload,
                input_ids=bundle.input_ids,
                enrich=_enrich_for(bundle.bullet_ids),
                resolve=_resolve_for(
                    run_id=run_id, id_factory=id_factory, clock=now
                ),
            ),
        )

        pending_stale_paths: tuple[str, ...] = ()

        def commit(
            held: sqlite3.Connection, resolved: Sequence[object]
        ) -> Iterable[str]:
            nonlocal pending_stale_paths
            findings = cast(tuple[VerificationFinding, ...], resolved[0])
            if {item.target_id for item in findings} != set(bundle.bullet_ids):
                raise IntegrityFailureError("verification_bullet_set_mismatch")
            for finding in findings:
                update_resume_bullet_verification(
                    held,
                    bullet_id=finding.target_id,
                    verification_status=finding.status,
                    unsupported_phrases=finding.unsupported_phrases,
                    verifier_reason=finding.reason,
                )
                # §15.7 returns no counterevidence, so a Stage 11 finding
                # grounds none and needs no bundle-reference allowance.
                insert_verification_finding(held, finding, bundle_refs=frozenset())
            fresh = _verifier_state(
                list_resume_bullets_for_branch(held, branch.id, current_only=True)
            )
            if fresh != prior_state:
                # §13.11: a changed verdict may not leave an older valid
                # manifest current, so the branch's published set is reported
                # stale before the commit-to-cleanup window opens.
                pending_stale_paths = branch_set_paths(workspace, (branch.id,))
                report_managed_residuals(pending_stale_paths)
            return tuple(item.id for item in findings)

        try:
            run_complete_stage(
                workspace,
                connection,
                stage="13.11",
                contract=RESUME_VERIFIER_CONTRACT,
                selection=selection,
                budgets=budgets,
                runner=runner,
                planned=planned,
                commit=commit,
                run_id=run_id,
                clock=now,
                cli_version=cli_version,
                capability_check=capability_check,
                monotonic=monotonic,
                sleeper=sleeper,
                jitter=jitter,
                token_patterns=token_patterns,
                resolved_credentials=resolved_credentials,
            )
        except BaseException:
            # Withdraw the pre-commit pending report only on a proven rollback.
            # Stage 11 supersedes nothing, so the committed marker is the
            # verifier-state change itself; an indeterminate read keeps the
            # report, because a spurious residual is the recoverable error.
            if pending_stale_paths:
                committed = True
                try:
                    if not connection.in_transaction:
                        committed = (
                            _verifier_state(
                                list_resume_bullets_for_branch(
                                    connection, branch.id, current_only=True
                                )
                            )
                            != prior_state
                        )
                except Exception:
                    committed = True
                if not committed:
                    withdraw_managed_residuals(pending_stale_paths)
            raise

        # §14.14 rule 6: the pass is durable from here on, so the whole
        # read-compose-cleanup window is guarded — an interrupt anywhere in it
        # reports the committed verdicts rather than an empty cancellation.
        cleaned_sets: list[str] = []
        try:
            findings = tuple(
                sorted(
                    list_verification_findings(connection, run_id=run_id),
                    key=lambda item: _id_key(item.id),
                )
            )
            verified = list_resume_bullets_for_branch(
                connection, branch.id, current_only=True
            )
            statuses = tuple(
                (bullet.id, bullet.verification_status) for bullet in verified
            )
            # §16.11: the pack's allowlist is exactly `supported`, so one
            # bullet outside it leaves the whole branch ineligible for export.
            blocked = any(
                status not in BULLET_EXPORT_ALLOWLIST
                for _bullet_id, status in statuses
            )
            if _verifier_state(verified) == prior_state:
                return _result(
                    run_id=run_id,
                    branch=branch,
                    findings=findings,
                    statuses=statuses,
                    blocked=blocked,
                    residuals=(),
                )
            # Cleanup failure never rolls the committed pass back.
            residual_paths = remove_branch_sets(
                workspace, (branch.id,), removed_ledger=cleaned_sets
            )
        except KeyboardInterrupt:
            # Only the set this pass never removed stays reported; a read that
            # the interrupt cut short contributes whatever it reached.
            cancelled = OperationCancelledError()
            cancelled.stage_result = _recovered_result(
                connection,
                run_id=run_id,
                branch=branch,
                residuals=unfinished_stale_paths(pending_stale_paths, cleaned_sets),
            )
            raise cancelled from None
        return _result(
            run_id=run_id,
            branch=branch,
            findings=findings,
            statuses=statuses,
            blocked=blocked,
            residuals=residual_paths,
        )


__all__ = [
    "Stage11Result",
    "require_consistent_bullets",
    "require_current_anchor",
    "run_bullet_verification",
]
