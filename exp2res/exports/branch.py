"""Shared branch-pack integrity predicate and the §13.12 export graph.

Stages 11 and 12 read the same stored graph for two different reasons — one
before spending a provider call, one before writing a managed set — and issue
#260 asked for exactly one predicate rather than a guard per finding. The
checks live here because both callers may import `exp2res.exports` while
nothing under `exp2res.exports` imports a pipeline stage.

Every check below is a stored-state invariant that Stage 10's write boundary
already established: reaching one means the persisted graph disagrees with
itself — restored, migrated, or damaged state — not that a model returned
something wrong.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import sqlite3
from typing import Literal, Sequence

from exp2res.domain.enums import ResumeTargetSection
from exp2res.domain.models import (
    AssessmentSnapshot,
    EvidenceItem,
    ExperienceFact,
    JobDescription,
    RawLog,
    ResumeBranch,
    ResumeBullet,
    SelfClaim,
)
from exp2res.errors import (
    BulletPackExportBlockedError,
    IntegrityFailureError,
    SelectorNotFoundError,
)
from exp2res.storage.repository import (
    BULLET_EXPORT_ALLOWLIST,
    STAGE10_ANCHOR_ALLOWLIST,
    bullet_log_closure,
    get_assessment_snapshot,
    get_experience_fact,
    get_job_description,
    hydrate_assessment_snapshot,
    hydrate_evidence_item,
    hydrate_raw_log,
    hydrate_resume_branch,
    hydrate_resume_bullet,
    hydrate_self_claim,
    list_resume_bullets_for_branch,
    list_self_claims_for_snapshot,
    validate_experience_fact_sources,
)

from .graph import (
    ClaimRenderEntry,
    EvidenceRenderEntry,
    FactRenderEntry,
    FactSourceRecord,
    FactSourceRenderEntry,
    RawLogRenderEntry,
    SnapshotRenderEntry,
    StoredRecord,
    _BundleModel,
    _merged_stored,
    _require_reference,
    _stored,
    assessment_integrity_failure,
    id_key,
    load_snapshot_claims,
    load_supplemental_closure,
)
from .markdown import normalize_generated_text


_SECTION_ORDER = {
    section: index
    for index, section in enumerate(ResumeTargetSection.__args__)  # type: ignore[attr-defined]
}


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


def current_branch_bullets(
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


def require_current_anchor(
    connection: sqlite3.Connection, branch: ResumeBranch
) -> None:
    """Fail closed on a branch whose §18 assessment anchor no longer resolves.

    `validate_branch_production` establishes a present, current anchor at
    insert and §13.13 rule 4 supersedes the branch with its snapshot, so a
    current branch pointing at a missing or superseded one is restored,
    migrated, or damaged state. The verifier bundle alone would not see it: a
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
    if get_assessment_snapshot(connection, branch.assessment_snapshot_id) is None:
        raise IntegrityFailureError("branch_snapshot_superseded")


