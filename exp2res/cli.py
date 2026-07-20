"""Typer CLI implementing the available §22 command surface."""

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
from exp2res.domain.models import ExperienceFact, SelfClaim, SelfSignal
from exp2res.domain.results import (
    AffectedIds,
    AssessListResult,
    AssessShowResult,
    CLIEnvelope,
    CommandPath,
    ContradictionsResult,
    DetectionsGenerateResult,
    EntityIdGroup,
    FactsListResult,
    GapsListResult,
    InvalidatedView,
    LogProjection,
    LogsDeleteResult,
    LogsListResult,
    SchemaProjection,
    SchemaResult,
    SelectedLogProjection,
    SignalsListResult,
    SnapshotListItem,
)
from exp2res.errors import (
    Exp2ResError,
    MigrationFailedError,
    MigrationInterrupted,
    NonInteractiveInputRequired,
    OperationDeferredError,
)
from exp2res.llm.contracts import ContractWarning
from exp2res.services.capture import (
    capture_daily,
    capture_daily_file,
    capture_gap_answer,
    capture_gap_answer_file,
    capture_retro,
    validate_gap_answer_selection,
    validate_project_label,
)
from exp2res.services.assessment import (
    list_current_snapshots,
    run_assess_generate,
    show_snapshot,
    validate_assessment_selection,
)
from exp2res.services.detection import (
    list_current_contradictions,
    list_current_gaps,
    run_detections_generate,
    show_contradiction,
)
from exp2res.services.extraction import run_extract, validate_extract_selection
from exp2res.services.facts import list_facts, show_fact
from exp2res.services.logs import delete_log, list_logs, show_log
from exp2res.services.signals import list_current_signals, run_signals_generate
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
facts_app = typer.Typer(help="Inspect current extracted facts.")
detections_app = typer.Typer(
    help="Generate both complete current detection sets together."
)
gaps_app = typer.Typer(help="Inspect and answer current gap questions.")
contradictions_app = typer.Typer(
    help="Inspect current contradictions and immutable contradiction history."
)
signals_app = typer.Typer(help="Generate and inspect current self-signals.")
assess_app = typer.Typer(help="Generate and inspect self-assessment views.")
app.add_typer(db_app, name="db")
app.add_typer(log_app, name="log")
app.add_typer(logs_app, name="logs")
app.add_typer(correction_app, name="correction")
app.add_typer(facts_app, name="facts")
app.add_typer(detections_app, name="detections")
app.add_typer(gaps_app, name="gaps")
app.add_typer(contradictions_app, name="contradictions")
app.add_typer(signals_app, name="signals")
app.add_typer(assess_app, name="assess")


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
    generation_ids: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    invalidated_views: list[InvalidatedView] = field(default_factory=list)
    residual_paths: list[str] = field(default_factory=list)
    warnings: list[ContractWarning] = field(default_factory=list)
    result: (
        SchemaResult
        | LogsListResult
        | LogsDeleteResult
        | FactsListResult
        | DetectionsGenerateResult
        | GapsListResult
        | ContradictionsResult
        | SignalsListResult
        | AssessListResult
        | AssessShowResult
        | None
    ) = None
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
            # §14.14 rule 5: a failed §15 invocation still reports the
            # committed processing runs it created (LLMInvocationError
            # carries them; other error classes leave the default empty).
            run_ids=list(getattr(error, "run_ids", ()) or ()),
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
        generation_ids=outcome.generation_ids,
        run_ids=outcome.run_ids,
        invalidated_views=outcome.invalidated_views,
        invalidated_branches=[],
        findings=[],
        residual_paths=outcome.residual_paths,
        warnings=outcome.warnings,
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
        validate_project_label(project)
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


