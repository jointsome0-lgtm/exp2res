"""§13.7 single-pass assessment verification."""

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
    AssessmentSnapshot,
    CounterevidenceItem,
    EvidenceItem,
    ExperienceFact,
    RawLog,
    SelfClaim,
    SelfSignal,
    VerificationFinding,
)
from exp2res.errors import (
    IntegrityFailureError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
)
from exp2res.exports.managed import assessment_set_paths, remove_assessment_sets
from exp2res.llm.assessment_verifier import (
    ASSESSMENT_VERIFIER_CONTRACT,
    AssessmentVerifierInput,
    AssessmentVerifierOutput,
)
from exp2res.llm.contracts import (
    ContractValidationError,
    validation_diagnostics,
)
from exp2res.llm.registry import LLMSelection
from exp2res.llm.runner import CallBudgets, ContractRunner
from exp2res.services.capture import new_id
from exp2res.storage.repository import (
    get_assessment_snapshot,
    get_raw_log,
    insert_verification_finding,
    list_experience_facts,
    list_self_claims_for_snapshot,
    list_self_signals,
    list_verification_findings,
    update_assessment_snapshot_verification,
    update_self_claim_verification,
)
from exp2res.storage.workspace import (
    DEFAULT_BUSY_TIMEOUT_MS,
    report_managed_residuals,
    withdraw_managed_residuals,
    writer_database,
)

from .evidence_context import project_evidence_context
from .orchestration import PlannedCall, run_complete_stage
from .view_selection import select_assessment_view


_AGGREGATE_PRECEDENCE = (
    "rejected",
    "unsupported",
    "contradicted",
    "needs_clarification",
    "partially_supported",
    "inferred_but_acceptable",
    "supported",
)


@dataclass(frozen=True)
class Stage7Result:
    run_id: str
    snapshot_id: str
    snapshot_status: VerificationStatus
    findings: tuple[VerificationFinding, ...]
    claim_statuses: tuple[tuple[str, VerificationStatus], ...]
    residual_paths: tuple[str, ...]


@dataclass(frozen=True)
class _ClaimBundle:
    input_payload: AssessmentVerifierInput
    bundle_refs: frozenset[tuple[str, str]]
    has_direct_chain: bool


@dataclass(frozen=True)
class _ResolvedVerification:
    claim_id: str
    finding: VerificationFinding
    bundle_refs: frozenset[tuple[str, str]]


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _aggregate(statuses: Iterable[VerificationStatus]) -> VerificationStatus:
    values = set(statuses)
    if not values:
        raise IntegrityFailureError("snapshot_claim_set_empty")
    if "unverified" in values:
        return "unverified"
    for status in _AGGREGATE_PRECEDENCE:
        if status in values:
            return cast(VerificationStatus, status)
    raise IntegrityFailureError("snapshot_status_invalid")


def _require_current_members(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
) -> tuple[SelfClaim, ...]:
    rows = connection.execute(
        "SELECT superseded_at FROM self_claims WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    if not rows:
        raise IntegrityFailureError("snapshot_claim_set_empty")
    if any(row["superseded_at"] is not None for row in rows):
        raise IntegrityFailureError("snapshot_claim_not_current")
    claims = list_self_claims_for_snapshot(connection, snapshot_id)
    if not claims:
        raise IntegrityFailureError("snapshot_claim_set_empty")
    return claims


def _check_snapshot_integrity(
    connection: sqlite3.Connection,
    *,
    snapshot: AssessmentSnapshot,
    claims: Sequence[SelfClaim],
) -> None:
    summaries = [item for item in claims if item.claim_kind == "narrative_summary"]
    if len(summaries) != 1 or summaries[0].claim != snapshot.summary:
        raise IntegrityFailureError("snapshot_narrative_gate_failed")

    current_contradictions = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM contradictions WHERE superseded_at IS NULL"
        )
    }
    if set(snapshot.contradiction_ids) != current_contradictions:
        raise IntegrityFailureError("snapshot_contradiction_set_stale")
    for gap_id in snapshot.gap_question_ids:
        row = connection.execute(
            "SELECT superseded_at FROM gap_questions WHERE id = ?", (gap_id,)
        ).fetchone()
        if row is None or row["superseded_at"] is not None:
            raise IntegrityFailureError("snapshot_gap_reference_invalid")