def require_consistent_bullets(
    connection: sqlite3.Connection,
    bullets: Sequence[ResumeBullet],
    job_description: JobDescription,
) -> None:
    """Fail closed on a bullet whose own typed references disagree (§15.7).

    Both relations are §12 rule 10 invariants that `insert_resume_bullet`
    establishes and no current-row update may change, so reaching either
    failure means the stored graph disagrees with itself. Checking them here
    keeps that state from being charged to a provider and from receiving a
    semantic verdict §18 would then have to refuse at export.
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
        # withheld from the verifier bundle.
        #
        # Equality is on the set, not the list order: `insert_resume_bullet`
        # compares a sorted copy and stores the caller's own order, so a bullet
        # that passed the write boundary may legitimately hold the closure
        # unsorted. Comparing positionally would fail a valid pack.
        closure = bullet_log_closure(connection, bullet.source_fact_ids)
        if tuple(sorted(bullet.source_log_ids, key=id_key)) != closure:
            raise IntegrityFailureError("bullet_log_closure_mismatch")


def require_direct_retained_chain(
    connection: sqlite3.Connection,
    fact_ids: Sequence[str],
    *,
    diagnostic: str,
) -> None:
    """§16.1: a row entering verification or export reaches one live chain.

    Hydration already refuses a fact with no `direct` `fact_sources` row, and
    `require_consistent_bullets` already pins the log closure. Neither reaches
    this: a fact's direct evidence may sit on a record a correction displaced,
    leaving the chain complete in identity but with no retained `RawLog` at its
    end — which is the one thing §16.1 names as the chain's last link.
    """

    for fact_id in fact_ids:
        reached = connection.execute(
            """
            SELECT 1
            FROM fact_sources AS source
            JOIN evidence_items AS item ON item.id = source.evidence_item_id
            JOIN raw_logs AS log ON log.id = item.raw_log_id
            WHERE source.fact_id = ?
              AND source.support_type = 'direct'
              AND NOT EXISTS (
                SELECT 1 FROM raw_logs AS correction
                WHERE correction.corrects_log_id = log.id
              )
            LIMIT 1
            """,
            (fact_id,),
        ).fetchone()
        if reached is not None:
            return
    raise IntegrityFailureError(diagnostic)


def load_branch_pack(
    connection: sqlite3.Connection, branch: ResumeBranch
) -> tuple[tuple[ResumeBullet, ...], JobDescription]:
    """Run the one shared pre-transport predicate over a selected branch.

    Both consumers reach this with a current branch already resolved and need
    the same three things established before doing anything expensive: the
    bullet set is one complete current batch, the vacancy the branch names is
    still there, and every bullet's own typed references agree with the stored
    graph. Stage 11 stops here; Stage 12 continues into the export graph below.
    """

    bullets = current_branch_bullets(connection, branch.id)
    # §13.12/§18: both consumers recover the vacancy through the branch's
    # persisted association, and a missing one fails verification and export.
    job_description = get_job_description(connection, branch.job_description_id)
    if job_description is None:
        raise IntegrityFailureError("branch_job_description_missing")
    require_current_anchor(connection, branch)
    require_consistent_bullets(connection, bullets, job_description)
    return bullets, job_description


def render_order(
    bullets: Sequence[ResumeBullet], requirement_ids: Sequence[str]
) -> tuple[ResumeBullet, ...]:
    """Recompute §13.10's persisted-state ordering key for rendering.

    §13.10 rule 56 makes this Stage 12's job: allocated IDs carry a random
    component and no persisted order column exists, so the render order is
    recovered from the same (section, earliest matched requirement, text bytes)
    key Stage 10 sorted by. Rule 53 dropped every later exact duplicate, so
    retained texts are unique and that key is total — the response-position
    tie-break Stage 10 needed is unreachable from persisted state.
    """

    position = {value: index for index, value in enumerate(requirement_ids)}
    unmatched = len(requirement_ids)

    def sort_key(bullet: ResumeBullet):
        matched = [
            position[value]
            for value in bullet.matched_jd_requirements
            if value in position
        ]
        return (
            _SECTION_ORDER[bullet.target_section],
            min(matched) if matched else unmatched,
            bullet.text.encode("utf-8"),
        )

    ordered = sorted(bullets, key=sort_key)
    texts = [bullet.text for bullet in ordered]
    if len(texts) != len(set(texts)):
        # Rule 53 suppressed exact duplicates before persistence, so two
        # current bullets sharing a text would leave the key non-total and the
        # render order dependent on the stored row order.
        raise IntegrityFailureError("bullet_text_duplicate")
    # Exact-text uniqueness is not enough: §18 renders the LF/NFC projection,
    # so two byte-distinct stored texts that project equal would publish the
    # same logical line twice under two IDs. Stage 10 refuses that collision
    # before persistence; damaged state reaches it here instead.
    projections = [normalize_generated_text(text) for text in texts]
    if len(projections) != len(set(projections)):
        raise IntegrityFailureError("bullet_projection_collision")
    return tuple(ordered)


@dataclass(frozen=True)
class BranchExportGraph:
    branch: StoredRecord[ResumeBranch]
    # §13.10 render order, not ID-byte order: the export members and their
    # companions all present the pack in this sequence.
    bullets: tuple[StoredRecord[ResumeBullet], ...]
    snapshot: StoredRecord[AssessmentSnapshot]
    snapshot_created_at_text: str
    job_description: JobDescription
    claims: tuple[StoredRecord[SelfClaim], ...]
    facts: tuple[StoredRecord[ExperienceFact], ...]
    evidence_items: tuple[EvidenceItem, ...]
    raw_logs: tuple[RawLog, ...]
    fact_sources: tuple[FactSourceRecord, ...]
    # The anchor snapshot's complete current member set. No companion renders
    # an uncited member, but every one of them is read to gate: §16.11's
    # integrity half reduces the stored aggregate from exactly this set and
    # matches the `narrative_summary` against it. §13.14 rule 2 puts anything
    # read to *gate* in the render hash, so an uncited member changing under a
    # reduction that happens to land on the same aggregate still invalidates
    # the published set.
    anchor_claims: tuple[StoredRecord[SelfClaim], ...] = ()
    # Supplemental rows outside the bullet and cited-claim source closure —
    # counterevidence grounding targets. Read to validate rendering, so rule 2
    # folds them into `source_ids` and the bundle, while the closed §13.12
    # evidence map and companions keep consuming only the closure fields.
    supplemental_facts: tuple[StoredRecord[ExperienceFact], ...] = ()
    supplemental_fact_sources: tuple[FactSourceRecord, ...] = ()
    supplemental_evidence_items: tuple[EvidenceItem, ...] = ()
    supplemental_raw_logs: tuple[RawLog, ...] = ()

    def source_ids(self) -> dict[str, list[str]]:
        # §13.14 rule 2: each list is the complete duplicate-free ID-byte-
        # ordered set actually read to render a member, and the snapshot and
        # job-description lists each hold exactly the one ID `identity` names.
        # `self_claim_ids` stays the cited set: rule 2 scopes the source lists
        # to what renders a member and widens only the hash to what validates
        # or gates one, so a gate-only anchor member belongs to the second
        # surface and not this one.
        def merged(main: list[str], extra: list[str]) -> list[str]:
            return sorted(set(main) | set(extra), key=id_key)

        return {
            "resume_bullet_ids": sorted(
                (item.value.id for item in self.bullets), key=id_key
            ),
            "assessment_snapshot_ids": [self.snapshot.value.id],
            "job_description_ids": [self.job_description.id],
            "self_claim_ids": [item.value.id for item in self.claims],
            "experience_fact_ids": merged(
                [item.value.id for item in self.facts],
                [item.value.id for item in self.supplemental_facts],
            ),
            "evidence_item_ids": merged(
                [item.id for item in self.evidence_items],
                [item.id for item in self.supplemental_evidence_items],
            ),
            "raw_log_ids": merged(
                [item.id for item in self.raw_logs],
                [item.id for item in self.supplemental_raw_logs],
            ),
            "jd_requirement_ids": sorted(
                (
                    requirement.id
                    for requirement in self.job_description.parsed.requirements
                ),
                key=id_key,
            ),
        }


class BranchRenderEntry(_BundleModel):
    value: ResumeBranch
    generation_id: str
    produced_by_run_id: str


class BulletRenderEntry(_BundleModel):
    value: ResumeBullet
    generation_id: str
    produced_by_run_id: str


class JobDescriptionRenderEntry(_BundleModel):
    value: JobDescription


def load_current_branch(
    connection: sqlite3.Connection, branch_id: str
) -> tuple[sqlite3.Row, ResumeBranch]:
    row = connection.execute(
        "SELECT * FROM resume_branches WHERE id = ?", (branch_id,)
    ).fetchone()
    if row is None:
        raise SelectorNotFoundError()
    branch = hydrate_resume_branch(row)
    if branch.superseded_at is not None:
        raise SelectorNotFoundError()
    return row, branch


def _stored_bullet(
    connection: sqlite3.Connection, bullet: ResumeBullet
) -> StoredRecord[ResumeBullet]:
    row = connection.execute(
        "SELECT * FROM resume_bullets WHERE id = ?", (bullet.id,)
    ).fetchone()
    if row is None:
        raise IntegrityFailureError("branch_bullet_missing")
    return _stored(row, bullet)


def load_branch_graph(
    connection: sqlite3.Connection,
    *,
    branch_row: sqlite3.Row,
    branch: ResumeBranch,
) -> BranchExportGraph:
    """Load and validate one branch's complete §13.12 export closure.

    The §18 fail list is enforced here rather than trusted from the persisted
    verdict: a status column says a verifier once passed this pack, not that
    the graph beneath it still resolves.
    """

    branch_record = _stored(branch_row, branch)
    bullets, job_description = load_branch_pack(connection, branch)

    requirement_ids = [
        requirement.id for requirement in job_description.parsed.requirements
    ]
    ordered = render_order(bullets, requirement_ids)

    snapshot_row = connection.execute(
        "SELECT * FROM assessment_snapshots WHERE id = ?",
        (branch.assessment_snapshot_id,),
    ).fetchone()
    if snapshot_row is None:
        raise IntegrityFailureError("branch_snapshot_missing")
    snapshot = hydrate_assessment_snapshot(snapshot_row)
    # §16.11's integrity half runs before its status half, exactly as the
    # assessment export does: a stored aggregate that no longer reduces from
    # its own current claims, a claim set missing its matching
    # narrative_summary, or a member claim from another Stage 6 batch is
    # broken state, and reading an allowlist verdict off it would let damaged
    # state license the pack. This also supplies the checked member set the
    # cited-claim loop below resolves against.
    snapshot_record, members = load_snapshot_claims(
        connection, snapshot_row=snapshot_row, snapshot=snapshot
    )
    failure = assessment_integrity_failure(snapshot, members)
    if failure == "aggregate_mismatch":
        raise IntegrityFailureError("snapshot_aggregate_mismatch")
    if failure is not None:
        raise IntegrityFailureError("snapshot_narrative_gate_failed")
    # §18: the anchor must still be eligible to anchor Stage 10. A snapshot
    # that fell out of the allowlist after generation no longer licenses the
    # pack it fixed the assessment context for.
    if snapshot.verification_status not in STAGE10_ANCHOR_ALLOWLIST:
        raise BulletPackExportBlockedError()

    bullet_records: list[StoredRecord[ResumeBullet]] = []
    for bullet in ordered:
        if bullet.branch_id != branch.id:
            raise IntegrityFailureError("bullet_branch_mismatch")
        # §16.11: only a `supported` bullet may enter the exported pack, and
        # the gate is re-read here rather than inherited from Stage 11's run.
        if bullet.verification_status not in BULLET_EXPORT_ALLOWLIST:
            raise BulletPackExportBlockedError()
        require_direct_retained_chain(
            connection,
            bullet.source_fact_ids,
            diagnostic="bullet_direct_chain_missing",
        )
        bullet_records.append(_stored_bullet(connection, bullet))

    # §18: a cited claim is a current `supported` member of the branch's own
    # anchor snapshot, so the checked member set above is both lookup and
    # membership test — it already refused a superseded or mixed-generation
    # member, so nothing here can reintroduce one.
    by_id = {item.value.id: item for item in members}
    claim_ids = {
        claim_id for bullet in ordered for claim_id in bullet.source_self_claim_ids
    }
    claim_records: list[StoredRecord[SelfClaim]] = []
    supplemental_refs: dict[str, set[str]] = {}
    for claim_id in sorted(claim_ids, key=id_key):
        record = by_id.get(claim_id)
        if record is None:
            raise IntegrityFailureError("bullet_claim_not_member")
        # Membership is integrity; status is a §16.11 consumer gate. A claim
        # a later Stage 7 pass moved off `supported` is a coherent graph the
        # gate refuses, so it owes the caller class 10, not class 7.
        if record.value.verification_status not in BULLET_EXPORT_ALLOWLIST:
            raise BulletPackExportBlockedError()
        claim_records.append(record)
        require_direct_retained_chain(
            connection,
            record.value.source_fact_ids,
            diagnostic="claim_direct_chain_missing",
        )
        for counterevidence in record.value.counterevidence:
            # §16.1, exactly as the assessment export reads it: a cited claim's
            # counterevidence is a typed reference into current state, so it is
            # resolved rather than trusted. Without this the pack could publish
            # over a dangling or §13.3-displaced target, and its hash would
            # cover only the reference string instead of what it points at.
            _require_reference(
                connection,
                counterevidence.source_ref_type,
                counterevidence.source_ref_id,
                "export_source_reference_invalid",
            )
            supplemental_refs.setdefault(counterevidence.source_ref_type, set()).add(
                counterevidence.source_ref_id
            )

    # The fact closure is every fact any exported row reaches: a bullet's own
    # sources plus each cited claim's, since the evidence map resolves both.
    fact_ids = {fact_id for bullet in ordered for fact_id in bullet.source_fact_ids}
    fact_ids.update(
        fact_id for item in claim_records for fact_id in item.value.source_fact_ids
    )

    fact_records: list[StoredRecord[ExperienceFact]] = []
    fact_source_records: list[FactSourceRecord] = []
    for fact_id in sorted(fact_ids, key=id_key):
        row = connection.execute(
            "SELECT * FROM experience_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("bullet_fact_missing")
        fact = get_experience_fact(connection, fact_id)
        if fact is None or fact.superseded_at is not None:
            raise IntegrityFailureError("bullet_fact_superseded")
        for source in connection.execute(
            "SELECT fact_id, evidence_item_id, support_type "
            "FROM fact_sources WHERE fact_id = ?",
            (fact_id,),
        ).fetchall():
            support_type = source["support_type"]
            if support_type not in {"direct", "corroborating"}:
                raise IntegrityFailureError("fact_source_support_type_invalid")
            fact_source_records.append(
                FactSourceRecord(
                    fact_id=source["fact_id"],
                    evidence_item_id=source["evidence_item_id"],
                    support_type=support_type,
                )
            )
        fact_records.append(_stored(row, fact))
    fact_source_records.sort(
        key=lambda item: (id_key(item.fact_id), id_key(item.evidence_item_id))
    )

    evidence_ids = sorted(
        {source.evidence_item_id for source in fact_source_records}, key=id_key
    )
    evidence_items: list[EvidenceItem] = []
    for evidence_id in evidence_ids:
        row = connection.execute(
            "SELECT * FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("fact_evidence_missing")
        evidence_items.append(hydrate_evidence_item(row))

    raw_log_ids = sorted({item.raw_log_id for item in evidence_items}, key=id_key)
    raw_logs: list[RawLog] = []
    for raw_log_id in raw_log_ids:
        row = connection.execute(
            "SELECT * FROM raw_logs WHERE id = ?", (raw_log_id,)
        ).fetchone()
        if row is None:
            raise IntegrityFailureError("fact_raw_log_missing")
        raw_logs.append(hydrate_raw_log(row))

    # §13.12: the evidence map must agree exactly with the persisted §11
    # relations — per fact, the `fact_sources` rows, the hydrated
    # `evidence_item_ids`, and the derived raw-log set are equal, not subsets.
    log_by_evidence = {item.id: item.raw_log_id for item in evidence_items}
    rows_by_fact: dict[str, list[str]] = defaultdict(list)
    for source in fact_source_records:
        rows_by_fact[source.fact_id].append(source.evidence_item_id)
    for fact_record in fact_records:
        fact = fact_record.value
        row_evidence = sorted(set(rows_by_fact.get(fact.id, [])), key=id_key)
        if row_evidence != list(fact.evidence_item_ids):
            raise IntegrityFailureError("fact_evidence_closure_incomplete")
        derived_logs = sorted(
            {log_by_evidence[item] for item in row_evidence}, key=id_key
        )
        if derived_logs != list(fact.source_log_ids):
            raise IntegrityFailureError("fact_raw_log_closure_incomplete")
        try:
            validate_experience_fact_sources(connection, fact)
        except IntegrityFailureError as error:
            raise IntegrityFailureError("fact_source_selection_invalid") from error

    # The one reference kind that can leave this export's closure. The shared
    # loader cascades each target one level — fact -> fact_sources/evidence ->
    # raw log — and re-checks the same per-fact equality as the closure above,
    # so a counterevidence target enters the hash as a validated projection.
    closure = load_supplemental_closure(
        connection,
        supplemental_refs=supplemental_refs,
        fact_ids=fact_ids,
        evidence_ids=evidence_ids,
        raw_log_ids=raw_log_ids,
        log_by_evidence=log_by_evidence,
    )

    return BranchExportGraph(
        branch=branch_record,
        bullets=tuple(bullet_records),
        snapshot=snapshot_record,
        snapshot_created_at_text=snapshot_row["created_at"],
        job_description=job_description,
        claims=tuple(claim_records),
        facts=tuple(fact_records),
        evidence_items=tuple(evidence_items),
        raw_logs=tuple(raw_logs),
        fact_sources=tuple(fact_source_records),
        anchor_claims=tuple(members),
        supplemental_facts=closure.facts,
        supplemental_fact_sources=closure.fact_sources,
        supplemental_evidence_items=closure.evidence_items,
        supplemental_raw_logs=closure.raw_logs,
    )


class BranchRenderInputBundle(_BundleModel):
    manifest_version: Literal[6] = 6
    output_kind: Literal["resume"] = "resume"
    resume_branches: list[BranchRenderEntry]
    resume_bullets: list[BulletRenderEntry]
    assessment_snapshots: list[SnapshotRenderEntry]
    job_descriptions: list[JobDescriptionRenderEntry]
    self_claims: list[ClaimRenderEntry]
    experience_facts: list[FactRenderEntry]
    evidence_items: list[EvidenceRenderEntry]
    raw_logs: list[RawLogRenderEntry]
    fact_sources: list[FactSourceRenderEntry]


def branch_render_input_bundle(graph: BranchExportGraph) -> BranchRenderInputBundle:
    """Project every row the export read into §13.14's hashed bundle.

    Rule 2: no database value read to render, validate, or gate a member may
    be excluded, so a bullet status transition or a snapshot going ineligible
    invalidates a published set even when every ID is unchanged.

    Rule 2 also partitions entries by canonical entity type and ID-byte-orders
    them *within* type, so the bullets are re-sorted by ID here. Render order
    is a presentation fact of `bullet_pack.md` and its companions; letting it
    reach the hashed bundle would make the digest depend on which requirement
    each bullet happened to match, and any other conforming implementation
    would compute a different `render_input_sha256` for identical state.

    The claim entries are the anchor's whole member set, not the cited subset
    the companions link: the §16.11 gate reads every member, so an uncited one
    changing is a gate input changing.
    """

    bundle_facts = _merged_stored(graph.facts, graph.supplemental_facts)
    bundle_evidence = sorted(
        (*graph.evidence_items, *graph.supplemental_evidence_items),
        key=lambda item: id_key(item.id),
    )
    bundle_raw_logs = sorted(
        (*graph.raw_logs, *graph.supplemental_raw_logs),
        key=lambda item: id_key(item.id),
    )
    bundle_fact_sources = sorted(
        (*graph.fact_sources, *graph.supplemental_fact_sources),
        key=lambda item: (id_key(item.fact_id), id_key(item.evidence_item_id)),
    )
    return BranchRenderInputBundle(
        resume_branches=[
            BranchRenderEntry(
                value=graph.branch.value,
                generation_id=graph.branch.generation_id,
                produced_by_run_id=graph.branch.produced_by_run_id,
            )
        ],
        resume_bullets=[
            BulletRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in sorted(graph.bullets, key=lambda item: id_key(item.value.id))
        ],
        assessment_snapshots=[
            SnapshotRenderEntry(
                value=graph.snapshot.value,
                stored_created_at=graph.snapshot_created_at_text,
                generation_id=graph.snapshot.generation_id,
                produced_by_run_id=graph.snapshot.produced_by_run_id,
            )
        ],
        job_descriptions=[JobDescriptionRenderEntry(value=graph.job_description)],
        self_claims=[
            ClaimRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in sorted(
                graph.anchor_claims, key=lambda item: id_key(item.value.id)
            )
        ],
        experience_facts=[
            FactRenderEntry(
                value=item.value,
                generation_id=item.generation_id,
                produced_by_run_id=item.produced_by_run_id,
            )
            for item in bundle_facts
        ],
        evidence_items=[EvidenceRenderEntry(value=item) for item in bundle_evidence],
        raw_logs=[RawLogRenderEntry(value=item) for item in bundle_raw_logs],
        fact_sources=[
            FactSourceRenderEntry(
                fact_id=item.fact_id,
                evidence_item_id=item.evidence_item_id,
                support_type=item.support_type,
            )
            for item in bundle_fact_sources
        ],
    )


__all__ = [
    "BranchExportGraph",
    "BranchRenderInputBundle",
    "branch_render_input_bundle",
    "current_branch_bullets",
    "load_branch_graph",
    "load_branch_pack",
    "load_current_branch",
    "render_order",
    "require_consistent_bullets",
    "require_current_anchor",
    "require_direct_retained_chain",
    "require_one_generation",
]