@app.command("extract")
def extract_command(
    context: typer.Context,
    log_id: str | None = typer.Option(None, "--log-id"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        # §14.14 rule 3, in `logs delete` order: the selector must resolve
        # before consent is requested and before any adapter construction.
        validate_extract_selection(workspace, log_id=log_id)
        # §14.14 rule 3: extraction is cost-bearing — explicit --yes when
        # non-interactive, a TTY confirmation otherwise.
        if not controls.yes:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            if not typer.confirm(
                "Run fact extraction with the configured model provider?",
                err=True,
            ):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        extracted = run_extract(workspace, log_id=log_id)
        created = list(extracted.created)
        superseded = list(extracted.superseded)
        superseded_groups: list[EntityIdGroup] = []
        if superseded:
            superseded_groups.append(
                EntityIdGroup(entity_type="experience_fact", ids=superseded)
            )
        if extracted.superseded_gap_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="gap_question",
                    ids=list(extracted.superseded_gap_ids),
                )
            )
        if extracted.superseded_contradiction_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="contradiction",
                    ids=list(extracted.superseded_contradiction_ids),
                )
            )
        if extracted.superseded_signal_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="self_signal",
                    ids=list(extracted.superseded_signal_ids),
                )
            )
        if extracted.superseded_claim_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="self_claim",
                    ids=list(extracted.superseded_claim_ids),
                )
            )
        if extracted.superseded_snapshot_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="assessment_snapshot",
                    ids=list(extracted.superseded_snapshot_ids),
                )
            )
        invalidated_views = list(extracted.invalidated_views)
        view_lines = "\n".join(
            f"Invalidated {item.snapshot_id}: {item.regeneration_command}"
            for item in invalidated_views
        )
        return Outcome(
            affected_ids=AffectedIds(
                created=(
                    [EntityIdGroup(entity_type="experience_fact", ids=created)]
                    if created
                    else []
                ),
                superseded=superseded_groups,
                deleted=[],
            ),
            # §14.14 rule 5: produced OR invalidated generation IDs,
            # duplicate-free and deterministically ordered.
            generation_ids=sorted(
                {*extracted.generation_ids, *extracted.superseded_generation_ids},
                key=lambda value: value.encode("utf-8"),
            ),
            run_ids=[extracted.run_id],
            invalidated_views=invalidated_views,
            warnings=list(extracted.warnings),
            human_result=(
                f"Extracted {len(created)} facts ({len(superseded)} superseded)."
                + (f"\n{view_lines}" if view_lines else "")
            ),
        )

    _run_command(context, "extract", operation)


def _fact_human_line(fact: ExperienceFact) -> str:
    return f"{fact.id}\t{fact.claim_kind}\t{fact.project or ''}\t{fact.confidence}"


def _detection_groups(
    gap_ids: list[str], contradiction_ids: list[str]
) -> list[EntityIdGroup]:
    gap_ids = sorted(set(gap_ids), key=lambda value: value.encode("utf-8"))
    contradiction_ids = sorted(
        set(contradiction_ids), key=lambda value: value.encode("utf-8")
    )
    groups: list[EntityIdGroup] = []
    if gap_ids:
        groups.append(EntityIdGroup(entity_type="gap_question", ids=gap_ids))
    if contradiction_ids:
        groups.append(
            EntityIdGroup(entity_type="contradiction", ids=contradiction_ids)
        )
    return groups


@detections_app.command("generate")
def detections_generate(context: typer.Context) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        # §14.14 rule 3: compatibility precedes consent; this command has no
        # selector, and adapter construction follows cost consent.
        require_compatible(workspace)
        if not controls.yes:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            if not typer.confirm(
                "Replace both complete detection sets using the configured model provider?",
                err=True,
            ):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        generated = run_detections_generate(workspace)
        gaps = list(generated.current_gaps)
        contradictions = list(generated.current_contradictions)
        created_gap_ids = list(generated.created_gap_ids)
        created_contradiction_ids = list(generated.created_contradiction_ids)
        superseded_gap_ids = list(generated.superseded_gap_ids)
        superseded_contradiction_ids = list(
            generated.superseded_contradiction_ids
        )
        superseded_groups = _detection_groups(
            superseded_gap_ids, superseded_contradiction_ids
        )
        if generated.superseded_signal_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="self_signal",
                    ids=list(generated.superseded_signal_ids),
                )
            )
        if generated.superseded_claim_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="self_claim",
                    ids=list(generated.superseded_claim_ids),
                )
            )
        if generated.superseded_snapshot_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="assessment_snapshot",
                    ids=list(generated.superseded_snapshot_ids),
                )
            )
        invalidated_views = list(generated.invalidated_views)
        if generated.retained:
            human = "Retained the current detection generation unchanged."
        else:
            invalidated = (
                ", ".join(group.entity_type for group in superseded_groups)
                or "none"
            )
            human = (
                "Replaced both complete detection sets. "
                f"Current gaps ({len(gaps)}): "
                f"{', '.join(gap.id for gap in gaps) or 'none'}. "
                f"Current contradictions ({len(contradictions)}): "
                f"{', '.join(item.id for item in contradictions) or 'none'}. "
                f"Invalidated artifact classes: {invalidated}."
            )
            if invalidated_views:
                human += "\n" + "\n".join(
                    f"Invalidated {item.snapshot_id}: {item.regeneration_command}"
                    for item in invalidated_views
                )
        return Outcome(
            affected_ids=AffectedIds(
                created=_detection_groups(
                    created_gap_ids, created_contradiction_ids
                ),
                superseded=superseded_groups,
                deleted=[],
            ),
            generation_ids=sorted(
                {
                    *(
                        [generated.generation_id]
                        if generated.generation_id is not None
                        else []
                    ),
                    *generated.superseded_generation_ids,
                },
                key=lambda value: value.encode("utf-8"),
            ),
            run_ids=[generated.run_id],
            warnings=list(generated.warnings),
            invalidated_views=invalidated_views,
            result=DetectionsGenerateResult(
                gaps=gaps,
                contradictions=contradictions,
            ),
            human_result=human,
        )

    _run_command(context, "detections generate", operation)


