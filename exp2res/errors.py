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


class BlankProjectLabelError(InvalidInputError):
    diagnostic_class = "blank_project_label"
    public_message = "A project label must not canonicalize to blank."


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


class GapAlreadyAnsweredError(InvalidInputError):
    diagnostic_class = "gap_already_answered"
    public_message = "The selected gap has already been answered."


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

    def __init__(
        self,
        *,
        managed_backup_path: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        super().__init__()
        self.managed_backup_path = managed_backup_path
        self.failure_code = failure_code
        if failure_code is not None:
            self.public_message = (
                "The workspace migration failed and was rolled back "
                f"({failure_code})."
            )


class MigrationInterrupted(KeyboardInterrupt):
    """An owner interrupt during migration, carrying the retained backup.

    §14.14 rule 4 keeps code-9 precedence while requiring committed effects
    to remain reported: the verified backup created before the interrupt is
    durable managed workspace state, so its path rides along for the
    cancelled envelope instead of being dropped with a bare re-raise.
    """

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


class UnknownLLMConfigKeyError(ConfigurationError):
    diagnostic_class = "llm_config_key_unknown"
    public_message = "The LLM configuration contains an unknown key."


class UnknownLLMAdapterError(ConfigurationError):
    diagnostic_class = "llm_adapter_unknown"
    public_message = "The selected LLM adapter identifier is unknown."


class LLMAdapterNotRegisteredError(ConfigurationError):
    diagnostic_class = "llm_adapter_not_registered"
    public_message = "The selected LLM adapter is not registered in this build."


class LLMModelInvalidError(ConfigurationError):
    diagnostic_class = "llm_model_invalid"
    public_message = "The selected LLM model identifier is invalid."


class LLMSelectionMissingError(ConfigurationError):
    """§29.2: explicit [llm] selection is never replaced by a fallback."""

    diagnostic_class = "llm_selection_missing"
    public_message = (
        "Select [llm].adapter and [llm].model explicitly before LLM use."
    )


class LLMInvocationError(Exp2ResError):
    """Privacy-safe §15 failure carrying only a stable machine code."""

    exit_code = 6
    diagnostic_class = "transport_provider_error"
    public_message = "The model invocation failed."

    # §14.14 rule 4 assigns local validation/integrity §15 codes to exit
    # class 7: §15.1 invalid-after-retry, the §12 rule 10 commit-boundary
    # failures surfaced as business_commit_failed, and deterministic
    # service enrichment failing after a valid response — none of these is
    # a provider fault. Transport, capability, and budget/context preflight
    # codes stay in class 6.
    _VALIDATION_FAILURE_CODES = frozenset(
        {
            "response_validation_failed",
            "business_commit_failed",
            "deterministic_enrichment_failed",
        }
    )

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code
        self.diagnostic_class = failure_code
        if failure_code in self._VALIDATION_FAILURE_CODES:
            self.exit_code = 7
        # §14.14 rule 5: the command boundary reports the committed
        # processing runs a failed or cancelled invocation leaves behind.
        self.run_ids: tuple[str, ...] = ()


class LLMCancelledError(LLMInvocationError):
    exit_code = 9
    diagnostic_class = "cancelled"
    public_message = "The model invocation was cancelled."

    def __init__(self) -> None:
        super().__init__("cancelled")
