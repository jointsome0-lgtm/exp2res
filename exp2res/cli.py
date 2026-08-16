"""Typer CLI implementing the available §22 command surface."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import threading
from typing import Callable, Iterator, cast

import typer

try:
    from typer._click.exceptions import Abort, Exit, UsageError
except ImportError:  # typer releases that depend on an external click
    from click.exceptions import Abort, Exit, UsageError

from exp2res.config import load_workspace_config, require_timezone
from exp2res.domain.canonical import id_key
from exp2res.domain.enums import TemporalConfidence, TemporalPrecision
from exp2res.domain.models import (
    ExperienceFact,
    OccurredAt,
    SelfClaim,
)
from exp2res.domain.results import (
    AffectedIds,
    AssessListResult,
    AssessShowResult,
    AssessmentExportResult,
    CLIEnvelope,
    CommandPath,
    ContradictionsResult,
    EntityIdGroup,
    EvidenceItemProjection,
    FactsListResult,
    GapsListResult,
    InvalidatedBranch,
    InvalidatedView,
    JdDeleteResult,
    JdListResult,
    LogProjection,
    LogsDeleteResult,
    LogsListResult,
    LogsShowResult,
    Outcome,
    Retry,
    SchemaProjection,
    SchemaResult,
    SelectedLogProjection,
    SnapshotListItem,
    carry_committed,
    committed_outcome,
    extend_committed,
    merged_invalidated_branches,
    merged_invalidated_views,
    render_path,
    render_text,
)
from exp2res.errors import (
    Exp2ResError,
    InvalidInputError,
    InvalidUsageError,
    MigrationFailedError,
    MigrationInterrupted,
    NonInteractiveInputRequired,
    OperationCancelledError,
    PeriodNotAllowedError,
    SelectorNotFoundError,
    SnapshotNotCurrentError,
)
from exp2res.services.capture import (
    capture_daily,
    capture_daily_file,
    capture_gap_answer,
    capture_gap_answer_file,
    capture_outcome,
    capture_retro,
    capture_retro_file,
    validate_gap_answer_selection,
    validate_project_label,
)
from exp2res.services.source_files import validate_artifact_locator_count
from exp2res.services.correction import (
    CorrectionOutcome,
    capture_correction,
    read_correction_source,
    validate_correction_selection,
)
from exp2res.pipeline.stage10 import (
    bullets_generate_outcome,
    Stage10Result,
    validated_branch_name,
)
from exp2res.pipeline.stage11 import (
    bullets_verify_outcome,
    Stage11Result,
)
from exp2res.services.bullets import run_bullets_generate, run_bullets_verify
from exp2res.pipeline.stage6 import assess_generate_outcome, repair_outcome
from exp2res.pipeline.stage7 import assess_verify_outcome
from exp2res.services.assessment import (
    Stage6Result,
    Stage7Result,
    list_current_snapshots,
    run_assess_generate,
    run_assess_repair,
    run_assess_verify,
    show_snapshot,
)
from exp2res.pipeline.stage4 import detections_generate_outcome
from exp2res.services.detection import (
    Stage4Result,
    list_current_contradictions,
    list_current_gaps,
    run_detections_generate,
    show_contradiction,
)
from exp2res.pipeline.stage3 import stage3_outcome
from exp2res.services.extraction import (
    Stage3Result,
    run_extract,
    validate_extract_selection,
)
from exp2res.services.export import (
    export_assessment,
    export_bullet_pack,
    require_export_eligible,
)
from exp2res.services.facts import list_facts, show_fact
from exp2res.pipeline.stage8 import (
    jd_add_affected,
    jd_add_outcome,
)
from exp2res.services.job_descriptions import (
    delete_job_description,
    jd_delete_affected,
    jd_delete_human_result,
    jd_delete_outcome,
    jd_delete_result,
    job_description_projection,
    JobDescriptionCleanupOutcome,
    JobDescriptionDeleteOutcome,
    list_job_descriptions,
    run_jd_add_file,
    show_job_description,
)
from exp2res.services.logs import DeleteOutcome, delete_log, list_logs, show_log
from exp2res.services.lifecycle import (
    lifecycle_outcome,
    LifecycleResult,
    record_cancelled_lifecycle,
    run_recompute,
)
from exp2res.services.view_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ViewServer,
    validate_bind,
)
from exp2res.services.imports import (
    import_created,
    import_design_document,
    import_outcome,
    import_payload,
    import_record_line,
    import_result,
    ImportOutcome,
)
from exp2res.services.time_input import parse_occurred, workspace_zone
from exp2res.services.workspace import (
    purge_affected,
    purge_outcome,
    purge_workspace,
    PurgeOutcome,
)
from exp2res.storage.repository import (
    current_branch_by_folded_name,
    get_assessment_snapshot,
)
from exp2res.storage.workspace import (
    SchemaStatus,
    discover_workspace,
    initialize_workspace,
    inspect_workspace,
    migrate_workspace,
    read_database,
    require_compatible,
    collect_preamble_residuals,
    writer_database,
)


app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    help="Local-first Exp2Res manual capture.",
)
db_app = typer.Typer(help="Inspect or migrate the workspace schema.")
log_app = typer.Typer(help="Capture a manual record.")
logs_app = typer.Typer(help="Inspect or owner-delete raw records.")
correction_app = typer.Typer(help="Correction capture lifecycle.")
import_app = typer.Typer(
    help="Import one local §19 source payload or design document."
)
facts_app = typer.Typer(help="Inspect current extracted facts.")
detections_app = typer.Typer(
    help="Generate both complete current detection sets together."
)
gaps_app = typer.Typer(help="Inspect and answer current gap questions.")
contradictions_app = typer.Typer(
    help="Inspect current contradictions and immutable contradiction history."
)
assess_app = typer.Typer(help="Generate and inspect self-assessment views.")
export_app = typer.Typer(help="Publish deterministic managed exports.")
jd_app = typer.Typer(
    help="Add, list, and owner-delete job descriptions."
)
bullets_app = typer.Typer(help="Generate the verified bullet pack for a vacancy.")
workspace_app = typer.Typer(help="Manage the whole initialized workspace.")
view_app = typer.Typer(help="Serve the read-only local views on loopback.")
app.add_typer(db_app, name="db")
app.add_typer(log_app, name="log")
app.add_typer(logs_app, name="logs")
app.add_typer(correction_app, name="correction")
app.add_typer(import_app, name="import")
app.add_typer(facts_app, name="facts")
app.add_typer(detections_app, name="detections")
app.add_typer(gaps_app, name="gaps")
app.add_typer(contradictions_app, name="contradictions")
app.add_typer(assess_app, name="assess")
app.add_typer(export_app, name="export")
app.add_typer(jd_app, name="jd")
app.add_typer(bullets_app, name="bullets")
app.add_typer(workspace_app, name="workspace")
app.add_typer(view_app, name="view")


@dataclass(frozen=True)
class Controls:
    json_output: bool
    yes: bool
    no_input: bool
    workspace_override: str | None
    verbose: bool
    quiet: bool


def _carry_stage(
    error: Exp2ResError,
    kind: type,
    project: Callable[[object], Outcome],
) -> None:
    """§14.14 rules 5/6: report the stage swap an interrupt did not undo.

    The committed result rides on the error; its own projection turns it into
    exactly the envelope the successful path would have emitted, so an
    interrupted command reports the same effects it actually performed.
    """

    carried = getattr(error, "stage_result", None)
    if isinstance(carried, kind):
        carry_committed(error, project(carried))


def _status_for(exit_code: int) -> str:
    if exit_code == 0:
        return "ok"
    if exit_code == 9:
        return "cancelled"
    if exit_code == 10:
        return "blocked"
    return "failed"


def _empty_affected() -> AffectedIds:
    return AffectedIds(created=[], superseded=[], deleted=[])


def _schema_result(status: SchemaStatus) -> SchemaResult:
    return SchemaResult(
        schema=SchemaProjection(
            stored_version=status.stored_version,
            supported_version=status.supported_version,
            recognized=status.recognized,
            compatible=status.compatible,
            migration_path_available=status.migration_path_available,
            managed_backup_path=status.managed_backup_path,
        )
    )


def _log_projection(raw_log) -> LogProjection:
    return LogProjection(
        id=raw_log.id,
        recorded_at=raw_log.recorded_at,
        entry_type=raw_log.entry_type,
        source_type=raw_log.source_type,
        occurred=raw_log.occurred,
        project=raw_log.project,
        corrects_log_id=raw_log.corrects_log_id,
    )


def _selected_log_projection(raw_log) -> SelectedLogProjection:
    return SelectedLogProjection(
        **_log_projection(raw_log).model_dump(),
        external_ref=raw_log.external_ref,
    )


def _evidence_projection(item) -> EvidenceItemProjection:
    return EvidenceItemProjection(
        id=item.id,
        created_at=item.created_at,
        raw_log_id=item.raw_log_id,
        title=item.title,
        summary=item.summary,
        uri=item.uri,
        path=item.path,
        strength=item.strength,
    )


def _invalidated_view_lines(views: list[InvalidatedView]) -> list[str]:
    """§14.14 rule 5's one human rendering of §13.13 rule 9 view reports.

    Every command routes through `_run_command`, so composing the lines there
    is what makes the report reach the owner on the nonzero path too — the
    supersession that staled a published view is already committed when a
    later stage of the same lifecycle flow fails.
    """

    return [
        f"Invalidated assessment view {item.snapshot_id} "
        f"({item.scope}); regenerate with: {item.regeneration_command}"
        for item in views
    ]


def _invalidated_branch_lines(branches: list[InvalidatedBranch]) -> list[str]:
    """§14.14 rule 5's one human rendering of §13.13 rule 9 branch reports.

    The printed §14.10 shape is deliberately not executable: it keeps a
    `<new-snapshot-id>` placeholder because a branch needs a current snapshot
    that exists only after the invalidated view is regenerated.
    """

    return [
        f"Invalidated bullet-pack branch {item.name} for job description "
        f"{item.job_description_id}, generated against assessment view "
        f"{item.former_view.snapshot_id} ({item.former_view.scope}); "
        f"regenerate with: {item.regeneration_command_shape}"
        for item in branches
    ]


def _emit(envelope: CLIEnvelope, controls: Controls, human_result: str = "") -> None:
    if controls.json_output:
        payload = envelope.model_dump(mode="json", by_alias=True)
        typer.echo(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    elif human_result:
        typer.echo(human_result)


def _noninteractive(controls: Controls) -> bool:
    return controls.no_input or not sys.stdin.isatty()


def _failing_surface(error: Exp2ResError) -> str | None:
    """§15.10 rule 9's human-mode naming of the §15 surface that failed.

    Stage and contract identifier are service-owned constants, so this line
    carries no provider bytes, payload content, or credential; the provider's
    own error channel is never echoed. The §14.14 envelope is unchanged, and
    the same run stays inspectable through the reported `run_ids`.
    """

    stage = getattr(error, "failing_stage", None)
    contract = getattr(error, "failing_contract", None)
    if stage is None or contract is None:
        return None
    return (
        f"Failing surface: stage {stage}, contract {contract} "
        f"({error.diagnostic_class})."
    )


@contextmanager
def _interrupt_disposition_restored() -> Iterator[None]:
    """Give `SIGINT` back to its pre-invocation owner after the envelope.

    Only `view serve` installs a handler of its own (§14.17), and a second
    interrupt while the first drain finishes is its documented way to stop
    waiting. Handing `SIGINT` back the moment serving ends would leave that
    interrupt to the default handler while §14.14 rule 6's class-9 envelope
    is still being written, ending the process without it. Every other
    command leaves the disposition untouched, so this restores nothing.

    Off the main thread no handler can be installed or restored at all;
    `_require_interruptible` refuses the one command that would care.
    """

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)
    try:
        yield
    finally:
        if signal.getsignal(signal.SIGINT) is not previous:
            signal.signal(signal.SIGINT, previous)


def _run_command(
    context: typer.Context,
    command: CommandPath,
    operation: Callable[[Path, Controls], Outcome],
    *,
    init_command: bool = False,
    interrupt: Callable[[int, object], bool | None] | None = None,
) -> None:
    """Run one command's operation and emit its §14.14 envelope.

    `interrupt` belongs to a command that handles cancellation itself. It is
    installed before workspace discovery — before anything interruptible
    happens at all — and stays installed until the envelope is out, so no
    step of the run is left to the default handler's `KeyboardInterrupt`.
    Off the main thread nothing can be installed; the one command that
    passes a handler refuses to run there.
    """

    interruption_observed = threading.Event()
    startup_active = threading.Event()

    def handle_interrupt(signum: int, frame: object) -> None:
        interruption_observed.set()
        assert interrupt is not None
        cancel_startup = interrupt(signum, frame)
        if cancel_startup and startup_active.is_set():
            raise Abort()

    with _interrupt_disposition_restored():
        if interrupt is not None and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, handle_interrupt)
        _run_operation(
            context,
            command,
            operation,
            init_command=init_command,
            interruption_observed=(
                interruption_observed.is_set if interrupt is not None else None
            ),
            startup_active=startup_active if interrupt is not None else None,
        )


# §14.15/§14.16 name `deletion_incomplete` as the residual class for these
# two commands specifically. `logs delete` is absent because §14.11 routes
# it through §13.13 rules 6 and 8, where the rebuild republishes managed
# output and a residual is not necessarily a deletion failure.
_DESTRUCTIVE_DELETION_COMMANDS = frozenset({"workspace purge", "jd delete"})


def _run_operation(
    context: typer.Context,
    command: CommandPath,
    operation: Callable[[Path, Controls], Outcome],
    *,
    init_command: bool = False,
    interruption_observed: Callable[[], bool] | None = None,
    startup_active: threading.Event | None = None,
) -> None:
    controls = cast(Controls, context.obj)
    workspace: Path | None = None
    preamble_residuals: list[str] = []
    if startup_active is not None:
        startup_active.set()
    try:
        try:
            if interruption_observed is not None and interruption_observed():
                raise Abort()
            if controls.verbose and controls.quiet:
                error = Exp2ResError()
                error.exit_code = 2
                error.diagnostic_class = "invalid_usage"
                error.public_message = "--verbose and --quiet cannot be combined."
                raise error
            if init_command:
                if controls.workspace_override is not None:
                    error = Exp2ResError()
                    error.exit_code = 2
                    error.diagnostic_class = "invalid_usage"
                    error.public_message = "init does not accept --workspace."
                    raise error
                workspace = Path.cwd().resolve(strict=True)
            else:
                workspace = discover_workspace(
                    cwd=Path.cwd(), override=controls.workspace_override
                )
            with collect_preamble_residuals(preamble_residuals):
                outcome = operation(workspace, controls)
        except Exception:
            if interruption_observed is not None and interruption_observed():
                outcome = Outcome(exit_code=9, diagnostic_class="cancelled")
            else:
                raise
        finally:
            if startup_active is not None:
                startup_active.clear()
    except KeyboardInterrupt:
        outcome = Outcome(exit_code=9, diagnostic_class="cancelled")
    except Abort:
        outcome = Outcome(exit_code=9, diagnostic_class="cancelled")
    except MigrationFailedError as error:
        status = inspect_workspace(workspace) if workspace is not None else None
        outcome = Outcome(
            exit_code=error.exit_code,
            diagnostic_class=error.diagnostic_class,
            result=(
                None
                if status is None
                else _schema_result(
                    SchemaStatus(
                        stored_version=status.stored_version,
                        supported_version=status.supported_version,
                        recognized=status.recognized,
                        compatible=status.compatible,
                        migration_path_available=status.migration_path_available,
                        managed_backup_path=error.managed_backup_path,
                    )
                )
            ),
        )
        typer.echo(error.public_message, err=True)
    except Exp2ResError as error:
        # §14.14 rules 5/6: whatever the failing operation already committed
        # rides on the error as one projection, so the envelope is that
        # projection re-stamped with the error's own classification.
        outcome = replace(
            committed_outcome(error),
            exit_code=error.exit_code,
            diagnostic_class=error.diagnostic_class,
        )
        typer.echo(error.public_message, err=True)
        if not controls.json_output:
            surface = _failing_surface(error)
            if surface is not None:
                typer.echo(surface, err=True)
    except Exception:
        outcome = Outcome(exit_code=1, diagnostic_class="internal_error")
        typer.echo("The operation failed unexpectedly.", err=True)

    if outcome.exit_code and outcome.retry is not None and not controls.json_output:
        # §13.13 retry guidance is an operator diagnostic in human mode; the
        # JSON path carries the same executable command in the closed field.
        typer.echo(f"Retry: {outcome.retry.command}", err=True)

    view_lines = [
        *_invalidated_view_lines(outcome.invalidated_views),
        *_invalidated_branch_lines(outcome.invalidated_branches),
    ]
    if view_lines:
        outcome.human_result = "\n".join(
            [*([outcome.human_result] if outcome.human_result else []), *view_lines]
        )

    observed_residuals: list[str] = []
    for path in preamble_residuals:
        # A reported path that a later successful destructive or invalidation
        # step removed is no longer residual; anything unreadable stays
        # reported (fail closed). `lstat` never follows a final symlink.
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            pass
        observed_residuals.append(path)
    residual_paths = sorted(
        {
            render_path(path)
            for path in {*outcome.residual_paths, *observed_residuals}
        },
        key=os.fsencode,
    )
    outcome.residual_paths = residual_paths
    if residual_paths and (outcome.exit_code in {0, 10} or outcome.completed_report):
        # §14.14 rule 4: a non-cancelled completion that reports a residual is
        # class 8, including verifier findings that would otherwise be 10 and a
        # §19.4 import report that would otherwise be 2. An operational failure
        # is not a completion and keeps its own code while still reporting the
        # residual paths.
        outcome.exit_code = 8
        # §14.15/§14.16: a destructive privacy operation reports every
        # residual as `deletion_incomplete`, whatever produced it — the
        # command's own cleanup or the writer preamble merged in above.
        outcome.diagnostic_class = (
            "deletion_incomplete"
            if command in _DESTRUCTIVE_DELETION_COMMANDS
            else "managed_output_incomplete"
        )
    if residual_paths:
        # One diagnostic for every residual-carrying envelope — promoted,
        # direct class 8, cancelled, or failed — so a human-mode user always
        # sees the paths that still need cleanup.
        # Not every residual is a file to delete: §8.1's erasure sequence
        # reports the live database or its WAL when a checkpoint or `VACUUM`
        # cannot complete, and §14.16 requires that database to remain. The
        # wording therefore states that the paths are unresolved and never
        # prescribes removing them.
        typer.echo(
            "Cleanup did not complete; unresolved paths:",
            err=True,
        )
        for path in residual_paths:
            typer.echo(f"  {path}", err=True)
        if outcome.human_result:
            # The operation composed its primary result before the §13.14
            # preamble residuals were merged in, so a residual contributed by
            # the preamble alone would otherwise leave a human-mode success
            # sentence standing against an incomplete class-8 envelope.
            outcome.human_result += (
                "\nCleanup is incomplete; the paths reported above are "
                "unresolved."
            )

    if not controls.json_output:
        # §14.14 rule 5: human mode prints every envelope warning as one
        # stderr line on the success and nonzero paths alike; without this
        # the non-JSON surface would drop e.g. §13.3 rule 14's trace.
        # Free-text messages may carry line breaks — rendered as visible
        # escapes so one warning can never masquerade as a second line.
        for warning in outcome.warnings:
            message = warning.message.replace("\r", "\\r").replace("\n", "\\n")
            typer.echo(f"Warning ({warning.type}): {message}", err=True)

    envelope = CLIEnvelope(
        command=command,
        status=cast(object, _status_for(outcome.exit_code)),
        exit_code=outcome.exit_code,
        diagnostic_class=outcome.diagnostic_class,
        workspace=str(workspace) if workspace is not None else None,
        affected_ids=outcome.affected_ids,
        generation_ids=outcome.generation_ids,
        run_ids=outcome.run_ids,
        invalidated_views=outcome.invalidated_views,
        invalidated_branches=outcome.invalidated_branches,
        findings=outcome.findings,
        residual_paths=outcome.residual_paths,
        warnings=outcome.warnings,
        retry=outcome.retry,
        result=outcome.result,
    )
    _emit(envelope, controls, outcome.human_result)
    if outcome.exit_code:
        raise typer.Exit(outcome.exit_code)


@app.callback(invoke_without_command=True)
def root(
    context: typer.Context,
    json_output: bool = typer.Option(False, "--json"),
    yes: bool = typer.Option(False, "--yes"),
    no_input: bool = typer.Option(False, "--no-input"),
    workspace: str | None = typer.Option(None, "--workspace"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    context.obj = Controls(json_output, yes, no_input, workspace, verbose, quiet)
    if context.invoked_subcommand is None:
        raise UsageError("Missing command.")


@app.command("init")
def init_command(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        _, status, created = initialize_workspace(workspace)
        action = "Initialized" if created else "Opened"
        return Outcome(
            result=_schema_result(status),
            human_result=f"{action} schema version {status.stored_version} at {workspace}.",
        )

    _run_command(context, "init", operation, init_command=True)


@db_app.command("status")
def db_status(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        status = inspect_workspace(workspace)
        if status.compatible:
            return Outcome(
                result=_schema_result(status),
                human_result=f"Schema version {status.stored_version} is compatible.",
            )
        return Outcome(
            exit_code=4,
            diagnostic_class="schema_incompatible",
            result=_schema_result(status),
            human_result="Workspace schema is incompatible or unrecognized.",
        )

    _run_command(context, "db status", operation)


@db_app.command("migrate")
def db_migrate(context: typer.Context) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        status = inspect_workspace(workspace)
        if status.compatible:
            return Outcome(
                result=_schema_result(status),
                human_result="No migration is required.",
            )
        if status.migration_path_available:
            # §14.14 rule 3: db migrate is in the confirmation set — explicit
            # --yes when non-interactive, a TTY confirmation otherwise.
            if not controls.yes:
                if _noninteractive(controls):
                    raise NonInteractiveInputRequired()
                if not typer.confirm(
                    f"Migrate workspace schema from version "
                    f"{status.stored_version} to {status.supported_version} "
                    "(a verified backup is created first)?",
                    err=True,
                ):
                    return Outcome(exit_code=9, diagnostic_class="cancelled")
            try:
                migrated = migrate_workspace(workspace)
            except MigrationInterrupted as interrupt:
                # §14.14 rule 4: cancellation keeps code-9 precedence while
                # the committed effect — the retained verified backup —
                # remains reported in the cancelled envelope. Before the
                # backup exists there is no committed effect, so the generic
                # interrupt envelope (null result) applies.
                if interrupt.managed_backup_path is None:
                    return Outcome(exit_code=9, diagnostic_class="cancelled")
                after = inspect_workspace(workspace)
                return Outcome(
                    exit_code=9,
                    diagnostic_class="cancelled",
                    result=_schema_result(
                        SchemaStatus(
                            stored_version=after.stored_version,
                            supported_version=after.supported_version,
                            recognized=after.recognized,
                            compatible=after.compatible,
                            migration_path_available=after.migration_path_available,
                            managed_backup_path=interrupt.managed_backup_path,
                        )
                    ),
                )
            return Outcome(
                result=_schema_result(migrated),
                human_result=(
                    f"Migrated schema to version {migrated.stored_version}; "
                    f"backup: {migrated.managed_backup_path}."
                ),
            )
        return Outcome(
            exit_code=4,
            diagnostic_class="migration_path_unavailable",
            result=_schema_result(status),
            human_result="No complete migration path is registered.",
        )

    _run_command(context, "db migrate", operation)


def _merge_affected(*values: AffectedIds) -> AffectedIds:
    def merge(field_name: str) -> list[EntityIdGroup]:
        grouped: dict[str, set[str]] = {}
        for value in values:
            for group in getattr(value, field_name):
                grouped.setdefault(group.entity_type, set()).update(group.ids)
        return [
            EntityIdGroup(
                entity_type=entity_type,
                ids=sorted(ids, key=id_key),
            )
            for entity_type, ids in grouped.items()
        ]

    return AffectedIds(
        created=merge("created"),
        superseded=merge("superseded"),
        deleted=merge("deleted"),
    )


def _correction_affected(captured: CorrectionOutcome) -> AffectedIds:
    superseded = {
        "assessment_snapshot": captured.superseded_snapshot_ids,
        "contradiction": captured.superseded_contradiction_ids,
        "experience_fact": captured.superseded_fact_ids,
        "gap_question": captured.superseded_gap_ids,
        # §13.13 rule 4: correction capture supersedes every current branch and
        # bullet before the rebuild, which never sees them again — so this is
        # the only composition that can report them.
        "resume_branch": captured.superseded_branch_ids,
        "resume_bullet": captured.superseded_bullet_ids,
        "self_claim": captured.superseded_claim_ids,
    }
    return AffectedIds(
        created=[
            EntityIdGroup(
                entity_type="evidence_item",
                ids=sorted(
                    (item.id for item in captured.evidence_items),
                    key=id_key,
                ),
            ),
            EntityIdGroup(entity_type="raw_log", ids=[captured.raw_log.id]),
        ],
        superseded=[
            EntityIdGroup(entity_type=kind, ids=list(ids))
            for kind, ids in superseded.items()
            if ids
        ],
        deleted=[],
    )


def _decorate_lifecycle_error(
    error: Exp2ResError,
    *,
    base_affected: AffectedIds | None = None,
    base_generation_ids: tuple[str, ...] = (),
    base_invalidated_views: tuple[InvalidatedView, ...] = (),
    base_invalidated_branches: tuple[InvalidatedBranch, ...] = (),
    base_residual_paths: tuple[str, ...] = (),
    retry: Retry | None = None,
    result=None,
) -> None:
    progress = cast(
        LifecycleResult | None, getattr(error, "lifecycle_result", None)
    )
    extend_committed(
        error,
        affected_ids=_merge_affected(
            base_affected or AffectedIds(),
            progress.affected_ids if progress else AffectedIds(),
        ),
        generation_ids=sorted(
            {*base_generation_ids, *(progress.generation_ids if progress else ())},
            key=id_key,
        ),
        invalidated_views=list(
            merged_invalidated_views(
                base_invalidated_views,
                progress.invalidated_views if progress else (),
            )
        ),
        invalidated_branches=list(
            merged_invalidated_branches(
                base_invalidated_branches,
                progress.invalidated_branches if progress else (),
            )
        ),
        residual_paths=sorted(
            {*base_residual_paths, *(progress.residual_paths if progress else ())},
            key=os.fsencode,
        ),
        warnings=list(progress.warnings) if progress else [],
        retry=retry,
        result=result,
    )


def _validate_non_prompt_period(
    precision: str | None, period: str | None, confidence: str | None
) -> None:
    """§14.3's typed-set rules for a non-prompt temporal value.

    The set is all-or-nothing: a partial set cannot be completed by a prompt
    the non-prompt form never shows, and `unknown` with a period is a
    contradiction rather than a value to discard.
    """

    if precision is None or confidence is None:
        raise NonInteractiveInputRequired()
    if precision == "unknown":
        if period is not None:
            raise PeriodNotAllowedError()
    elif period is None:
        raise NonInteractiveInputRequired()


@log_app.command("today")
def log_today(
    context: typer.Context,
    project: str | None = typer.Option(None, "--project"),
    source_file: str | None = typer.Option(None, "--file"),
    artifacts: list[str] | None = typer.Option(None, "--artifact"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        artifact_values = tuple(artifacts or ())
        validate_artifact_locator_count(artifact_values)
        if source_file is not None:
            return capture_outcome(
                capture_daily_file(
                    workspace,
                    source_path=source_file,
                    project=project,
                    artifacts=artifact_values,
                )
            )
        if _noninteractive(controls):
            raise NonInteractiveInputRequired()
        require_compatible(workspace)
        # Fail closed on the local-time contract before collecting owner text.
        workspace_zone(require_timezone(load_workspace_config(workspace)))
        raw_text = typer.prompt("Describe what happened", err=True)
        return capture_outcome(
            capture_daily(
                workspace,
                raw_text=raw_text,
                project=project,
                artifacts=artifact_values,
            )
        )

    _run_command(context, "log today", operation)


@log_app.command("retro")
def log_retro(
    context: typer.Context,
    source_file: str | None = typer.Option(None, "--file"),
    period: str | None = typer.Option(None, "--period"),
    precision: str | None = typer.Option(None, "--precision"),
    confidence: str | None = typer.Option(None, "--confidence"),
    project: str | None = typer.Option(None, "--project"),
    artifacts: list[str] | None = typer.Option(None, "--artifact"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        artifact_values = tuple(artifacts or ())
        validate_artifact_locator_count(artifact_values)
        if source_file is not None:
            _validate_non_prompt_period(precision, period, confidence)
            validate_project_label(project)
            require_compatible(workspace)
            timezone_name = require_timezone(load_workspace_config(workspace))
            occurred = parse_occurred(
                period=period,
                precision=cast(TemporalPrecision, precision),
                confidence=cast(TemporalConfidence, confidence),
                timezone_name=timezone_name,
            )
            return capture_outcome(
                capture_retro_file(
                    workspace,
                    source_path=source_file,
                    occurred=occurred,
                    project=project,
                    artifacts=artifact_values,
                )
            )
        if _noninteractive(controls):
            raise NonInteractiveInputRequired()
        require_compatible(workspace)
        # Fail closed on the local-time contract before collecting owner text.
        timezone_name = require_timezone(load_workspace_config(workspace))
        workspace_zone(timezone_name)
        # An explicitly supplied value is owner input even when it is empty:
        # prompting for a replacement would discard it silently, so only an
        # absent option is prompted for and `parse_occurred` rejects the rest.
        precision_value = (
            precision
            if precision is not None
            else typer.prompt("How precise is this?", err=True)
        )
        if precision_value == "unknown":
            period_value = period
        else:
            period_value = (
                period
                if period is not None
                else typer.prompt("What period are we reconstructing?", err=True)
            )
        confidence_value = (
            confidence
            if confidence is not None
            else typer.prompt("How confident are you?", err=True)
        )
        project_value = (
            project
            if project is not None
            else typer.prompt("Project/activity?", default="", err=True) or None
        )
        validate_project_label(project_value)
        raw_text = typer.prompt("Describe what you remember.", err=True)
        occurred = parse_occurred(
            period=period_value,
            precision=cast(TemporalPrecision, precision_value),
            confidence=cast(TemporalConfidence, confidence_value),
            timezone_name=timezone_name,
        )
        return capture_outcome(
            capture_retro(
                workspace,
                occurred=occurred,
                raw_text=raw_text,
                project=project_value,
                artifacts=artifact_values,
            )
        )

    _run_command(context, "log retro", operation)


@correction_app.command("add")
def correction_add(
    context: typer.Context,
    log_id: str = typer.Option(..., "--log-id"),
    source_file: str | None = typer.Option(None, "--file"),
    period: str | None = typer.Option(None, "--period"),
    precision: str | None = typer.Option(None, "--precision"),
    confidence: str | None = typer.Option(None, "--confidence"),
    project_option: str | None = typer.Option(None, "--project"),
    clear_project: bool = typer.Option(False, "--clear-project"),
    artifacts: list[str] | None = typer.Option(None, "--artifact"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        artifact_values = tuple(artifacts or ())
        validate_artifact_locator_count(artifact_values)
        replaces_occurred = any(
            value is not None for value in (period, precision, confidence)
        )
        # §14.4: the replacement flags belong to the non-prompt form. Without
        # `--file` the command is about to ask for each of these anyway, so a
        # flag here would be a silent partial answer rather than a shortcut.
        if source_file is None and (
            replaces_occurred or project_option is not None or clear_project
        ):
            error = InvalidUsageError()
            error.public_message = (
                "--precision, --period, --confidence, --project, and "
                "--clear-project require --file."
            )
            raise error
        if project_option is not None and clear_project:
            error = InvalidUsageError()
            error.public_message = (
                "--project and --clear-project cannot be combined."
            )
            raise error
        target = validate_correction_selection(workspace, log_id=log_id)

        if source_file is not None:
            # Copy-unless-replaced, made explicit: no temporal flag copies the
            # target's placement exactly, and any flag demands §14.3's whole
            # typed set rather than a partial edit of the copied value.
            if replaces_occurred:
                _validate_non_prompt_period(precision, period, confidence)
                require_compatible(workspace)
                occurred = parse_occurred(
                    period=period,
                    precision=cast(TemporalPrecision, precision),
                    confidence=cast(TemporalConfidence, confidence),
                    timezone_name=require_timezone(
                        load_workspace_config(workspace)
                    ),
                )
            else:
                occurred = target.occurred
            project = (
                target.project
                if project_option is None and not clear_project
                else project_option
            )
            validate_project_label(project)
            raw_text, external_ref = read_correction_source(
                workspace, source_path=source_file
            )
            return _store_correction(
                workspace,
                target_id=target.id,
                raw_text=raw_text,
                occurred=occurred,
                project=project,
                external_ref=external_ref,
                artifacts=artifact_values,
            )

        if _noninteractive(controls):
            raise NonInteractiveInputRequired()

        raw_text = typer.prompt(
            "Self-contained correction text",
            err=True,
        )
        occurred_seed = target.occurred.model_dump_json()
        occurred_value = typer.prompt(
            "OccurredAt JSON (accept to copy the stored placement exactly)",
            default=occurred_seed,
            err=True,
        )
        if occurred_value == occurred_seed:
            occurred = target.occurred
        else:
            try:
                occurred = OccurredAt.model_validate_json(occurred_value)
            except (ValueError, TypeError) as cause:
                error = InvalidInputError()
                error.diagnostic_class = "invalid_time_shape"
                error.public_message = "The temporal shape is invalid."
                raise error from cause

        project_seed = json.dumps(target.project, ensure_ascii=False)
        project_value = typer.prompt(
            "Project/activity JSON (accept to copy the stored value exactly)",
            default=project_seed,
            err=True,
        )
        if project_value == project_seed:
            project = target.project
        else:
            try:
                parsed_project = json.loads(project_value)
            except (json.JSONDecodeError, TypeError) as cause:
                raise InvalidInputError() from cause
            if parsed_project is not None and not isinstance(parsed_project, str):
                raise InvalidInputError()
            project = parsed_project
        validate_project_label(project)

        return _store_correction(
            workspace,
            target_id=target.id,
            raw_text=raw_text,
            occurred=occurred,
            project=project,
            external_ref=None,
            artifacts=artifact_values,
        )

    _run_command(context, "correction add", operation)


def _store_correction(
    workspace: Path,
    *,
    target_id: str,
    raw_text: str,
    occurred: OccurredAt,
    project: str | None,
    external_ref: str | None,
    artifacts: tuple[str, ...],
) -> Outcome:
    """Commit one §14.4 correction and its §13.13 rebuild, however captured.

    Both capture forms reach this identical boundary after prompt or file
    input, flags, and source acquisition resolve, so the writer authority
    below covers exactly the capture-and-rebuild pair.
    """

    # §8.1: one writer authority covers the §13.13 rule 4 capture
    # boundary and the selected-lineage rebuild — no other business
    # writer can interleave between them. Capture input stays outside the lock.
    with writer_database(workspace, reconcile=True) as connection:
        try:
            captured = capture_correction(
                workspace,
                log_id=target_id,
                raw_text=raw_text,
                occurred=occurred,
                project=project,
                external_ref=external_ref,
                artifacts=artifacts,
                connection=connection,
            )
        except OperationCancelledError as error:
            committed = cast(
                CorrectionOutcome | None,
                getattr(error, "correction_outcome", None),
            )
            if committed is not None:
                try:
                    progress = record_cancelled_lifecycle(
                        connection, log_id=committed.raw_log.id
                    )
                except Exception:
                    progress = None
                if progress is not None:
                    extend_committed(error, run_ids=list(progress.run_ids))
                    error.lifecycle_result = progress
                # §14.14 rule 6: the committed capture is reported in
                # the cancelled envelope with its failed `13.13` run and
                # §14.12 retry.
                _decorate_lifecycle_error(
                    error,
                    base_affected=_correction_affected(committed),
                    base_generation_ids=committed.superseded_generation_ids,
                    base_invalidated_views=committed.invalidated_views,
                    base_invalidated_branches=committed.invalidated_branches,
                    base_residual_paths=committed.residual_paths,
                    retry=Retry(
                        command="exp2res recompute --log-id "
                        + shlex.quote(committed.raw_log.id)
                    ),
                )
            raise
        retry = Retry(
            command=f"exp2res recompute --log-id {shlex.quote(captured.raw_log.id)}"
        )
        try:
            recomputed = run_recompute(
                workspace, log_id=captured.raw_log.id, connection=connection
            )
        except Exp2ResError as error:
            _decorate_lifecycle_error(
                error,
                base_affected=_correction_affected(captured),
                base_generation_ids=captured.superseded_generation_ids,
                base_invalidated_views=captured.invalidated_views,
                base_invalidated_branches=captured.invalidated_branches,
                base_residual_paths=captured.residual_paths,
                retry=retry,
            )
            raise

    lifecycle = lifecycle_outcome(
        recomputed,
        base_invalidated_views=captured.invalidated_views,
        base_invalidated_branches=captured.invalidated_branches,
    )
    lifecycle.affected_ids = _merge_affected(
        _correction_affected(captured), lifecycle.affected_ids
    )
    lifecycle.generation_ids = sorted(
        {
            *captured.superseded_generation_ids,
            *lifecycle.generation_ids,
        },
        key=id_key,
    )
    lifecycle.residual_paths = sorted(
        {*captured.residual_paths, *lifecycle.residual_paths},
        key=os.fsencode,
    )
    lifecycle.human_result = (
        f"Stored correction {captured.raw_log.id}.\n" + lifecycle.human_result
    )
    return lifecycle


@app.command("recompute")
def recompute_command(
    context: typer.Context,
    log_id: str | None = typer.Option(None, "--log-id"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        validate_extract_selection(workspace, log_id=log_id)
        retry = Retry(
            command=(
                "exp2res recompute"
                if log_id is None
                else f"exp2res recompute --log-id {shlex.quote(log_id)}"
            )
        )
        try:
            recomputed = run_recompute(workspace, log_id=log_id)
        except Exp2ResError as error:
            _decorate_lifecycle_error(error, retry=retry)
            raise
        return lifecycle_outcome(recomputed)

    _run_command(context, "recompute", operation)


def _import_ending(error: Exp2ResError) -> str:
    return "cancelled" if isinstance(error, OperationCancelledError) else "failed"


def _import_decorate(error: Exp2ResError) -> None:
    """Report the committed records an ended import left behind.

    §14.14 rule 6 and §19.4 rule 4: each accepted record commits in its own
    transaction, so whatever ended the payload — a signal, a classified
    failure, a teardown fault — the records already committed are a lifecycle
    boundary this envelope reports. Every ending decorates here, so none of
    them reports less than the others.
    """

    committed = cast(ImportOutcome | None, getattr(error, "import_outcome", None))
    if committed is None:
        return
    extend_committed(
        error,
        affected_ids=import_created(committed),
        # Rule 5 nulls a result only when none exists yet; an interrupt after
        # the last record leaves a complete one.
        result=(
            import_result(committed)
            if getattr(error, "import_classified", False)
            else None
        ),
        # Human mode reads no `affected_ids`, so the same boundary is rendered
        # as the committed records' own lines.
        human_result="\n".join(
            [f"{_import_ending(error)}, {len(committed.accepted)} committed"]
            + [
                import_record_line(record, "accepted")
                for record in committed.accepted
            ]
        ),
    )


def _import_command(
    context: typer.Context, command: CommandPath, source_system: str, payload: str
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        try:
            imported = import_payload(
                workspace, source_system=source_system, payload_path=payload
            )
            try:
                return import_outcome(imported)
            except KeyboardInterrupt:
                # The classification is complete and durable; an interrupt in
                # result assembly still reports it under §14.14 rule 6.
                cancelled = OperationCancelledError()
                cancelled.import_outcome = imported
                cancelled.import_classified = True
                raise cancelled from None
        except Exp2ResError as error:
            try:
                _import_decorate(error)
            except KeyboardInterrupt:
                # The signal replaced the failure while its boundary was being
                # rendered. That boundary now belongs to the cancellation, and
                # rendering it there is the same bounded work.
                cancelled = OperationCancelledError()
                cancelled.import_outcome = getattr(error, "import_outcome", None)
                cancelled.import_classified = getattr(
                    error, "import_classified", False
                )
                _import_decorate(cancelled)
                raise cancelled from None
            raise

    _run_command(context, command, operation)


@import_app.command("ephemeris")
def import_ephemeris(
    context: typer.Context,
    payload: str = typer.Argument(..., metavar="PATH"),
) -> None:
    _import_command(context, "import ephemeris", "ephemeris", payload)


@import_app.command("atlas")
def import_atlas(
    context: typer.Context,
    payload: str = typer.Argument(..., metavar="PATH"),
) -> None:
    _import_command(context, "import atlas", "atlas", payload)


@import_app.command("github")
def import_github(
    context: typer.Context,
    payload: str = typer.Argument(..., metavar="PATH"),
) -> None:
    # §14.5 and §19.3: one local payload whose `repo` field names the
    # repository. Nothing here reaches the network.
    _import_command(context, "import github", "github", payload)


@import_app.command("file")
def import_file(
    context: typer.Context,
    source_path: str = typer.Argument(..., metavar="PATH"),
    project: str | None = typer.Option(None, "--project"),
) -> None:
    # §14.5: not a §19 record, so it goes through neither `_import_command`
    # nor §14.14 rule 5's `{counts, records}` schema. Unlisted there, it
    # reports `result = null` with its created IDs in `affected_ids`, exactly
    # as the §14.2 captures whose record shape it shares do.
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        return capture_outcome(
            import_design_document(
                workspace, source_path=source_path, project=project
            )
        )

    _run_command(context, "import file", operation)


@app.command("extract")
def extract_command(
    context: typer.Context,
    log_id: str | None = typer.Option(None, "--log-id"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: the selector must resolve before any adapter
        # construction. A mistyped selector therefore remains a class-2
        # refusal and cannot reach provider preflight.
        validate_extract_selection(workspace, log_id=log_id)
        try:
            extracted = run_extract(workspace, log_id=log_id)
        except Exp2ResError as error:
            _carry_stage(error, Stage3Result, stage3_outcome)
            raise
        return stage3_outcome(extracted)

    _run_command(context, "extract", operation)


def _fact_human_line(fact: ExperienceFact) -> str:
    return f"{fact.id}\t{fact.claim_kind}\t{fact.project or ''}\t{fact.confidence}"


def _occurred_human(occurred: OccurredAt) -> str:
    """Render a placement in the same shapes §14.3 accepts as input."""

    qualifier = f"({occurred.precision}, confidence {occurred.confidence})"
    if occurred.start is None:
        return f"unspecified {qualifier}"
    period = occurred.start.isoformat()
    if occurred.precision in {"date_range", "approximate_range"}:
        # `..` is §14.3's own open-ended end segment, so the rendering states
        # openness without implying a present-tense continuation (§16.7).
        period += "/" + (
            ".." if occurred.end is None else occurred.end.isoformat()
        )
    return f"{period} {qualifier}"


def _fact_human_block(fact: ExperienceFact) -> str:
    """§14.6's labeled rendering of every §11.4 field except `metadata`."""

    def optional(value: str | None) -> str:
        return "none" if value is None else value

    def listed(values: list[str]) -> str:
        return ", ".join(values) or "none"

    return "\n".join(
        [
            f"Fact {fact.id}",
            f"Created: {fact.created_at.isoformat()}",
            f"Claim: {fact.claim}",
            f"Claim kind: {fact.claim_kind}",
            f"Ownership level: {fact.ownership_level}",
            f"Context: {fact.context}",
            f"Project: {optional(fact.project)}",
            f"Role: {optional(fact.role)}",
            f"Company: {optional(fact.company)}",
            f"Action: {optional(fact.action)}",
            f"Object: {optional(fact.object)}",
            f"Outcome: {optional(fact.outcome)}",
            f"Skills: {listed(fact.skills)}",
            f"Technologies: {listed(fact.technologies)}",
            f"Themes: {listed(fact.themes)}",
            f"Occurred: {_occurred_human(fact.occurred)}",
            f"Confidence: {fact.confidence}",
            f"Source logs: {listed(fact.source_log_ids)}",
            f"Evidence items: {listed(fact.evidence_item_ids)}",
        ]
    )