def _signal_human_line(signal: SelfSignal) -> str:
    return f"{signal.id}\t{signal.signal_type}\t{signal.confidence}"


@signals_app.command("generate")
def signals_generate(context: typer.Context) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        require_compatible(workspace)
        if not controls.yes:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            if not typer.confirm(
                "Replace the complete current signal generation using the "
                "configured model provider?",
                err=True,
            ):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        generated = run_signals_generate(workspace)
        created = list(generated.created_signal_ids)
        superseded = list(generated.superseded_signal_ids)
        created_groups = (
            [EntityIdGroup(entity_type="self_signal", ids=created)]
            if created
            else []
        )
        superseded_groups = (
            [EntityIdGroup(entity_type="self_signal", ids=superseded)]
            if superseded
            else []
        )
        if generated.superseded_claim_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="self_claim",
                    ids=list(generated.superseded_claim_ids),
                )
            )
        if generated.superseded_snapshot_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="assessment_snapshot",
                    ids=list(generated.superseded_snapshot_ids),
                )
            )
        invalidated_views = list(generated.invalidated_views)
        view_lines = "\n".join(
            f"Invalidated {item.snapshot_id}: {item.regeneration_command}"
            for item in invalidated_views
        )
        return Outcome(
            affected_ids=AffectedIds(
                created=created_groups,
                superseded=superseded_groups,
                deleted=[],
            ),
            generation_ids=sorted(
                {
                    *(
                        [generated.generation_id]
                        if generated.generation_id is not None
                        else []
                    ),
                    *generated.superseded_generation_ids,
                },
                key=lambda value: value.encode("utf-8"),
            ),
            run_ids=[generated.run_id],
            invalidated_views=invalidated_views,
            warnings=list(generated.warnings),
            result=None,
            human_result=(
                f"Created {len(created)} signals; superseded {len(superseded)}. "
                f"Invalidated {len(invalidated_views)} assessment views."
                + (f"\n{view_lines}" if view_lines else "")
            ),
        )

    _run_command(context, "signals generate", operation)


@signals_app.command("list")
def signals_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        signals = list(list_current_signals(workspace))
        human = "\n".join(_signal_human_line(signal) for signal in signals)
        return Outcome(
            result=SignalsListResult(signals=signals),
            human_result=human or "No current signals.",
        )

    _run_command(context, "signals list", operation)


def _claim_human_line(claim: SelfClaim) -> str:
    return (
        f"{claim.id}\t{claim.claim_kind}\t{claim.dimension}\t"
        f"{claim.confidence}\t{claim.verification_status}"
    )


@assess_app.command("generate")
def assess_generate(
    context: typer.Context,
    scope: str = typer.Option("global", "--scope"),
    project: str | None = typer.Option(None, "--project"),
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        selected_scope, selected_project = validate_assessment_selection(
            scope=scope, project=project
        )
        require_compatible(workspace)
        if not controls.yes:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            if not typer.confirm(
                "Generate the self-assessment view using the configured model provider?",
                err=True,
            ):
                return Outcome(exit_code=9, diagnostic_class="cancelled")
        generated = run_assess_generate(
            workspace, scope=selected_scope, project=selected_project
        )
        assert generated.snapshot is not None and generated.snapshot_id is not None
        created_groups = [
            EntityIdGroup(
                entity_type="assessment_snapshot", ids=[generated.snapshot_id]
            ),
            EntityIdGroup(
                entity_type="self_claim", ids=list(generated.created_claim_ids)
            ),
        ]
        superseded_groups: list[EntityIdGroup] = []
        if generated.superseded_claim_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="self_claim",
                    ids=list(generated.superseded_claim_ids),
                )
            )
        if generated.superseded_snapshot_ids:
            superseded_groups.append(
                EntityIdGroup(
                    entity_type="assessment_snapshot",
                    ids=list(generated.superseded_snapshot_ids),
                )
            )
        prior = (
            ""
            if generated.replaced_view is None
            else f"; superseded {generated.replaced_view.snapshot_id}"
        )
        return Outcome(
            affected_ids=AffectedIds(
                created=created_groups,
                superseded=superseded_groups,
                deleted=[],
            ),
            generation_ids=sorted(
                {
                    *(
                        [generated.generation_id]
                        if generated.generation_id is not None
                        else []
                    ),
                    *generated.superseded_generation_ids,
                },
                key=lambda value: value.encode("utf-8"),
            ),
            run_ids=[generated.run_id],
            warnings=list(generated.warnings),
            result=None,
            human_result=(
                f"Created {generated.snapshot.id} — {generated.snapshot.title}; "
                f"{len(generated.claims)} claims{prior}."
            ),
        )

    _run_command(context, "assess generate", operation)


