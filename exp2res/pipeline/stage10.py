"""§13.10 relevance-aware bullet generation for one verified bullet pack."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Pattern, Sequence, cast
import unicodedata

from pydantic import BaseModel, ValidationError

from exp2res.domain.enums import ResumeTargetSection
from exp2res.domain.models import (
    AssessmentSnapshot,
    ExperienceFact,
    ResumeBranch,
    ResumeBullet,
    SelfClaim,
    validate_structural,
)
from exp2res.domain.results import InvalidatedBranch
from exp2res.domain.verification import aggregate_verification_status
from exp2res.errors import (
    AnchorNotEligibleError,
    BranchNameInvalidError,
    IntegrityFailureError,
    LLMCancelledError,
    OperationCancelledError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
)
from exp2res.exports.managed import branch_set_paths, remove_branch_sets
from exp2res.llm.contracts import (
    ContractValidationError,
    ContractWarning,
    validation_diagnostics,
)
from exp2res.llm.fact_extractor import DisplacedSupportDescriptor
from exp2res.llm.registry import LLMSelection
from exp2res.llm.resume_writer import (
    RESUME_WRITER_CONTRACT,
    BranchContext,
    FactEvidence,
    JobDescriptionContext,
    ResumeBulletCandidate,
    ResumeWriterInput,
    ResumeWriterOutput,
    SelectedFact,
)
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    STAGE10_ANCHOR_ALLOWLIST,
    bullet_log_closure,
    current_branch_name_conflict,
    get_assessment_snapshot,
    get_experience_fact,
    get_job_description,
    get_raw_log,
    get_resume_branch,
    insert_resume_branch,
    insert_resume_bullet,
    list_experience_facts,
    list_resume_bullets_for_branch,
    list_self_claims_for_snapshot,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    report_managed_residuals,
    writer_database,
)

from .branch_lifecycle import BranchSupersession, supersede_branches
from .evidence_context import project_evidence_context
from .orchestration import (
    PlannedCall,
    run_complete_stage,
    unfinished_stale_paths,
    withdraw_pending_unless_superseded,
)


@dataclass(frozen=True)
class Stage10Result:
    run_id: str
    branch_name: str
    branch_id: str | None
    bullet_ids: tuple[str, ...]
    superseded_branch_ids: tuple[str, ...]
    superseded_bullet_ids: tuple[str, ...]
    generation_id: str | None
    superseded_generation_ids: tuple[str, ...]
    invalidated_branches: tuple[InvalidatedBranch, ...]
    residual_paths: tuple[str, ...]
    warnings: tuple[ContractWarning, ...]
    branch: ResumeBranch | None
    bullets: tuple[ResumeBullet, ...]


@dataclass(frozen=True)
class _ResolvedPack:
    candidates: tuple[ResumeBulletCandidate, ...]
    warnings: tuple[ContractWarning, ...]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _selected_facts(
    connection: sqlite3.Connection, facts: Sequence[ExperienceFact]
) -> tuple[SelectedFact, ...]:
    """Pair every current fact with its complete §13.3 rule 10 evidence."""

    context = {
        item.id: item
        for item in project_evidence_context(
            connection, facts, missing_diagnostic="bullet_evidence_missing"
        )
    }
    selected: list[SelectedFact] = []
    for fact in sorted(facts, key=lambda item: _id_key(item.id)):
        evidence: list[FactEvidence] = []
        for item_id in sorted(fact.evidence_item_ids, key=_id_key):
            item = context[item_id]
            if isinstance(item, DisplacedSupportDescriptor):
                # §13.3 rule 10: the descriptor stands in for a displaced
                # record precisely so its prose never reaches the writer.
                evidence.append(FactEvidence(evidence_item=item, raw_log=None))
                continue
            raw_log = get_raw_log(connection, item.raw_log_id)
            if raw_log is None:
                raise IntegrityFailureError("bullet_source_log_missing")
            evidence.append(FactEvidence(evidence_item=item, raw_log=raw_log))
        selected.append(SelectedFact(fact=fact, evidence=evidence))
    return tuple(selected)


def _enrich_for(
    input_payload: ResumeWriterInput,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Reject every out-of-context reference before a candidate is resolved."""

    fact_ids = {item.fact.id for item in input_payload.selected_facts}
    claim_ids = {claim.id for claim in input_payload.supported_self_claims}
    requirement_ids = {
        requirement.id
        for requirement in input_payload.job_description.parsed.requirements
    }

    def enrich(decoded: dict[str, Any]) -> dict[str, Any]:
        try:
            output = ResumeWriterOutput.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
            )
        except ValidationError as error:
            raise ContractValidationError(
                validation_diagnostics(RESUME_WRITER_CONTRACT, error.errors())
            ) from None

        errors: list[dict[str, object]] = []
        for index, candidate in enumerate(output.bullets):
            for field, allowed in (
                ("source_fact_ids", fact_ids),
                ("source_self_claim_ids", claim_ids),
                ("matched_jd_requirements", requirement_ids),
            ):
                for member_index, value in enumerate(getattr(candidate, field)):
                    if value not in allowed:
                        errors.append(
                            {
                                "loc": ("bullets", index, field, member_index),
                                "type": "out_of_context_target",
                            }
                        )
        if errors:
            raise ContractValidationError(
                validation_diagnostics(RESUME_WRITER_CONTRACT, errors)
            )
        return decoded

    return enrich


