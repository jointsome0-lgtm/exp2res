"""Shared §13.10 branch/bullet supersession and its §13.13 rule 9 report.

Stage 3, Stage 6, Stage 7, and correction capture all reach the same
conclusion from different directions: a replacement fact set, a replacement
assessment generation, a changed verifier state, or a correction leaves no
resume branch honest against the graph it was generated from. This module owns
that one swap so each trigger site names its trigger and supplies its snapshot
set instead of re-deriving the mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sqlite3
from typing import Iterable

from exp2res.domain.canonical import byte_sorted, id_key
from exp2res.domain.results import InvalidatedBranch, invalidated_branch
from exp2res.errors import IntegrityFailureError
from exp2res.storage.repository import (
    list_resume_branches,
    list_resume_bullets_for_branch,
    mark_resume_branches_superseded,
    mark_resume_bullets_superseded,
)


@dataclass(frozen=True)
class BranchSupersession:
    branch_ids: tuple[str, ...] = ()
    bullet_ids: tuple[str, ...] = ()
    superseded_generation_ids: tuple[str, ...] = ()
    invalidated_branches: tuple[InvalidatedBranch, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.branch_ids)


def _former_view_scope(connection: sqlite3.Connection, snapshot_id: str) -> str:
    # The anchoring snapshot may be superseded in this very transaction, so the
    # lookup deliberately ignores lifecycle state; §13.13 rule 9 wants the view
    # the branch was generated against.
    row = connection.execute(
        "SELECT scope FROM assessment_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    if row is None:
        raise IntegrityFailureError("branch_snapshot_missing")
    return row["scope"]


def supersede_branches(
    connection: sqlite3.Connection,
    branch_ids: Iterable[str],
    *,
    superseded_at: datetime,
) -> BranchSupersession:
    """Supersede the named current branches with every current bullet on them."""

    selected = list(branch_ids)
    if not selected:
        return BranchSupersession()
    wanted = set(selected)
    branches = tuple(
        branch
        for branch in list_resume_branches(connection, current_only=True)
        if branch.id in wanted
    )
    if len(branches) != len(wanted):
        raise IntegrityFailureError("branch_supersession_target_invalid")

    bullet_ids: list[str] = []
    reports: list[InvalidatedBranch] = []
    generation_ids: set[str] = set()
    for branch in branches:
        bullets = list_resume_bullets_for_branch(
            connection, branch.id, current_only=True
        )
        bullet_ids.extend(bullet.id for bullet in bullets)
        reports.append(
            invalidated_branch(
                name=branch.name,
                job_description_id=branch.job_description_id,
                scope=_former_view_scope(connection, branch.assessment_snapshot_id),
                snapshot_id=branch.assessment_snapshot_id,
            )
        )

    # §14.14 rule 5 reports produced OR invalidated generation IDs, and §12 rule
    # 13 shares one ID across a branch and its bullets, so both tables are read.
    # Each branch is read on its own: binding one parameter per bullet would
    # make a large branch exceed the connection's SQLITE_LIMIT_VARIABLE_NUMBER.
    for branch in branches:
        generation_ids.update(
            row[0]
            for row in connection.execute(
                "SELECT generation_id FROM resume_branches WHERE id = ? "
                "UNION SELECT DISTINCT generation_id FROM resume_bullets "
                "WHERE branch_id = ? AND superseded_at IS NULL",
                (branch.id, branch.id),
            )
        )

    mark_resume_bullets_superseded(connection, bullet_ids, superseded_at)
    mark_resume_branches_superseded(
        connection, (branch.id for branch in branches), superseded_at
    )
    return BranchSupersession(
        branch_ids=byte_sorted(branch.id for branch in branches),
        bullet_ids=byte_sorted(bullet_ids),
        superseded_generation_ids=byte_sorted(generation_ids),
        invalidated_branches=tuple(
            sorted(reports, key=lambda item: id_key(item.name))
        ),
    )


def supersede_dependent_branches(
    connection: sqlite3.Connection,
    snapshot_ids: Iterable[str],
    *,
    superseded_at: datetime,
) -> BranchSupersession:
    """Supersede every current branch anchored to one of these snapshots."""

    anchors = set(snapshot_ids)
    if not anchors:
        return BranchSupersession()
    return supersede_branches(
        connection,
        (
            branch.id
            for branch in list_resume_branches(connection, current_only=True)
            if branch.assessment_snapshot_id in anchors
        ),
        superseded_at=superseded_at,
    )


def supersede_current_branches(
    connection: sqlite3.Connection, *, superseded_at: datetime
) -> BranchSupersession:
    """Supersede every current branch, whatever it is anchored to (§13.13 r4)."""

    return supersede_branches(
        connection,
        (branch.id for branch in list_resume_branches(connection, current_only=True)),
        superseded_at=superseded_at,
    )
