"""Typer CLI implementing the available §22 Phase 0 command surface."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Callable, cast

import typer

try:
    from typer._click.exceptions import Abort, Exit, UsageError
except ImportError:  # typer releases that depend on an external click
    from click.exceptions import Abort, Exit, UsageError

from exp2res.config import load_workspace_config, require_timezone
from exp2res.domain.enums import TemporalConfidence, TemporalPrecision
from exp2res.domain.results import (
    AffectedIds,
    CLIEnvelope,
    CommandPath,
    EntityIdGroup,
    LogProjection,
    LogsDeleteResult,
    LogsListResult,
    SchemaProjection,
    SchemaResult,
    SelectedLogProjection,
)
from exp2res.errors import (
    Exp2ResError,
    MigrationFailedError,
    NonInteractiveInputRequired,
    OperationDeferredError,
)
from exp2res.services.capture import (
    capture_daily,
    capture_daily_file,
    capture_retro,
)
from exp2res.services.logs import delete_log, list_logs, show_log
from exp2res.services.time_input import parse_occurred, workspace_zone
from exp2res.storage.workspace import (
    SchemaStatus,
    discover_workspace,
    initialize_workspace,
    inspect_workspace,
    migrate_workspace,
    require_compatible,
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
app.add_typer(db_app, name="db")
app.add_typer(log_app, name="log")
app.add_typer(logs_app, name="logs")
app.add_typer(correction_app, name="correction")


@dataclass(frozen=True)
class Controls:
    json_output: bool
    yes: bool
    no_input: bool
    workspace_override: str | None
    verbose: bool
    quiet: bool


@dataclass
class Outcome:
    exit_code: int = 0
    diagnostic_class: str | None = None
    affected_ids: AffectedIds = field(default_factory=AffectedIds)
    residual_paths: list[str] = field(default_factory=list)
    result: SchemaResult | LogsListResult | LogsDeleteResult | None = None
    human_result: str = ""


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


def _run_command(
    context: typer.Context,
    command: CommandPath,
    operation: Callable[[Path, Controls], Outcome],
    *,
    init_command: bool = False,
) -> None:
    controls = cast(Controls, context.obj)
    workspace: Path | None = None
    try:
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
        outcome = operation(workspace, controls)
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
        outcome = Outcome(
            exit_code=error.exit_code,
            diagnostic_class=error.diagnostic_class,
        )
        typer.echo(error.public_message, err=True)
    except Exception:
        outcome = Outcome(exit_code=1, diagnostic_class="internal_error")
        typer.echo("The operation failed unexpectedly.", err=True)

    envelope = CLIEnvelope(
        command=command,
        status=cast(object, _status_for(outcome.exit_code)),
        exit_code=outcome.exit_code,
        diagnostic_class=outcome.diagnostic_class,
        workspace=str(workspace) if workspace is not None else None,
        affected_ids=outcome.affected_ids,
        generation_ids=[],
        run_ids=[],
        invalidated_views=[],
        invalidated_branches=[],
        findings=[],
        residual_paths=outcome.residual_paths,
        warnings=[],
        retry=None,
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
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        status = inspect_workspace(workspace)
        if status.compatible:
            return Outcome(
                result=_schema_result(status),
                human_result="No migration is required.",
            )
        if status.migration_path_available:
            migrated = migrate_workspace(workspace)
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


def _capture_outcome(bundle) -> Outcome:
    evidence_ids = [item.id for item in bundle.evidence_items]
    return Outcome(
        affected_ids=AffectedIds(
            created=[
                EntityIdGroup(entity_type="evidence_item", ids=evidence_ids),
                EntityIdGroup(entity_type="raw_log", ids=[bundle.raw_log.id]),
            ],
            superseded=[],
            deleted=[],
        ),
        human_result=(
            f"Created raw log {bundle.raw_log.id} with evidence {evidence_ids[0]}."
        ),
    )


@log_app.command("today")
def log_today(
    context: typer.Context,
    project: str | None = typer.Option(None, "--project"),
    source_file: str | None = typer.Option(None, "--file"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        if source_file is not None:
            return _capture_outcome(
                capture_daily_file(
                    workspace, source_path=source_file, project=project
                )
            )
        if _noninteractive(controls):
            raise NonInteractiveInputRequired()
        require_compatible(workspace)
        # Fail closed on the local-time contract before collecting owner text.
        workspace_zone(require_timezone(load_workspace_config(workspace)))
        raw_text = typer.prompt("Describe what happened", err=True)
        return _capture_outcome(
            capture_daily(workspace, raw_text=raw_text, project=project)
        )

    _run_command(context, "log today", operation)


@log_app.command("retro")
def log_retro(context: typer.Context) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        if _noninteractive(controls):
            raise NonInteractiveInputRequired()
        require_compatible(workspace)
        # Fail closed on the local-time contract before collecting owner text.
        timezone_name = require_timezone(load_workspace_config(workspace))
        workspace_zone(timezone_name)
        period = typer.prompt("What period are we reconstructing?", err=True)
        precision_value = typer.prompt("How precise is this?", err=True)
        confidence_value = typer.prompt("How confident are you?", err=True)
        project = typer.prompt("Project/activity?", default="", err=True) or None
        raw_text = typer.prompt("Describe what you remember.", err=True)
        occurred = parse_occurred(
            period=period,
            precision=cast(TemporalPrecision, precision_value),
            confidence=cast(TemporalConfidence, confidence_value),
            timezone_name=timezone_name,
        )
        return _capture_outcome(
            capture_retro(
                workspace, occurred=occurred, raw_text=raw_text, project=project
            )
        )

    _run_command(context, "log retro", operation)


@correction_app.command("add")
def correction_add(
    context: typer.Context,
    log_id: str = typer.Option(..., "--log-id"),
) -> None:
    def operation(_workspace: Path, _controls: Controls) -> Outcome:
        _ = log_id
        raise OperationDeferredError()

    _run_command(context, "correction add", operation)


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
            if not typer.confirm(f"Delete raw log {selected.id}?", err=True):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        deleted = delete_log(workspace, log_id=log_id)
        result = LogsDeleteResult(
            selected_log=SelectedLogProjection(
                **_log_projection(deleted.selected_log).model_dump(),
                external_ref=deleted.selected_log.external_ref,
            )
        )
        exit_code = 8 if deleted.residual_paths else 0
        return Outcome(
            exit_code=exit_code,
            diagnostic_class="deletion_incomplete" if exit_code else None,
            affected_ids=AffectedIds(
                created=[],
                superseded=[],
                deleted=[
                    EntityIdGroup(
                        entity_type="evidence_item",
                        ids=list(deleted.evidence_item_ids),
                    ),
                    EntityIdGroup(entity_type="raw_log", ids=[deleted.selected_log.id]),
                ],
            ),
            residual_paths=list(deleted.residual_paths),
            result=result,
            human_result=(
                f"Deleted raw log {deleted.selected_log.id}."
                if not deleted.residual_paths
                else f"Deleted raw log {deleted.selected_log.id}; cleanup is incomplete."
            ),
        )

    _run_command(context, "logs delete", operation)


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