@assess_app.command("list")
def assess_list(context: typer.Context) -> None:
    def operation(workspace: Path, _controls: Controls) -> Outcome:
        snapshots = list_current_snapshots(workspace)
        items = [
            SnapshotListItem(
                id=item.id,
                scope=item.scope,
                scope_target=item.scope_target,
                verification_status=item.verification_status,
                created_at=item.created_at,
            )
            for item in snapshots
        ]
        human = "\n".join(
            f"{item.id}\t{item.scope}\t{item.scope_target or ''}\t"
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
) -> None:
    def operation(workspace: Path, controls: Controls) -> Outcome:
        # Resolve before file acquisition or prompt; capture re-checks under
        # the writer lock so this read-only validation cannot race the write.
        validate_gap_answer_selection(workspace, gap_id=gap_id)
        if source_file is not None:
            bundle = capture_gap_answer_file(
                workspace, gap_id=gap_id, source_path=source_file
            )
        else:
            if _noninteractive(controls):
                raise NonInteractiveInputRequired()
            # Fail closed on local-time configuration before owner input.
            workspace_zone(require_timezone(load_workspace_config(workspace)))
            raw_text = typer.prompt("Answer the gap question", err=True)
            bundle = capture_gap_answer(
                workspace, gap_id=gap_id, raw_text=raw_text
            )
        evidence_ids = [item.id for item in bundle.evidence_items]
        # §13/§14.7 gap-answer stale-export trigger: when §13.12/§13.14 managed
        # exports land, this transaction enumerates and removes the affected
        # out/assessment and out/branch sets. It supersedes no snapshot row and
        # reports no §13.13 rule 9 regeneration command — the still-current
        # snapshot needs re-export, not regeneration.
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
            human_result=_fact_human_line(fact),
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
                deleted=(
                    [
                        EntityIdGroup(
                            entity_type="evidence_item",
                            ids=list(deleted.evidence_item_ids),
                        )
                    ]
                    + (
                        [
                            EntityIdGroup(
                                entity_type="experience_fact",
                                ids=list(deleted.purged_fact_ids),
                            )
                        ]
                        if deleted.purged_fact_ids
                        else []
                    )
                    + (
                        [
                            EntityIdGroup(
                                entity_type="gap_question",
                                ids=list(deleted.purged_gap_ids),
                            )
                        ]
                        if deleted.purged_gap_ids
                        else []
                    )
                    + (
                        [
                            EntityIdGroup(
                                entity_type="contradiction",
                                ids=list(deleted.purged_contradiction_ids),
                            )
                        ]
                        if deleted.purged_contradiction_ids
                        else []
                    )
                    + (
                        [
                            EntityIdGroup(
                                entity_type="self_signal",
                                ids=list(deleted.purged_signal_ids),
                            )
                        ]
                        if deleted.purged_signal_ids
                        else []
                    )
                    + (
                        [
                            EntityIdGroup(
                                entity_type="verification_finding",
                                ids=list(deleted.purged_finding_ids),
                            )
                        ]
                        if deleted.purged_finding_ids
                        else []
                    )
                    + (
                        [
                            EntityIdGroup(
                                entity_type="self_claim",
                                ids=list(deleted.purged_claim_ids),
                            )
                        ]
                        if deleted.purged_claim_ids
                        else []
                    )
                    + (
                        [
                            EntityIdGroup(
                                entity_type="assessment_snapshot",
                                ids=list(deleted.purged_snapshot_ids),
                            )
                        ]
                        if deleted.purged_snapshot_ids
                        else []
                    )
                    + [
                        EntityIdGroup(
                            entity_type="raw_log",
                            ids=[deleted.selected_log.id],
                        )
                    ]
                ),
            ),
            residual_paths=list(deleted.residual_paths),
            invalidated_views=list(deleted.invalidated_views),
            result=result,
            human_result=(
                f"Deleted raw log {deleted.selected_log.id}."
                if not deleted.residual_paths
                else f"Deleted raw log {deleted.selected_log.id}; cleanup is incomplete."
            )
            + (
                "\n"
                + "\n".join(
                    f"Purged {item.snapshot_id}: {item.regeneration_command}"
                    for item in deleted.invalidated_views
                )
                if deleted.invalidated_views
                else ""
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
