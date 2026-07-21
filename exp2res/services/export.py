"""Stage 12 assessment-export service substrate (no CLI surface)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from exp2res.errors import (
    AssessmentExportBlockedError,
    InvalidInputError,
    ManagedOutputIncompleteError,
)
from exp2res.exports.graph import load_assessment_graph, load_current_snapshot
from exp2res.exports.managed import (
    ENTITY_ID,
    publish_assessment,
    reconcile_managed_outputs as _reconcile_managed_outputs,
)
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
    """Apply the §16.11 assessment-export allowlist to a loaded status."""

    if verification_status not in _ASSESSMENT_EXPORT_ALLOWLIST:
        raise AssessmentExportBlockedError()


@dataclass(frozen=True)
class AssessmentExportResult:
    manifest_path: str
    managed_paths: list[str]


def reconcile_managed_outputs(workspace: Path) -> tuple[str, ...]:
    """Run the §13.14 writer preamble under the business-writer lock."""

    with writer_database(workspace):
        return _reconcile_managed_outputs(workspace)


def export_assessment(
    workspace: Path,
    *,
    snapshot_id: str,
    clock=None,
) -> AssessmentExportResult:
    """Render, publish, and revalidate one current assessment snapshot."""

    # The caller-supplied selector is rejected before workspace/output I/O;
    # the selected stored row is validated again by the managed writer.
    if ENTITY_ID.fullmatch(snapshot_id) is None:
        raise InvalidInputError()

    # §15.10 rule 8: export is a later compatible writer, so the default
    # abandoned-telemetry reconciliation runs before its business operation.
    with writer_database(workspace) as connection:
        residuals = _reconcile_managed_outputs(workspace)
        if residuals:
            raise ManagedOutputIncompleteError(residuals)
        try:
            connection.execute("BEGIN IMMEDIATE")
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
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    manifest_path = next(
        path for path in managed_paths if Path(path).name == "manifest.json"
    )
    return AssessmentExportResult(
        manifest_path=manifest_path,
        managed_paths=list(managed_paths),
    )

