"""Secret-safe application errors mapped by the public CLI contract."""

from __future__ import annotations


class Exp2ResError(Exception):
    exit_code = 1
    diagnostic_class = "internal_error"
    public_message = "The operation failed unexpectedly."


class InvalidInputError(Exp2ResError):
    exit_code = 2
    diagnostic_class = "invalid_input"
    public_message = "The supplied input is invalid."


class NonInteractiveInputRequired(InvalidInputError):
    diagnostic_class = "input_required"
    public_message = "Required input was not supplied in non-interactive mode."


class OperationDeferredError(InvalidInputError):
    diagnostic_class = "operation_deferred_phase_2"
    public_message = "Correction capture is deferred until Phase 2."


class ForbiddenPathError(InvalidInputError):
    diagnostic_class = "forbidden_path"
    public_message = "The selected source path is not permitted."


class PublicCheckoutError(InvalidInputError):
    diagnostic_class = "public_checkout_forbidden"
    public_message = "A public engine checkout cannot be initialized as a private workspace."


class SelectorNotFoundError(InvalidInputError):
    diagnostic_class = "selector_not_found"
    public_message = "The selected record does not exist."


class WorkspaceError(Exp2ResError):
    exit_code = 3
    diagnostic_class = "workspace_not_found"
    public_message = "No valid Exp2Res workspace was established."


class SchemaCompatibilityError(Exp2ResError):
    exit_code = 4
    diagnostic_class = "schema_incompatible"
    public_message = "The workspace schema is incompatible or unrecognized."


class WorkspaceBusyError(Exp2ResError):
    exit_code = 5
    diagnostic_class = "workspace_busy"
    public_message = "The workspace is busy."


class IntegrityFailureError(Exp2ResError):
    exit_code = 7
    diagnostic_class = "integrity_failure"
    public_message = "Validation or storage integrity failed."


class MigrationFailedError(IntegrityFailureError):
    """A rolled-back migration is §14.14 class 7, not schema class 4."""

    diagnostic_class = "migration_failed"
    public_message = "The workspace migration failed and was rolled back."

    def __init__(self, *, managed_backup_path: str | None = None) -> None:
        super().__init__()
        self.managed_backup_path = managed_backup_path


class IdCollisionError(IntegrityFailureError):
    diagnostic_class = "id_collision"
    public_message = "A service-assigned entity ID collided repeatedly."


class HydrationFailureError(IntegrityFailureError):
    diagnostic_class = "hydration_failed"
    public_message = "Stored data failed strict validation."


class ConfigurationError(IntegrityFailureError):
    diagnostic_class = "configuration_invalid"
    public_message = "Workspace configuration is invalid."


class LLMInvocationError(Exp2ResError):
    """Privacy-safe §15 failure carrying only a stable machine code."""

    exit_code = 6
    diagnostic_class = "transport_provider_error"
    public_message = "The model invocation failed."

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code
        self.diagnostic_class = failure_code


class LLMCancelledError(LLMInvocationError):
    exit_code = 9
    diagnostic_class = "cancelled"
    public_message = "The model invocation was cancelled."

    def __init__(self) -> None:
        super().__init__("cancelled")