def _has_direct_chain(
    connection: sqlite3.Connection, facts: Sequence[ExperienceFact]
) -> bool:
    if not facts:
        return False
    placeholders = ",".join("?" for _ in facts)
    row = connection.execute(
        f"""
        SELECT 1
        FROM fact_sources AS source
        JOIN experience_facts AS fact ON fact.id = source.fact_id
        JOIN evidence_items AS item ON item.id = source.evidence_item_id
        JOIN raw_logs AS owner ON owner.id = item.raw_log_id
        WHERE source.fact_id IN ({placeholders})
          AND fact.superseded_at IS NULL
          AND source.support_type = 'direct'
        LIMIT 1
        """,
        tuple(fact.id for fact in facts),
    ).fetchone()
    return row is not None


def _build_bundle(
    connection: sqlite3.Connection,
    *,
    claim: SelfClaim,
    snapshot: AssessmentSnapshot,
    current_facts: dict[str, ExperienceFact],
    current_signals: dict[str, SelfSignal],
    scope_facts: tuple[ExperienceFact, ...],
    scope_signals: tuple[SelfSignal, ...],
) -> _ClaimBundle:
    try:
        source_signals = tuple(
            sorted(
                (current_signals[item] for item in claim.source_signal_ids),
                key=lambda item: _id_key(item.id),
            )
        )
    except KeyError as error:
        raise IntegrityFailureError("assessment_bundle_signal_invalid") from error

    fact_ids = {
        *claim.source_fact_ids,
        *(
            fact_id
            for signal in source_signals
            for fact_id in (*signal.supporting_fact_ids, *signal.counter_fact_ids)
        ),
    }
    try:
        source_facts = tuple(
            sorted(
                (current_facts[item] for item in fact_ids),
                key=lambda item: _id_key(item.id),
            )
        )
    except KeyError as error:
        raise IntegrityFailureError("assessment_bundle_fact_invalid") from error

    source_evidence = project_evidence_context(
        connection,
        source_facts,
        missing_diagnostic="assessment_bundle_evidence_invalid",
    )
    non_displaced_log_ids = {
        item.raw_log_id for item in source_evidence if isinstance(item, EvidenceItem)
    }
    source_logs_list: list[RawLog] = []
    for log_id in sorted(non_displaced_log_ids, key=_id_key):
        raw_log = get_raw_log(connection, log_id)
        if raw_log is None:
            raise IntegrityFailureError("assessment_bundle_log_invalid")
        source_logs_list.append(raw_log)
    source_logs = tuple(source_logs_list)

    input_payload = AssessmentVerifierInput(
        self_claim=claim,
        scope=snapshot.scope,
        scope_target=snapshot.scope_target,
        source_signals=list(source_signals),
        scope_signals=list(scope_signals),
        scope_facts=list(scope_facts),
        source_facts=list(source_facts),
        source_evidence_items=list(source_evidence),
        source_logs=list(source_logs),
    )
    bundle_refs = frozenset(
        {
            *(("self_signal", item.id) for item in source_signals),
            *(("self_signal", item.id) for item in scope_signals),
            *(("experience_fact", item.id) for item in source_facts),
            *(("experience_fact", item.id) for item in scope_facts),
            *(("evidence_item", item.id) for item in source_evidence),
            *(("raw_log", item.id) for item in source_logs),
        }
    )
    return _ClaimBundle(
        input_payload=input_payload,
        bundle_refs=bundle_refs,
        has_direct_chain=_has_direct_chain(connection, source_facts),
    )


