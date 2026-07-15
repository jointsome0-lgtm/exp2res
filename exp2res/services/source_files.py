"""§29.4 local source acquisition gate for manual capture files."""

from __future__ import annotations

from fnmatch import fnmatchcase
import os
from pathlib import Path, PurePosixPath
import re
import stat

from exp2res.config import WorkspaceConfig
from exp2res.domain.models import RAW_TEXT_LIMIT
from exp2res.errors import ForbiddenPathError, InvalidInputError

WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
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


def _forbidden_supplied_form(value: str) -> bool:
    return "\\" in value or WINDOWS_DRIVE.match(value) is not None or value.startswith("//")


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

    selected_name = PurePosixPath(resolved.name)
    selected_value = selected_name.as_posix()
    resolved_value = resolved.as_posix()
    if any(
        fnmatchcase(
            selected_value.casefold() if folded else selected_value,
            pattern.casefold() if folded else pattern,
        )
        or fnmatchcase(
            resolved_value.casefold() if folded else resolved_value,
            pattern.casefold() if folded else pattern,
        )
        for pattern in config.ignore_paths
    ):
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
