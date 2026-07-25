"""§29.4 local-source and persisted-locator authorization gates."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import BinaryIO
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
    LocatorReauthorizationFailedError,
)

WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
SLASH_WINDOWS_DRIVE = re.compile(r"^/[A-Za-z]:[\\/]")
URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
URI_COMPONENT = re.compile(
    r"^(?:[A-Za-z0-9._~!$&'()*+,;=:@/?-]|%[0-9A-Fa-f]{2})*$"
)
URI_AUTHORITY = re.compile(
    r"^(?:[A-Za-z0-9._~!$&'()*+,;=:@\[\]-]|%[0-9A-Fa-f]{2})*$"
)
MAX_ARTIFACT_LOCATORS = 16
PROMPT_LOCATOR_FIELDS = frozenset({"path", "uri", "url", "external_ref"})
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
        _validate_absolute_uri(value)
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
        if SLASH_WINDOWS_DRIVE.match(decoded) is not None:
            raise ValueError("file URI contains a Windows drive path")
        validate_posix_path(decoded)
    except (UnicodeError, ValueError, TypeError) as error:
        raise ArtifactLocatorUnsupportedPathError() from error
    return decoded


def _validate_absolute_uri(value: str) -> None:
    """Validate RFC 3986 URI syntax without rewriting the supplied bytes."""

    if (
        not value.isascii()
        or any(char.isspace() for char in value)
        or INVALID_PERCENT_ESCAPE.search(value) is not None
        or value.count("#") > 1
    ):
        raise ValueError("invalid absolute URI syntax")
    scheme_match = URI_SCHEME.match(value)
    if scheme_match is None:
        raise ValueError("absolute URI requires a scheme")
    parsed = urlsplit(value)
    if not parsed.scheme or parsed.scheme.casefold() != value[
        : scheme_match.end() - 1
    ].casefold():
        raise ValueError("absolute URI scheme mismatch")
    if URI_AUTHORITY.fullmatch(parsed.netloc) is None:
        raise ValueError("invalid URI authority")
    if URI_COMPONENT.fullmatch(parsed.path) is None:
        raise ValueError("invalid URI path")
    if URI_COMPONENT.fullmatch(parsed.query) is None:
        raise ValueError("invalid URI query")
    if URI_COMPONENT.fullmatch(parsed.fragment) is None:
        raise ValueError("invalid URI fragment")
    try:
        parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError("invalid URI authority") from error


def _authorize_local_locator(
    value: str, *, config: WorkspaceConfig
) -> str:
    if _forbidden_supplied_form(value):
        raise ArtifactLocatorUnsupportedPathError()
    try:
        validate_posix_path(value)
    except (UnicodeError, ValueError, TypeError) as error:
        raise ArtifactLocatorUnsupportedPathError() from error
    try:
        resolved = Path(value).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ArtifactLocatorUnresolvableError() from error
    folded = _case_insensitive_lookup(resolved)
    if _mandatory_denied(resolved, folded=folded):
        raise ArtifactLocatorDeniedError()
    if _ignored(resolved, config=config, folded=folded):
        raise ArtifactLocatorIgnoredError()
    try:
        return validate_posix_path(resolved.as_posix())
    except (UnicodeError, ValueError, TypeError) as error:
        raise ArtifactLocatorInvalidError() from error


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
                _validate_absolute_uri(value)
            except ValueError as error:
                raise ArtifactLocatorInvalidError() from error
            locator = ArtifactLocator(uri=value, path=None)
        else:
            local_value = _file_uri_path(value) if scheme is not None else value
            canonical = _authorize_local_locator(local_value, config=config)
            locator = ArtifactLocator(uri=None, path=canonical)

        if locator.stored_key in stored_keys:
            raise ArtifactLocatorDuplicateError()
        stored_keys.add(locator.stored_key)
        accepted.append(locator)
    # §13.1 orders the created items by stored locator, not by input order, so
    # the persisted bundle and every later read agree without depending on an
    # insertion-order storage artifact.
    return tuple(sorted(accepted, key=lambda locator: locator.order_key))


def reauthorize_prompt_locators(
    payload: object, *, config: WorkspaceConfig
) -> None:
    """Fail closed if a persisted local locator cannot enter a §15 prompt."""

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    key in PROMPT_LOCATOR_FIELDS
                    and child is not None
                    and isinstance(child, str)
                ):
                    scheme_match = URI_SCHEME.match(child)
                    scheme = (
                        child[: scheme_match.end() - 1].casefold()
                        if scheme_match is not None
                        else None
                    )
                    if scheme is not None and scheme != "file":
                        continue
                    try:
                        local_value = (
                            _file_uri_path(child)
                            if scheme == "file"
                            else child
                        )
                        _authorize_local_locator(local_value, config=config)
                    except InvalidInputError as error:
                        raise LocatorReauthorizationFailedError() from error
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(payload)
def _read_bounded_utf8(stream: BinaryIO) -> str:
    try:
        data = stream.read(RAW_TEXT_LIMIT + 1)
    except OSError as error:
        raise InvalidInputError() from error
    if len(data) > RAW_TEXT_LIMIT:
        error = InvalidInputError()
        error.diagnostic_class = "input_too_large"
        error.public_message = "The selected source exceeds the raw-text limit."
        raise error
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        failure = InvalidInputError()
        failure.diagnostic_class = "input_not_utf8"
        failure.public_message = "The selected source is not valid UTF-8."
        raise failure from error


def read_capture_file(
    supplied: str, *, config: WorkspaceConfig
) -> tuple[str, str | None]:
    if supplied == "-":
        stream = getattr(sys.stdin, "buffer", None)
        if stream is None:
            raise InvalidInputError()
        return _read_bounded_utf8(stream), None
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
            text = _read_bounded_utf8(stream)
    except ForbiddenPathError:
        raise
    except OSError as error:
        raise InvalidInputError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return text, path.as_posix()