def _enrich_for(
    bundle: _ClaimBundle,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def enrich(decoded: dict[str, Any]) -> dict[str, Any]:
        try:
            output = AssessmentVerifierOutput.model_validate_json(
                json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
            )
        except ValidationError as error:
            raise ContractValidationError(
                validation_diagnostics(ASSESSMENT_VERIFIER_CONTRACT, error.errors())
            ) from None

        errors: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(output.counterevidence):
            pair = (item.source_ref_type, item.source_ref_id)
            if pair in seen:
                errors.append(
                    {"loc": ("counterevidence", index), "type": "duplicate_reference"}
                )
            elif pair not in bundle.bundle_refs:
                errors.append(
                    {
                        "loc": ("counterevidence", index, "source_ref_id"),
                        "type": "out_of_context_target",
                    }
                )
            seen.add(pair)
        # §16.1 + §13.7 check 8 forbid a passing or presentable verdict
        # without a direct chain. §16.11 makes rejected the rule-violation
        # verdict; a valid negative pass still persists its finding.
        if not bundle.has_direct_chain and output.status not in {
            "rejected",
            "unsupported",
        }:
            errors.append({"loc": ("status",), "type": "direct_chain_required"})
        if errors:
            raise ContractValidationError(
                validation_diagnostics(ASSESSMENT_VERIFIER_CONTRACT, errors)
            )
        return decoded

    return enrich


def _resolve_for(
    *,
    claim_id: str,
    run_id: str,
    bundle_refs: frozenset[tuple[str, str]],
    id_factory: Callable[[str], str],
    clock: Callable[[], datetime],
) -> Callable[[BaseModel], object]:
    def resolve(validated: BaseModel) -> object:
        output = cast(AssessmentVerifierOutput, validated)
        finding = VerificationFinding(
            id=id_factory("finding"),
            created_at=clock(),
            produced_by_run_id=run_id,
            target_type="self_claim",
            target_id=claim_id,
            status=output.status,
            reason=output.reason,
            unsupported_phrases=output.unsupported_phrases,
            suggested_rewrite=output.suggested_rewrite,
            counterevidence=[
                CounterevidenceItem(**item.model_dump())
                for item in output.counterevidence
            ],
        )
        return _ResolvedVerification(claim_id, finding, bundle_refs)

    return resolve


def run_assessment_verification(
    workspace: Path,
    *,
    snapshot_id: str,
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
) -> Stage7Result:
    """Verify every member claim, then commit one complete verifier state."""

    now = clock or (lambda: datetime.now(timezone.utc))
    with writer_database(
        workspace, timeout_ms=timeout_ms, reconcile=True
    ) as connection:
        snapshot = get_assessment_snapshot(
            connection, snapshot_id, current_only=False
        )
        if snapshot is None:
            raise SelectorNotFoundError()
        if snapshot.superseded_at is not None:
            raise SnapshotNotCurrentError()

        claims = _require_current_members(connection, snapshot_id=snapshot_id)
        prior_verification_state = (
            snapshot.verification_status,
            tuple(
                (claim.id, claim.verification_status, claim.counterevidence)
                for claim in claims
            ),
        )
        _check_snapshot_integrity(connection, snapshot=snapshot, claims=claims)

        view = select_assessment_view(
            connection, scope=snapshot.scope, scope_target=snapshot.scope_target
        )
        scope_facts = tuple(
            sorted(
                {item.id: item for item in (*view.facts, *view.context_facts)}.values(),
                key=lambda item: _id_key(item.id),
            )
        )
        scope_signals = tuple(sorted(view.signals, key=lambda item: _id_key(item.id)))
        current_facts = {item.id: item for item in list_experience_facts(connection)}
        current_signals = {item.id: item for item in list_self_signals(connection)}
        bundles = tuple(
            _build_bundle(
                connection,
                claim=claim,
                snapshot=snapshot,
                current_facts=current_facts,
                current_signals=current_signals,
                scope_facts=scope_facts,
                scope_signals=scope_signals,
            )
            for claim in claims
        )

        run_id = id_factory("run")
        planned = tuple(
            PlannedCall(
                input_payload=bundle.input_payload,
                input_ids=tuple(
                    sorted(
                        {
                            claim.id,
                            *(item[1] for item in bundle.bundle_refs),
                        },
                        key=_id_key,
                    )
                ),
                enrich=_enrich_for(bundle),
                resolve=_resolve_for(
                    claim_id=claim.id,
                    run_id=run_id,
                    bundle_refs=bundle.bundle_refs,
                    id_factory=id_factory,
                    clock=now,
                ),
            )
            for claim, bundle in zip(claims, bundles, strict=True)
        )
        snapshot_status = snapshot.verification_status

        def commit(
            held: sqlite3.Connection, resolved: Sequence[object]
        ) -> Iterable[str]:
            nonlocal snapshot_status, pending_stale_paths
            candidates = tuple(cast(_ResolvedVerification, item) for item in resolved)
            if tuple(item.claim_id for item in candidates) != tuple(
                item.id for item in claims
            ):
                raise IntegrityFailureError("verification_claim_set_mismatch")
            for candidate in candidates:
                update_self_claim_verification(
                    held,
                    claim_id=candidate.claim_id,
                    verification_status=candidate.finding.status,
                    counterevidence=candidate.finding.counterevidence,
                )
                insert_verification_finding(
                    held,
                    candidate.finding,
                    bundle_refs=candidate.bundle_refs,
                )
            snapshot_status = _aggregate(
                candidate.finding.status for candidate in candidates
            )
            update_assessment_snapshot_verification(
                held,
                snapshot_id=snapshot_id,
                verification_status=snapshot_status,
            )
            fresh_claims = list_self_claims_for_snapshot(held, snapshot_id)
            fresh_aggregate = _aggregate(
                item.verification_status for item in fresh_claims
            )
            stored = get_assessment_snapshot(held, snapshot_id)
            if stored is None or stored.verification_status != fresh_aggregate:
                raise IntegrityFailureError("snapshot_aggregate_mismatch")
            fresh_state = (
                fresh_aggregate,
                tuple(
                    (item.id, item.verification_status, item.counterevidence)
                    for item in fresh_claims
                ),
            )
            if fresh_state != prior_verification_state:
                # Pre-commit pending report (same pattern as Stages 3-6): an
                # interrupt in the commit-to-cleanup window still reports the
                # now-stale published set; rollback withdraws it below.
                pending_stale_paths = assessment_set_paths(
                    workspace, (snapshot_id,)
                )
                report_managed_residuals(pending_stale_paths)
            return tuple(candidate.finding.id for candidate in candidates)

        pending_stale_paths: tuple[str, ...] = ()
        try:
            run_complete_stage(
            workspace,
            connection,
            stage="13.7",
            contract=ASSESSMENT_VERIFIER_CONTRACT,
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
            withdraw_managed_residuals(pending_stale_paths)
            raise
        findings = tuple(
            sorted(
                list_verification_findings(connection, run_id=run_id),
                key=lambda item: _id_key(item.id),
            )
        )
        current_claims = list_self_claims_for_snapshot(connection, snapshot_id)
        current_snapshot = get_assessment_snapshot(connection, snapshot_id)
        if current_snapshot is None:
            raise IntegrityFailureError("snapshot_missing_after_verification")
        current_verification_state = (
            current_snapshot.verification_status,
            tuple(
                (claim.id, claim.verification_status, claim.counterevidence)
                for claim in current_claims
            ),
        )
        # §13.7 stale-export trigger: only a committed verification-field
        # change invalidates this snapshot's ID-keyed set. Finding history by
        # itself does not change the renderer state.
        if current_verification_state != prior_verification_state:
            residual_paths = remove_assessment_sets(workspace, (snapshot_id,))
        else:
            residual_paths = ()

    return Stage7Result(
        run_id=run_id,
        snapshot_id=snapshot_id,
        snapshot_status=current_snapshot.verification_status,
        findings=findings,
        claim_statuses=tuple(
            (item.id, item.verification_status) for item in current_claims
        ),
        residual_paths=residual_paths,
    )