def _resolve(validated: BaseModel) -> object:
    output = cast(ResumeWriterOutput, validated)
    return _ResolvedPack(tuple(output.bullets), tuple(output.warnings))


_SECTION_ORDER = {
    section: index
    for index, section in enumerate(ResumeTargetSection.__args__)  # type: ignore[attr-defined]
}


def select_persisted_batch(
    candidates: Sequence[ResumeBulletCandidate], requirement_ids: Sequence[str]
) -> tuple[ResumeBulletCandidate, ...]:
    """Order the complete batch and drop every later exact duplicate (§13.10).

    The key is section declaration order, then the earliest position of any
    matched requirement in the supplied job description, then the validated
    text bytes, and finally the candidate's position in the response — a
    tie-break reachable only between exact duplicates, which is why no
    allocated ID can participate in ordering or retention.
    """

    position = {value: index for index, value in enumerate(requirement_ids)}
    unmatched = len(requirement_ids)

    def sort_key(item: tuple[int, ResumeBulletCandidate]):
        index, candidate = item
        matched = [
            position[value]
            for value in candidate.matched_jd_requirements
            if value in position
        ]
        return (
            _SECTION_ORDER[candidate.target_section],
            min(matched) if matched else unmatched,
            candidate.text.encode("utf-8"),
            index,
        )

    retained: list[ResumeBulletCandidate] = []
    seen: set[str] = set()
    for _index, candidate in sorted(enumerate(candidates), key=sort_key):
        if candidate.text in seen:
            continue
        seen.add(candidate.text)
        retained.append(candidate)
    return tuple(retained)


def _projected_text(value: str) -> str:
    """Stage 12's mandatory generated-voice LF-newline and NFC projection."""

    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def validated_branch_name(value: str) -> str:
    """§14.10's non-blank, §11-hygienic `--branch` value, checked at entry.

    The same rule guards `BranchContext` and `ResumeBranch`, but those raise
    deep inside the stage, where a `ValidationError` would surface as §14.14's
    class-1 internal error instead of the class-2 result ordinary bad input
    owes the caller.
    """

    try:
        validate_structural(value)
    except ValueError as error:
        raise BranchNameInvalidError() from error
    if not value.strip():
        raise BranchNameInvalidError()
    return value


def _require_current_claim_facts(
    connection: sqlite3.Connection,
    claims: Sequence[SelfClaim],
    facts: Sequence[ExperienceFact],
) -> None:
    """Refuse a supplied claim whose §16.1 provenance chain is not current.

    A claim reaches the writer as guidance and can end up cited by a persisted
    bullet, so Stage 10 owes the same check Stage 7 and assessment export make
    before they use one: every `source_fact_ids` member resolves to a current
    fact. Only damaged state can fail it, which is exactly why it fails closed
    rather than reaching a provider.
    """

    current = {fact.id for fact in facts}
    for claim in claims:
        if not claim.source_fact_ids:
            # Storage refuses a chainless claim (`claim_source_facts_empty`),
            # so only damaged state has one — and the closure is the whole
            # provenance such a claim would lack.
            raise IntegrityFailureError("claim_source_facts_empty")
        for fact_id in sorted(set(claim.source_fact_ids) - current, key=_id_key):
            stored = get_experience_fact(connection, fact_id)
            raise IntegrityFailureError(
                "claim_fact_missing" if stored is None else "claim_fact_superseded"
            )


