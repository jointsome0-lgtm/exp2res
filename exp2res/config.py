"""Narrow, secret-safe Phase 0 configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Any

from .domain.models import validate_structural
from .errors import ConfigurationError, InvalidInputError

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # exercised by the supported Python 3.10 test environment
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class WorkspaceConfig:
    timezone: str | None
    ignore_paths: tuple[str, ...]


def _parse_toml(data: bytes) -> dict[str, Any]:
    return tomllib.loads(data.decode("utf-8"))


def load_workspace_config(workspace: Path) -> WorkspaceConfig:
    path = workspace / ".exp2res" / "config.toml"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ConfigurationError()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(1_048_577)
            if len(data) > 1_048_576:
                raise ConfigurationError()
        finally:
            os.close(descriptor)
        if path.is_symlink():
            raise ConfigurationError()
        parsed = _parse_toml(data)
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise ConfigurationError() from error

    # Phase 0 has no adapter, but literal values under credential-shaped keys
    # still fail closed instead of becoming latent workspace secrets.
    def reject_literal_credentials(section: dict[str, Any]) -> None:
        for key, value in section.items():
            if key in {
                "api_key",
                "access_token",
                "refresh_token",
                "secret",
                "password",
                "authorization",
            } and value:
                raise ConfigurationError()
            if isinstance(value, dict):
                reject_literal_credentials(value)

    for section in parsed.values():
        if not isinstance(section, dict):
            raise ConfigurationError()
        reject_literal_credentials(section)

    workspace_section = parsed.get("workspace", {})
    privacy_section = parsed.get("privacy", {})
    if not isinstance(workspace_section, dict) or not isinstance(privacy_section, dict):
        raise ConfigurationError()

    timezone = workspace_section.get("timezone")
    if timezone == "":
        timezone = None
    if timezone is not None:
        if not isinstance(timezone, str):
            raise ConfigurationError()
        try:
            validate_structural(timezone)
        except ValueError as error:
            raise ConfigurationError() from error

    ignore_paths = privacy_section.get("ignore_paths", [])
    if not isinstance(ignore_paths, list) or not all(
        isinstance(value, str) for value in ignore_paths
    ):
        raise ConfigurationError()
    validated: list[str] = []
    try:
        for pattern in ignore_paths:
            validate_structural(pattern)
            if "\\" in pattern or pattern.startswith("/"):
                raise ValueError("unsupported ignore pattern")
            validated.append(pattern)
    except ValueError as error:
        raise ConfigurationError() from error

    return WorkspaceConfig(timezone=timezone, ignore_paths=tuple(validated))


def require_timezone(config: WorkspaceConfig) -> str:
    if config.timezone is None:
        error = InvalidInputError()
        error.diagnostic_class = "workspace_timezone_required"
        error.public_message = "Set [workspace].timezone to an IANA name first."
        raise error
    return config.timezone