@detections_app.command("generate")
def detections_generate(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: compatibility precedes adapter construction; this
        # command has no selector.
        require_compatible(workspace)
        try:
            generated = run_detections_generate(workspace)
        except Exp2ResError as error:
            _carry_stage(error, Stage4Result, detections_generate_outcome)
            raise
        return detections_generate_outcome(generated)

    _run_command(context, "detections generate", operation)


def _claim_human_line(claim: SelfClaim) -> str:
    return (
        f"{claim.id}\t{claim.claim_kind}\t{claim.dimension}\t"
        f"{claim.confidence}\t{claim.verification_status}"
    )


@assess_app.command("generate")
def assess_generate(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        require_compatible(workspace)
        try:
            generated = run_assess_generate(workspace)
        except Exp2ResError as error:
            _carry_stage(error, Stage6Result, assess_generate_outcome)
            raise
        return assess_generate_outcome(generated)

    _run_command(context, "assess generate", operation)


@assess_app.command("repair")
def assess_repair(
    context: typer.Context,
    snapshot_id: str = typer.Option(..., "--snapshot"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.9: no provider call or adapter construction; the §13.6
        # preconditions and swap run under the ordinary writer authority.
        try:
            repaired = run_assess_repair(workspace, snapshot_id=snapshot_id)
        except Exp2ResError as error:
            _carry_stage(error, Stage6Result, repair_outcome)
            raise
        return repair_outcome(repaired)

    _run_command(context, "assess repair", operation)


@assess_app.command("verify")
def assess_verify(
    context: typer.Context,
    snapshot_id: str = typer.Option(..., "--snapshot"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: resolve the selector before adapter construction so a
        # missing or superseded snapshot cannot reach provider preflight.
        require_compatible(workspace)
        with read_database(workspace) as connection:
            snapshot = get_assessment_snapshot(
                connection, snapshot_id, current_only=False
            )
        if snapshot is None:
            raise SelectorNotFoundError()
        if snapshot.superseded_at is not None:
            raise SnapshotNotCurrentError()

        try:
            verified = run_assess_verify(workspace, snapshot_id=snapshot_id)
        except Exp2ResError as error:
            _carry_stage(error, Stage7Result, assess_verify_outcome)
            raise
        if verified.snapshot_status in {"unsupported", "rejected"}:
            typer.echo(
                "Assessment verification completed, but assessment export is blocked.",
                err=True,
            )
        return assess_verify_outcome(verified)

    _run_command(context, "assess verify", operation)


@assess_app.command("list")
def assess_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        snapshots = list_current_snapshots(workspace)
        items = [
            SnapshotListItem(
                id=item.id,
                scope=item.scope,
                verification_status=item.verification_status,
                created_at=item.created_at,
            )
            for item in snapshots
        ]
        human = "\n".join(
            f"{item.id}\t{item.scope}\t"
            f"{item.verification_status}\t{item.created_at.isoformat()}"
            for item in items
        )
        return Outcome(
            result=AssessListResult(snapshots=items),
            human_result=human or "No current assessment snapshots.",
        )

    _run_command(context, "assess list", operation)


@assess_app.command("show")
def assess_show(
    context: typer.Context,
    snapshot_id: str = typer.Option(..., "--snapshot"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        details = show_snapshot(workspace, snapshot_id=snapshot_id)
        human = details.snapshot.title
        if details.claims:
            human += "\n" + "\n".join(
                _claim_human_line(claim) for claim in details.claims
            )
        return Outcome(
            result=AssessShowResult(
                snapshot=details.snapshot,
                claims=list(details.claims),
                gaps=list(details.gaps),
                contradictions=list(details.contradictions),
            ),
            human_result=human,
        )

    _run_command(context, "assess show", operation)


@export_app.command("assessment")
def export_assessment_command(
    context: typer.Context,
    snapshot_id: str = typer.Option(..., "--snapshot"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: resolve the selector read-only before the writer
        # path, so a missing or superseded snapshot reports class 2 even when
        # a managed-output residual would stop publication with class 8. The
        # export service re-validates the stored row under the writer lock.
        require_compatible(workspace)
        with read_database(workspace) as connection:
            snapshot = get_assessment_snapshot(
                connection, snapshot_id, current_only=False
            )
        if snapshot is None:
            raise SelectorNotFoundError()
        if snapshot.superseded_at is not None:
            raise SnapshotNotCurrentError()
        # §16.11 gate on the already-loaded row: a status-ineligible export is
        # class 10 before the writer path, so a managed residual cannot turn
        # the refusal into class 8. The service re-applies the gate under the
        # writer lock against the stored row.
        require_export_eligible(snapshot.verification_status)

        exported = export_assessment(workspace, snapshot_id=snapshot_id)
        result = AssessmentExportResult(
            manifest_path=exported.manifest_path,
            managed_paths=exported.managed_paths,
        )
        return Outcome(
            result=result,
            human_result="\n".join(
                [
                    result.manifest_path,
                    *(
                        path
                        for path in result.managed_paths
                        if path != result.manifest_path
                    ),
                ]
            ),
        )

    _run_command(context, "export assessment", operation)


@gaps_app.command("list")
def gaps_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        gaps = list(list_current_gaps(workspace))
        human = "\n".join(
            f"{gap.id}\t{gap.priority}\t{gap.reason}\t{str(gap.answered).lower()}"
            for gap in gaps
        )
        return Outcome(
            result=GapsListResult(gaps=gaps),
            human_result=human or "No current gaps.",
        )

    _run_command(context, "gaps list", operation)


@gaps_app.command("answer")
def gaps_answer(
    context: typer.Context,
    gap_id: str = typer.Option(..., "--gap-id"),
    source_file: str | None = typer.Option(None, "--file"),
    artifacts: list[str] | None = typer.Option(None, "--artifact"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        artifact_values = tuple(artifacts or ())
        validate_artifact_locator_count(artifact_values)
        # Resolve before file acquisition or prompt; capture re-checks under
        # the writer lock so this read-only validation cannot race the write.
        validate_gap_answer_selection(workspace, gap_id=gap_id)
        if source_file is not None:
            bundle = capture_gap_answer_file(
                workspace,
                gap_id=gap_id,
                source_path=source_file,
                artifacts=artifact_values,
            )
        else:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            # Fail closed on local-time configuration before owner input.
            workspace_zone(require_timezone(load_workspace_config(workspace)))
            raw_text = typer.prompt("Answer the gap question", err=True)
            bundle = capture_gap_answer(
                workspace,
                gap_id=gap_id,
                raw_text=raw_text,
                artifacts=artifact_values,
            )
        evidence_ids = [item.id for item in bundle.evidence_items]
        # §13/§14.7: capture removed every current snapshot's assessment set
        # after commit. It supersedes no snapshot and reports no §13.13 rule 9
        # regeneration command — the view needs re-export, not regeneration.
        return Outcome(
            affected_ids=AffectedIds(
                created=[
                    EntityIdGroup(
                        entity_type="evidence_item",
                        ids=sorted(
                            evidence_ids,
                            key=id_key,
                        ),
                    ),
                    EntityIdGroup(entity_type="raw_log", ids=[bundle.raw_log.id]),
                ],
                superseded=[],
                deleted=[],
            ),
            residual_paths=list(bundle.residual_paths),
            human_result=(
                f"Answered gap {gap_id} with raw log {bundle.raw_log.id}."
            ),
        )

    _run_command(context, "gaps answer", operation)


@contradictions_app.command("list")
def contradictions_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        contradictions = list(list_current_contradictions(workspace))
        human = "\n".join(
            f"{item.id}\t{item.title}\t{item.created_at.isoformat()}"
            for item in contradictions
        )
        return Outcome(
            result=ContradictionsResult(contradictions=contradictions),
            human_result=human or "No current contradictions.",
        )

    _run_command(context, "contradictions list", operation)


@contradictions_app.command("show")
def contradictions_show(
    context: typer.Context,
    contradiction_id: str = typer.Option(..., "--contradiction-id"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        contradiction = show_contradiction(
            workspace, contradiction_id=contradiction_id
        )
        return Outcome(
            result=ContradictionsResult(contradictions=[contradiction]),
            human_result=(
                f"{contradiction.id}\t{contradiction.title}\t"
                f"{contradiction.created_at.isoformat()}"
            ),
        )

    _run_command(context, "contradictions show", operation)


@facts_app.command("list")
def facts_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        facts = list(list_facts(workspace))
        return Outcome(
            result=FactsListResult(facts=facts),
            human_result=(
                "\n".join(_fact_human_line(fact) for fact in facts)
                or "No current facts."
            ),
        )

    _run_command(context, "facts list", operation)


@facts_app.command("show")
def facts_show(
    context: typer.Context,
    fact_id: str = typer.Option(..., "--fact-id"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        fact = show_fact(workspace, fact_id=fact_id)
        return Outcome(
            result=FactsListResult(facts=[fact]),
            human_result=_fact_human_block(fact),
        )

    _run_command(context, "facts show", operation)


@logs_app.command("list")
def logs_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        logs = list_logs(workspace)
        projections = [_log_projection(raw_log) for raw_log in logs]
        human = "\n".join(
            f"{item.id}\t{item.entry_type}\t{item.recorded_at.isoformat()}"
            for item in projections
        )
        return Outcome(
            result=LogsListResult(logs=projections),
            human_result=human or "No raw logs.",
        )

    _run_command(context, "logs list", operation)


@logs_app.command("show")
def logs_show(
    context: typer.Context,
    log_id: str = typer.Option(..., "--log-id"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        bundle = show_log(workspace, log_id=log_id)
        result = LogsShowResult(
            log=_selected_log_projection(bundle.raw_log),
            evidence_items=[
                _evidence_projection(item)
                for item in bundle.evidence_items
            ],
        )
        projection = json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        return Outcome(
            result=result,
            human_result=f"{projection}\n{bundle.raw_log.raw_text}",
        )

    _run_command(context, "logs show", operation)


def _delete_result(deleted: DeleteOutcome) -> LogsDeleteResult:
    return LogsDeleteResult(
        selected_log=_selected_log_projection(deleted.selected_log)
    )


def _delete_affected(deleted: DeleteOutcome) -> AffectedIds:
    classes = (
        ("evidence_item", deleted.evidence_item_ids),
        ("experience_fact", deleted.purged_fact_ids),
        ("gap_question", deleted.purged_gap_ids),
        ("contradiction", deleted.purged_contradiction_ids),
        ("verification_finding", deleted.purged_finding_ids),
        ("resume_bullet", deleted.purged_bullet_ids),
        ("resume_branch", deleted.purged_branch_ids),
        ("self_claim", deleted.purged_claim_ids),
        ("assessment_snapshot", deleted.purged_snapshot_ids),
        ("raw_log", (deleted.selected_log.id,)),
    )
    return AffectedIds(
        created=[],
        superseded=[],
        deleted=[
            EntityIdGroup(entity_type=entity_type, ids=list(ids))
            for entity_type, ids in classes
            if ids
        ],
    )


@logs_app.command("delete")
def logs_delete(
    context: typer.Context,
    log_id: str = typer.Option(..., "--log-id"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        selected = show_log(workspace, log_id=log_id).raw_log
        if not controls.yes:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            # §14.14 rule 3: one confirmation names both halves — the
            # destructive purge and the cost-bearing §13.13 rule 5 rebuild
            # that may call the configured model provider.
            if not typer.confirm(
                f"Delete raw log {selected.id} and rebuild derived state "
                "with the configured model provider?",
                err=True,
            ):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        # §8.1: one owner-delete writer authority covers the committed
        # privacy purge and its §13.13 rule 5 rebuild — no other business
        # writer can interleave between them.
        with writer_database(
            workspace, owner_delete=True, reconcile=True
        ) as connection:
            retry = Retry(command="exp2res recompute")
            try:
                deleted = delete_log(
                    workspace, log_id=log_id, connection=connection
                )
            except OperationCancelledError as error:
                committed = cast(
                    DeleteOutcome | None,
                    getattr(error, "delete_outcome", None),
                )
                if committed is not None:
                    try:
                        progress = record_cancelled_lifecycle(
                            connection, log_id=None
                        )
                    except Exception:
                        progress = None
                    if progress is not None:
                        extend_committed(error, run_ids=list(progress.run_ids))
                        error.lifecycle_result = progress
                    _decorate_lifecycle_error(
                        error,
                        base_affected=_delete_affected(committed),
                        base_generation_ids=committed.purged_generation_ids,
                        base_invalidated_views=committed.invalidated_views,
                        base_invalidated_branches=committed.invalidated_branches,
                        base_residual_paths=committed.residual_paths,
                        retry=retry,
                        result=_delete_result(committed),
                    )
                raise
            result = _delete_result(deleted)
            base_affected = _delete_affected(deleted)
            try:
                recomputed = run_recompute(
                    workspace, log_id=None, connection=connection
                )
            except Exp2ResError as error:
                _decorate_lifecycle_error(
                    error,
                    base_affected=base_affected,
                    base_generation_ids=deleted.purged_generation_ids,
                    base_invalidated_views=deleted.invalidated_views,
                    base_invalidated_branches=deleted.invalidated_branches,
                    base_residual_paths=deleted.residual_paths,
                    retry=retry,
                    result=result,
                )
                raise
        lifecycle = lifecycle_outcome(recomputed)
        exit_code = 8 if deleted.residual_paths or lifecycle.residual_paths else 0
        return Outcome(
            exit_code=exit_code,
            diagnostic_class="deletion_incomplete" if exit_code else None,
            affected_ids=_merge_affected(base_affected, lifecycle.affected_ids),
            generation_ids=sorted(
                {*deleted.purged_generation_ids, *lifecycle.generation_ids},
                key=id_key,
            ),
            run_ids=lifecycle.run_ids,
            residual_paths=sorted(
                {*deleted.residual_paths, *lifecycle.residual_paths},
                key=os.fsencode,
            ),
            invalidated_views=merged_invalidated_views(
                deleted.invalidated_views, lifecycle.invalidated_views
            ),
            invalidated_branches=merged_invalidated_branches(
                deleted.invalidated_branches, lifecycle.invalidated_branches
            ),
            warnings=lifecycle.warnings,
            result=result,
            human_result=(
                f"Deleted raw log {deleted.selected_log.id}; rebuilt through Stage 5."
            ),
        )

    _run_command(context, "logs delete", operation)


@jd_app.command("add")
def jd_add(
    context: typer.Context,
    source_path: str = typer.Argument(..., metavar="PATH"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: compatibility precedes source acquisition and adapter
        # construction; the service re-checks under its own authority.
        require_compatible(workspace)
        parsed = run_jd_add_file(workspace, source_path=source_path)
        try:
            return jd_add_outcome(parsed)
        except KeyboardInterrupt:
            # The service returned a durable parse; §14.14 rule 6 keeps it
            # reported even when the interrupt lands in result assembly.
            cancelled = OperationCancelledError()
            carry_committed(cancelled, jd_add_outcome(parsed))
            raise cancelled from None

    _run_command(context, "jd add", operation)


@jd_app.command("list")
def jd_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        job_descriptions = list(list_job_descriptions(workspace))
        human = "\n".join(
            "\t".join(
                (
                    item.id,
                    item.created_at.isoformat(),
                    # §16.13 permits a tab or newline inside parsed source
                    # text; unescaped, one record could pose as several.
                    render_text(item.title) if item.title else "-",
                    render_text(item.company) if item.company else "-",
                )
            )
            for item in job_descriptions
        )
        return Outcome(
            result=JdListResult(
                job_descriptions=[
                    job_description_projection(item) for item in job_descriptions
                ]
            ),
            human_result=human or "No job descriptions.",
        )

    _run_command(context, "jd list", operation)


def _jd_cleanup_result(cleaned: JobDescriptionCleanupOutcome) -> JdDeleteResult:
    return JdDeleteResult(
        selected_job_description=job_description_projection(cleaned.selected),
        purged_branches=[],
        removed_managed_paths=[
            render_path(path) for path in cleaned.removed_managed_paths
        ],
    )


def _jd_cleanup_human_result(cleaned: JobDescriptionCleanupOutcome) -> str:
    lines = [
        f"Job description {cleaned.selected.id} was not deleted; managed "
        "cleanup had already run."
    ]
    lines.extend(
        f"Removed managed path: {path}"
        for path in _jd_cleanup_result(cleaned).removed_managed_paths
    )
    return "\n".join(lines)


@jd_app.command("delete")
def jd_delete(
    context: typer.Context,
    job_description_id: str = typer.Option(..., "--jd"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        # §13.13 rule 10 captures the selected projection first, and an
        # unknown selector must fail before the writer preamble can touch
        # managed output. The service revalidates it under the writer lock,
        # so this read cannot race the deletion.
        selected = show_job_description(
            workspace, job_description_id=job_description_id
        )
        if not controls.yes:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            if not typer.confirm(
                f"Delete job description {selected.id} and every "
                "verified bullet pack derived from it?",
                err=True,
            ):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        try:
            deleted = delete_job_description(
                workspace, job_description_id=job_description_id
            )
        except OperationCancelledError as error:
            committed = cast(
                JobDescriptionDeleteOutcome | None,
                getattr(error, "delete_outcome", None),
            )
            if committed is not None:
                # The error's own class-9 classification replaces the
                # projection's on emission, so carrying it whole is safe.
                carry_committed(error, jd_delete_outcome(committed))
                raise
            # §14.14 rule 6: managed cleanup ran before the transaction, so
            # its effects are reported even though nothing was deleted — no
            # affected IDs and no run, because neither became durable.
            cleaned = cast(
                JobDescriptionCleanupOutcome | None,
                getattr(error, "cleanup_outcome", None),
            )
            if cleaned is not None:
                extend_committed(
                    error,
                    residual_paths=list(cleaned.residual_paths),
                    result=_jd_cleanup_result(cleaned),
                    human_result=_jd_cleanup_human_result(cleaned),
                )
            raise
        try:
            return jd_delete_outcome(deleted)
        except KeyboardInterrupt:
            # The service returned a durable deletion; §14.14 rule 6 keeps it
            # reported even when the interrupt lands in result assembly.
            cancelled = OperationCancelledError()
            carry_committed(cancelled, jd_delete_outcome(deleted))
            raise cancelled from None

    _run_command(context, "jd delete", operation)


@bullets_app.command("generate")
def bullets_generate(
    context: typer.Context,
    job_description_id: str = typer.Option(..., "--jd"),
    snapshot_id: str = typer.Option(..., "--snapshot"),
    branch_name: str = typer.Option(..., "--branch"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: compatibility precedes adapter construction; the
        # service re-checks under its own authority.
        require_compatible(workspace)
        try:
            generated = run_bullets_generate(
                workspace,
                job_description_id=job_description_id,
                snapshot_id=snapshot_id,
                branch_name=branch_name,
            )
        except Exp2ResError as error:
            _carry_stage(error, Stage10Result, bullets_generate_outcome)
            raise
        try:
            return bullets_generate_outcome(generated)
        except KeyboardInterrupt:
            # The service returned a durable swap; §14.14 rule 6 keeps it
            # reported even when the interrupt lands in result assembly.
            cancelled = OperationCancelledError()
            cancelled.stage_result = generated
            _carry_stage(cancelled, Stage10Result, bullets_generate_outcome)
            raise cancelled from None

    _run_command(context, "bullets generate", operation)


@bullets_app.command("verify")
def bullets_verify(
    context: typer.Context,
    branch_name: str = typer.Option(..., "--branch"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: compatibility precedes adapter construction; the
        # service re-checks under its own authority.
        require_compatible(workspace)
        try:
            verified = run_bullets_verify(workspace, branch_name=branch_name)
        except Exp2ResError as error:
            _carry_stage(error, Stage11Result, bullets_verify_outcome)
            raise
        try:
            if verified.export_blocked:
                typer.echo(
                    "Bullet verification completed, but bullet-pack export is blocked.",
                    err=True,
                )
            return bullets_verify_outcome(verified)
        except KeyboardInterrupt:
            # The service returned a durable pass; §14.14 rule 6 keeps it
            # reported even when the interrupt lands in result assembly, which
            # for a whole-branch finding set is not a negligible window.
            cancelled = OperationCancelledError()
            cancelled.stage_result = verified
            _carry_stage(cancelled, Stage11Result, bullets_verify_outcome)
            raise cancelled from None

    _run_command(context, "bullets verify", operation)


@bullets_app.command("export")
def bullets_export(
    context: typer.Context,
    branch_name: str = typer.Option(..., "--branch"),
) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        # §14.14 rule 3: resolve the selector read-only before the writer path,
        # so an unknown branch reports class 2 even where a managed-output
        # residual would stop publication with class 8. The export service
        # re-resolves and re-validates the stored rows under the writer lock.
        require_compatible(workspace)
        validated = validated_branch_name(branch_name)
        with read_database(workspace) as connection:
            selected = current_branch_by_folded_name(connection, validated)
        if selected is None:
            raise SelectorNotFoundError()

        exported = export_bullet_pack(workspace, branch_name=branch_name)
        result = AssessmentExportResult(
            manifest_path=exported.manifest_path,
            managed_paths=exported.managed_paths,
        )
        return Outcome(
            result=result,
            human_result="\n".join(
                [
                    result.manifest_path,
                    *(
                        path
                        for path in result.managed_paths
                        if path != result.manifest_path
                    ),
                ]
            ),
        )

    _run_command(context, "bullets export", operation)


@workspace_app.command("purge")
def workspace_purge(
    context: typer.Context,
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        if not (controls.yes or yes):
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            if not typer.confirm(
                "Purge all Exp2Res-managed data from this workspace?",
                err=True,
            ):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        try:
            purged = purge_workspace(workspace)
        except OperationCancelledError as error:
            committed = cast(
                PurgeOutcome | None,
                getattr(error, "purge_outcome", None),
            )
            if committed is not None:
                carry_committed(error, purge_outcome(committed))
            raise
        try:
            return purge_outcome(purged)
        except KeyboardInterrupt:
            # The service returned a durable purge; §14.14 rule 6 keeps it
            # reported even when the interrupt lands in result assembly.
            cancelled = OperationCancelledError()
            carry_committed(cancelled, purge_outcome(purged))
            raise cancelled from None

    _run_command(context, "workspace purge", operation)


class _ServeCancellation:
    """The interrupt disposition for one `view serve` run, start to finish.

    `view serve` is the one §14 form that runs until it is interrupted, so
    the default handler's `KeyboardInterrupt` would unwind at whichever
    bytecode it landed on instead of starting the one absolute drain deadline
    at the interruption instant. `ViewServer.interrupt` is a state change that
    never raises and is safe from a handler at any instant: the first call
    drains, the second forces the close, and both keep the §14.14 rule 6
    class-9 envelope the command returns either way.

    This exists as an object rather than a closure because it is installed
    before there is a server to interrupt. Workspace discovery, the bind
    validation, and a §12.14 compatibility read that may block on a busy
    workspace are all interruptible, and each one left to the default handler
    is a window where the run enters envelope assembly with no handler
    installed — where a second interrupt raises past every catch in the
    runtime. Before `adopt`, the first interrupt asks the runtime to abort
    active startup work; later interrupts are absorbed so they cannot tear
    down envelope assembly. After `adopt`, every call reaches the server.

    Nothing is restored here. `_run_command` hands `SIGINT` back to whoever
    held it only once the envelope is written, because the second interrupt
    this absorbs is exactly what an impatient owner sends while the first
    drain is finishing.
    """

    def __init__(self) -> None:
        self._server: ViewServer | None = None
        self._pending = 0

    def __call__(self, _signum: int, _frame: object) -> bool:
        server = self._server
        if server is not None:
            server.interrupt()
            return False
        self._pending += 1
        return self._pending == 1

    def adopt(self, server: ViewServer) -> None:
        """Hand every later interrupt to the server."""

        self._server = server
        self._pending = 0


def _require_interruptible() -> None:
    """Refuse to serve where no interrupt could ever end it.

    Python delivers every signal handler on the main thread, and only the
    main thread may install one. Serving from any other thread would
    therefore block on `accept` with no path to §14.14 rule 6's class-9
    envelope at all: the interrupt would reach the main thread, which is
    somewhere else entirely. This runs before the bind, so the refusal costs
    no socket — it is a defect in the embedding caller, not owner input, and
    it takes the ordinary internal class rather than an invented one.
    """

    if threading.current_thread() is not threading.main_thread():
        error = Exp2ResError()
        error.exit_code = 1
        error.diagnostic_class = "internal_error"
        error.public_message = (
            "view serve must run on the main thread, which is the only place "
            "an interrupt can end it."
        )
        raise error


def _progress_reporter(controls: Controls) -> Callable[[str, str | None], None]:
    """Bind §14.17's progress lines to the stream this invocation owns.

    The sink is captured now rather than resolved at write time. §14.17's
    cancellation may not wait on a wedged reporter, so a line can still be
    written after the command returned; resolving `sys.stderr` then would
    send it wherever the process points next — under an embedding host or a
    test runner, into a later invocation's output. Writing to the captured
    stream instead means a late line reaches this command's own stderr or,
    once that stream is gone, nothing at all.

    A line carries only the closed outcome class and, when the request named
    one, the closed route literal: §30 rule 6 keeps request bytes out of
    every diagnostic. `--quiet` suppresses these per-request lines; §14.17
    exempts the startup URLs, which the command writes itself.
    """

    stream = sys.stderr

    def report(outcome: str, route: str | None) -> None:
        if controls.quiet:
            return
        typer.echo(outcome if route is None else f"{route} {outcome}", file=stream)

    return report


@view_app.command("serve")
def view_serve(
    context: typer.Context,
    host: str = typer.Option(DEFAULT_HOST, "--host"),
    port: int = typer.Option(DEFAULT_PORT, "--port"),
) -> None:
    cancellation = _ServeCancellation()

    def operation(workspace: Path, controls: Controls) -> Outcome:
        _require_interruptible()
        bind = validate_bind(host, port)
        require_compatible(workspace, require_managed_root=False)

        report = _progress_reporter(controls)
        server = ViewServer(workspace, bind, report=report)
        cancellation.adopt(server)
        server.open()
        stream = sys.stderr
        server.advertise(lambda url: typer.echo(url, file=stream))
        server.serve()
        return Outcome(exit_code=9, diagnostic_class="cancelled")

    _run_command(context, "view serve", operation, interrupt=cancellation)


def _parse_error_envelope(json_output: bool, diagnostic: str, message: str) -> None:
    envelope = CLIEnvelope(
        command=None,
        status="failed",
        exit_code=2,
        diagnostic_class=diagnostic,
        workspace=None,
        affected_ids=_empty_affected(),
        generation_ids=[],
        run_ids=[],
        invalidated_views=[],
        invalidated_branches=[],
        findings=[],
        residual_paths=[],
        warnings=[],
        retry=None,
        result=None,
    )
    typer.echo(message, err=True)
    if json_output:
        typer.echo(
            json.dumps(
                envelope.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def main() -> None:
    try:
        result = app(standalone_mode=False)
        if isinstance(result, int) and result:
            raise SystemExit(result)
    except Exit as error:
        raise SystemExit(error.exit_code)
    except UsageError:
        _parse_error_envelope("--json" in sys.argv, "invalid_usage", "Invalid command usage.")
        raise SystemExit(2)
    except Abort:
        raise SystemExit(9)


__all__ = ["app", "main"]
