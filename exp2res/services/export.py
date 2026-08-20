"""Stage 12 export services: the assessment set and the verified bullet pack."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from exp2res.errors import (
    AssessmentExportBlockedError,
    InvalidInputError,
    ManagedOutputIncompleteError,
    SelectorNotFoundError,
)
from exp2res.exports.branch import load_branch_graph, load_current_branch
from exp2res.exports.graph import load_assessment_graph, load_current_snapshot
from exp2res.exports.managed import (
    ENTITY_ID,
    publish_assessment,
    publish_branch,
    reconcile_managed_outputs as _reconcile_managed_outputs,
)
from exp2res.pipeline.stage10 import validated_branch_name
from exp2res.services.writers import transaction
from exp2res.storage.repository import current_branch_by_folded_name
from exp2res.storage.workspace import writer_database


_ASSESSMENT_EXPORT_ALLOWLIST = frozenset(
    {
        "supported",
        "partially_supported",
        "inferred_but_acceptable",
        "needs_clarification",
        "contradicted",
    }
)


def require_export_eligible(verification_status: str) -> None:
    # §16.11 assessment-export allowlist.
    if verification_status not in _ASSESSMENT_EXPORT_ALLOWLIST:
        raise AssessmentExportBlockedError()


@dataclass(frozen=True)
class AssessmentExportResult:
    manifest_path: str
    managed_paths: list[str]


@dataclass(frozen=True)
class BulletPackExportResult:
    branch_id: str
    branch_name: str
    manifest_path: str
    managed_paths: list[str]


def reconcile_managed_outputs(workspace: Path) -> tuple[str, ...]:
    # §13.14 writer preamble under the business-writer lock.
    with writer_database(workspace):
        return _reconcile_managed_outputs(workspace)


def export_assessment(
    workspace: Path,
    *,
    snapshot_id: str,
    clock=None,
) -> AssessmentExportResult:
    # Selector hygiene precedes workspace/output I/O.
    if ENTITY_ID.fullmatch(snapshot_id) is None:
        raise InvalidInputError()

    # §15.10 rule 8: abandoned-telemetry reconciliation precedes the business op.
    with writer_database(workspace) as connection:
        residuals = _reconcile_managed_outputs(workspace)
        if residuals:
            raise ManagedOutputIncompleteError(residuals)
        with transaction(connection):
            snapshot_row, snapshot = load_current_snapshot(connection, snapshot_id)
            require_export_eligible(snapshot.verification_status)
            graph = load_assessment_graph(
                connection,
                snapshot_row=snapshot_row,
                snapshot=snapshot,
            )
            _manifest, managed_paths = publish_assessment(
                workspace, graph, clock=clock
            )

    manifest_path = next(
        path for path in managed_paths if Path(path).name == "manifest.json"
    )
    return AssessmentExportResult(
        manifest_path=manifest_path,
        managed_paths=list(managed_paths),
    )


def export_bullet_pack(
    workspace: Path,
    *,
    branch_name: str,
    clock=None,
) -> BulletPackExportResult:
    # §14.14 rule 4: selector hygiene precedes workspace and output I/O.
    validated = validated_branch_name(branch_name)

    with writer_database(workspace) as connection:
        # §15.10 rule 8.
        residuals = _reconcile_managed_outputs(workspace)
        if residuals:
            raise ManagedOutputIncompleteError(residuals)
        with transaction(connection):
            selected = current_branch_by_folded_name(connection, validated)
            if selected is None:
                raise SelectorNotFoundError()
            branch_row, branch = load_current_branch(connection, selected.id)
            graph = load_branch_graph(
                connection, branch_row=branch_row, branch=branch
            )
            _manifest, managed_paths = publish_branch(workspace, graph, clock=clock)

    manifest_path = next(
        path for path in managed_paths if Path(path).name == "manifest.json"
    )
    return BulletPackExportResult(
        branch_id=branch.id,
        branch_name=branch.name,
        manifest_path=manifest_path,
        managed_paths=list(managed_paths),
    )