_SNAPSHOT_REFERENCES = (
    ("gap_question_ids", "gap_questions", "snapshot_gap_reference_invalid"),
    (
        "contradiction_ids",
        "contradictions",
        "snapshot_contradiction_reference_invalid",
    ),
)


def _require_current_snapshot_references(
    connection: sqlite3.Connection, snapshot: AssessmentSnapshot
) -> None:
    """§11.7: a read-time consumer revalidates the snapshot's typed references.

    Resolvable and current — and nothing beyond that. §11's typed-ID
    validation already makes a hydrated snapshot's reference lists
    duplicate-free, a gap answered after synthesis stays valid because §11.7
    says so, and the set-equality rules Stage 7 and assessment export layer on
    top of this are their own freshness gates rather than reference integrity.
    """

    for field, table, code in _SNAPSHOT_REFERENCES:
        for reference in getattr(snapshot, field):
            row = connection.execute(
                f"SELECT superseded_at FROM {table} WHERE id = ?", (reference,)
            ).fetchone()
            if row is None or row["superseded_at"] is not None:
                raise IntegrityFailureError(code)


def _run_is_committed(connection: sqlite3.Connection, run_id: str) -> bool:
    """Prove the stage's single final transaction reached durable storage.

    The run's completed transition commits with the business swap, so a
    `completed` row is exactly the durability the interrupt could not undo.
    """

    try:
        row = connection.execute(
            "SELECT status FROM processing_runs WHERE id = ?", (run_id,)
        ).fetchone()
    except Exception:
        return False
    return bool(row) and row[0] == "completed"


