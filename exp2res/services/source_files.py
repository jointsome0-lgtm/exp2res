"""§29.4 local source acquisition gate for manual capture files."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import unquote, urlsplit

from exp2res.config import WorkspaceConfig
from exp2res.domain.models import (
    RAW_TEXT_LIMIT,
    validate_posix_path,
    validate_structural,
)
from exp2res.errors import (
    ArtifactLocatorDeniedError,
    ArtifactLocatorDuplicateError,
    ArtifactLocatorIgnoredError,
    ArtifactLocatorInvalidError,
    ArtifactLocatorLimitError,
    ArtifactLocatorUnresolvableError,
    ArtifactLocatorUnsupportedPathError,
    ForbiddenPathError,
    InvalidInputError,
)

WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MAX_ARTIFACT_LOCATORS = 16
DENIED_COMPONENTS = {
    "secrets",
    "credentials",
    ".git",
    ".exp2res",
    "out",
    "node_modules",
    ".venv",
    "dist",
    "build",
}


@dataclass(frozen=True)
class ArtifactLocator:
    """One validated inert locator in its exact persisted field shape."""

    uri: str | None
    path: str | None

    @property
    def stored_key(self) -> tuple[str, str]:
        if self.path is not None:
            return ("path", self.path)
        if self.uri is None:
            raise ArtifactLocatorInvalidError()
        return ("uri", self.uri)

    @property
    def order_key(self) -> tuple[int, bytes]:
        """§13.1's canonical order: local locators first, then by stored bytes."""

        field, value = self.stored_key
        return (0 if field == "path" else 1, value.encode("utf-8"))


def _forbidden_supplied_form(value: str) -> bool:
    return (
        "\\" in value
        or WINDOWS_DRIVE.match(value) is not None
        or value.startswith("//")
    )


def _case_insensitive_lookup(path: Path) -> bool:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        alternate_name = "".join(
            char.swapcase() if char.isalpha() else char for char in component
        )
        if alternate_name == component:
            continue
        alternate = current.parent / alternate_name
        try:
            if alternate.exists() and os.path.samefile(current, alternate):
                return True
        except OSError:
            continue
    return False


def _mandatory_denied(path: Path, *, folded: bool) -> bool:
    for component in path.parts:
        compared = component.casefold() if folded else component
        denied_names = (
            {value.casefold() for value in DENIED_COMPONENTS}
            if folded
            else DENIED_COMPONENTS
        )
        if compared in denied_names:
            return True
        if compared == ".env" or compared.startswith(".env."):
            return True
        if compared.endswith(".pem") or compared.endswith(".key"):
            return True
    return False


def _ignored(path: Path, *, config: WorkspaceConfig, folded: bool) -> bool:
    selected_value = PurePosixPath(path.name).as_posix()
    resolved_value = path.as_posix()
    return any(
        fnmatchcase(
            selected_value.casefold() if folded else selected_value,
            pattern.casefold() if folded else pattern,
        )
        or fnmatchcase(
            resolved_value.casefold() if folded else resolved_value,
            pattern.casefold() if folded else pattern,
        )
        for pattern in config.ignore_paths
    )


def validate_artifact_locator_count(supplied: tuple[str, ...]) -> None:
    if len(supplied) > MAX_ARTIFACT_LOCATORS:
        raise ArtifactLocatorLimitError()


def _file_uri_path(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() != "file"
            or (
                parsed.netloc
                and parsed.netloc.casefold() != "localhost"
            )
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("file URI does not resolve to one POSIX path")
        decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
        validate_posix_path(decoded)
    except (UnicodeError, ValueError, TypeError) as error:
        raise ArtifactLocatorUnsupportedPathError() from error
    return decoded


def authorize_artifact_locators(
    supplied: tuple[str, ...], *, config: WorkspaceConfig
) -> tuple[ArtifactLocator, ...]:
    """Classify and authorize inert owner locators without opening them."""

    validate_artifact_locator_count(supplied)
    accepted: list[ArtifactLocator] = []
    stored_keys: set[tuple[str, str]] = set()
    for value in supplied:
        try:
            validate_structural(value)
        except (UnicodeError, ValueError, TypeError) as error:
            raise ArtifactLocatorInvalidError() from error

        scheme_match = URI_SCHEME.match(value)
        scheme = (
            value[: scheme_match.end() - 1].casefold()
            if scheme_match is not None
            else None
        )
        if WINDOWS_DRIVE.match(value) is not None:
            raise ArtifactLocatorUnsupportedPathError()
        if scheme is not None and scheme != "file":
            try:
                parsed = urlsplit(value)
            except ValueError as error:
                raise ArtifactLocatorInvalidError() from error
            if not parsed.scheme:
                raise ArtifactLocatorInvalidError()
            locator = ArtifactLocator(uri=value, path=None)
        else:
            local_value = _file_uri_path(value) if scheme is not None else value
            if _forbidden_supplied_form(local_value):
                raise ArtifactLocatorUnsupportedPathError()
            try:
                validate_posix_path(local_value)
            except (UnicodeError, ValueError, TypeError) as error:
                raise ArtifactLocatorUnsupportedPathError() from error
            try:
                resolved = Path(local_value).resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ArtifactLocatorUnresolvableError() from error
            folded = _case_insensitive_lookup(resolved)
            if _mandatory_denied(resolved, folded=folded):
                raise ArtifactLocatorDeniedError()
            if _ignored(resolved, config=config, folded=folded):
                raise ArtifactLocatorIgnoredError()
            try:
                canonical = validate_posix_path(resolved.as_posix())
            except (UnicodeError, ValueError, TypeError) as error:
                raise ArtifactLocatorInvalidError() from error
            locator = ArtifactLocator(uri=None, path=canonical)

        if locator.stored_key in stored_keys:
            raise ArtifactLocatorDuplicateError()
        stored_keys.add(locator.stored_key)
        accepted.append(locator)
    # §13.1 orders the created items by stored locator, not by input order, so
    # the persisted bundle and every later read agree without depending on an
    # insertion-order storage artifact.
    return tuple(sorted(accepted, key=lambda locator: locator.order_key))


def read_capture_file(
    supplied: str, *, config: WorkspaceConfig
) -> tuple[str, str]:
    if _forbidden_supplied_form(supplied):
        raise ForbiddenPathError()
    path = Path(supplied)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise InvalidInputError() from error
    folded = _case_insensitive_lookup(resolved)
    if not resolved.is_file() or _mandatory_denied(resolved, folded=folded):
        raise ForbiddenPathError()

    if _ignored(resolved, config=config, folded=folded):
        raise ForbiddenPathError()

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        opened = os.fstat(descriptor)
        current = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(opened, current):
            raise ForbiddenPathError()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(RAW_TEXT_LIMIT + 1)
    except ForbiddenPathError:
        raise
    except OSError as error:
        raise InvalidInputError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) > RAW_TEXT_LIMIT:
        error = InvalidInputError()
        error.diagnostic_class = "input_too_large"
        error.public_message = "The selected source exceeds the raw-text limit."
        raise error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidInputError() from error
    return text, path.as_posix()