def run_bullet_generation(
    workspace: Path,
    *,
    job_description_id: str,
    snapshot_id: str,
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
) -> Stage10Result:
    """Run the one whole-pack writer call and atomically swap its branch."""

    now = clock or (lambda: datetime.now(timezone.utc))
    branch_name = validated_branch_name(branch_name)
    # The writer teardown — the final commit, the connection close, and the
    # §8.1 lock release — runs after the guarded block below and can itself
    # be interrupted over a durable swap, so the completed result stays
    # recoverable until this function has actually returned it.
    completed: Stage10Result | None = None
    try:
        with writer_database(
            workspace, timeout_ms=timeout_ms, reconcile=True
        ) as connection:
            job_description = get_job_description(connection, job_description_id)
            if job_description is None:
                raise SelectorNotFoundError()
            snapshot = get_assessment_snapshot(
                connection, snapshot_id, current_only=False
            )
            if snapshot is None:
                raise SelectorNotFoundError()
            if snapshot.superseded_at is not None:
                raise SnapshotNotCurrentError()
            _require_current_snapshot_references(connection, snapshot)
            members = list_self_claims_for_snapshot(connection, snapshot.id)
            if snapshot.verification_status != aggregate_verification_status(
                item.verification_status for item in members
            ):
                # §16.11: every gated consumer re-reduces the aggregate rather than
                # trusting the stored one. Broken state precedes the eligibility
                # verdict, because a status that no longer reduces from its own
                # claims is not a status an ordinary class-2 refusal may report.
                raise IntegrityFailureError("snapshot_aggregate_mismatch")
            if snapshot.verification_status not in STAGE10_ANCHOR_ALLOWLIST:
                raise AnchorNotEligibleError()

            facts = list_experience_facts(connection)
            selected_facts = _selected_facts(connection, facts)
            supported = tuple(
                sorted(
                    (
                        item
                        for item in members
                        if item.verification_status == "supported"
                    ),
                    key=lambda item: _id_key(item.id),
                )
            )
            _require_current_claim_facts(connection, supported, facts)
            input_payload = ResumeWriterInput(
                branch=BranchContext(
                    name=branch_name,
                    job_description_id=job_description.id,
                    assessment_snapshot_id=snapshot.id,
                    assessment_scope=snapshot.scope,
                ),
                job_description=JobDescriptionContext(
                    id=job_description.id,
                    title=job_description.title,
                    company=job_description.company,
                    parsed=job_description.parsed,
                ),
                selected_facts=list(selected_facts),
                supported_self_claims=list(supported),
            )
            run_id = id_factory("run")
            planned = (
                PlannedCall(
                    input_payload=input_payload,
                    # §12.13 telemetry names every transited entity, and this
                    # payload carries each fact's evidence items and their raw
                    # logs beside the parsed vacancy's typed requirements.
                    input_ids=tuple(
                        sorted(
                            {
                                job_description.id,
                                snapshot.id,
                                *(
                                    requirement.id
                                    for requirement in (
                                        job_description.parsed.requirements
                                    )
                                ),
                                *(item.fact.id for item in selected_facts),
                                *(
                                    evidence.evidence_item.id
                                    for item in selected_facts
                                    for evidence in item.evidence
                                ),
                                *(
                                    evidence.raw_log.id
                                    for item in selected_facts
                                    for evidence in item.evidence
                                    if evidence.raw_log is not None
                                ),
                                *(claim.id for claim in supported),
                            },
                            key=_id_key,
                        )
                    ),
                    enrich=_enrich_for(input_payload),
                    resolve=_resolve,
                ),
            )

            branch_id: str | None = None
            bullet_ids: tuple[str, ...] = ()
            generation_id: str | None = None
            superseded_generation_ids: set[str] = set()
            replaced = BranchSupersession()
            pending_stale_paths: tuple[str, ...] = ()
            pack_warnings: tuple[ContractWarning, ...] = ()

            def commit(
                held: sqlite3.Connection, resolved: Sequence[object]
            ) -> Iterable[str]:
                nonlocal branch_id, bullet_ids, generation_id, replaced
                nonlocal pending_stale_paths, pack_warnings
                pack = cast(_ResolvedPack, resolved[0])
                pack_warnings = pack.warnings
                requirement_ids = [
                    requirement.id
                    for requirement in job_description.parsed.requirements
                ]
                retained = select_persisted_batch(pack.candidates, requirement_ids)
                projected: dict[str, str] = {}
                for candidate in retained:
                    projection = _projected_text(candidate.text)
                    if projection in projected:
                        # §13.10: two distinct retained texts that collapse under
                        # Stage 12's projection fail the complete batch closed
                        # rather than render ambiguously.
                        raise IntegrityFailureError("bullet_projection_collision")
                    projected[projection] = candidate.text

                swap_time = now()
                if retained:
                    # §15.6's valid empty array is a semantic no-bullet result with
                    # no branch, no bullet, and no partial commit, so the current
                    # branch is replaced only by a batch that actually persists.
                    conflict = current_branch_name_conflict(held, branch_name)
                    if conflict is not None:
                        # §13.10/§14.10: generating a name that folds equal to a
                        # current branch's supersedes exactly that branch, so at
                        # most one generation of the named branch is ever current.
                        replaced = supersede_branches(
                            held, (conflict.id,), superseded_at=swap_time
                        )
                        superseded_generation_ids.update(
                            replaced.superseded_generation_ids
                        )

                    new_branch_id = id_factory("branch")
                    generation_id = id_factory("gen")
                    insert_resume_branch(
                        held,
                        ResumeBranch(
                            id=new_branch_id,
                            name=branch_name,
                            assessment_snapshot_id=snapshot.id,
                            job_description_id=job_description.id,
                            created_at=swap_time,
                            superseded_at=None,
                            metadata={},
                        ),
                        produced_by_run_id=run_id,
                        generation_id=generation_id,
                    )
                    created: list[str] = []
                    for candidate in retained:
                        bullet = ResumeBullet(
                            id=id_factory("bullet"),
                            created_at=swap_time,
                            superseded_at=None,
                            branch_id=new_branch_id,
                            text=candidate.text,
                            target_section=candidate.target_section,
                            target_role_relevance=candidate.target_role_relevance,
                            matched_jd_requirements=list(
                                candidate.matched_jd_requirements
                            ),
                            source_fact_ids=list(candidate.source_fact_ids),
                            source_log_ids=list(
                                bullet_log_closure(held, candidate.source_fact_ids)
                            ),
                            source_self_claim_ids=list(candidate.source_self_claim_ids),
                            verification_status="unverified",
                        )
                        insert_resume_bullet(
                            held,
                            bullet,
                            produced_by_run_id=run_id,
                            generation_id=generation_id,
                        )
                        created.append(bullet.id)
                    branch_id = new_branch_id
                    bullet_ids = tuple(created)

                # Pre-commit pending report: an interrupt anywhere in the
                # commit-to-cleanup window still reports the replaced branch's
                # retained managed set (§13 stale-export invalidation rule).
                pending_stale_paths = branch_set_paths(workspace, replaced.branch_ids)
                report_managed_residuals(pending_stale_paths)
                created_ids = () if branch_id is None else (branch_id, *bullet_ids)
                return created_ids

            def build_result(
                residuals: tuple[str, ...],
                branch: ResumeBranch | None,
                bullets: tuple[ResumeBullet, ...],
            ) -> Stage10Result:
                return Stage10Result(
                    run_id=run_id,
                    branch_name=branch_name,
                    branch_id=branch_id,
                    bullet_ids=bullet_ids,
                    superseded_branch_ids=replaced.branch_ids,
                    superseded_bullet_ids=replaced.bullet_ids,
                    generation_id=generation_id,
                    superseded_generation_ids=tuple(
                        sorted(superseded_generation_ids, key=_id_key)
                    ),
                    invalidated_branches=replaced.invalidated_branches,
                    residual_paths=residuals,
                    warnings=pack_warnings,
                    branch=branch,
                    bullets=bullets,
                )

            def committed_pack() -> tuple[
                ResumeBranch | None, tuple[ResumeBullet, ...]
            ]:
                if branch_id is None:
                    return None, ()
                return (
                    get_resume_branch(connection, branch_id),
                    list_resume_bullets_for_branch(connection, branch_id),
                )

            # One guarded window from the business swap to the end of cleanup:
            # every interrupt past the durable commit — inside the transaction,
            # between it and the result read, in the read, or in the cleanup —
            # owes the caller the same §14.14 rule 6 report.
            branch: ResumeBranch | None = None
            bullets: tuple[ResumeBullet, ...] = ()
            cleaned_sets: list[str] = []
            returned = False
            try:
                run_complete_stage(
                    workspace,
                    connection,
                    stage="13.10",
                    contract=RESUME_WRITER_CONTRACT,
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
                returned = True
                # Read the committed rows before the interruptible cleanup below,
                # so the guard has a complete result to carry.
                branch, bullets = committed_pack()
                # §13 stale-export trigger class 1: the swap is already committed,
                # so cleanup failure or interruption never rolls it back.
                residual_paths = remove_branch_sets(
                    workspace, replaced.branch_ids, removed_ledger=cleaned_sets
                )
                # Built inside the guard so an interrupt during construction
                # still leaves the guard a committed result to carry.
                completed = build_result(residual_paths, branch, bullets)
            except BaseException as error:
                # Withdraw the pre-commit pending report only when the rollback is
                # proven; an interrupt after a durable commit keeps it reported.
                # The proof reads the table this stage supersedes.
                withdraw_pending_unless_superseded(
                    connection,
                    pending_stale_paths,
                    replaced.branch_ids,
                    table="resume_branches",
                )
                cancelling = isinstance(error, (KeyboardInterrupt, LLMCancelledError))
                if cancelling and (returned or _run_is_committed(connection, run_id)):
                    # §14.14 rule 6: the class-9 error carries the complete
                    # committed result, and only the sets this pass never reached
                    # stay reported.
                    if branch is None and branch_id is not None:
                        try:
                            branch, bullets = committed_pack()
                        except BaseException:
                            # A second interrupt inside the recovery read must not
                            # cost the caller the identifiers already in hand; the
                            # cancellation is honored by the raise below either way.
                            pass
                    cancelled = OperationCancelledError()
                    cancelled.stage_result = build_result(
                        unfinished_stale_paths(pending_stale_paths, cleaned_sets),
                        branch,
                        bullets,
                    )
                    raise cancelled from None
                raise
            return completed
    except KeyboardInterrupt:
        if completed is None:
            raise
        cancelled = OperationCancelledError()
        cancelled.stage_result = completed
        raise cancelled from None
