"""§29.4 local-source and persisted-locator authorization gates."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import BinaryIO, Callable, Iterator
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
    PayloadLocatorError,
)

WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
SLASH_WINDOWS_DRIVE = re.compile(r"^/[A-Za-z]:[\\/]")
URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# §29.4 rule 18: `x://host/path` is a remote locator (RFC 3986 one-char scheme), not drive `x:`.
AUTHORITY_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
URI_COMPONENT = re.compile(
    r"^(?:[A-Za-z0-9._~!$&'()*+,;=:@/?-]|%[0-9A-Fa-f]{2})*$"
)
URI_AUTHORITY = re.compile(
    r"^(?:[A-Za-z0-9._~!$&'()*+,;=:@\[\]-]|%[0-9A-Fa-f]{2})*$"
)
MAX_ARTIFACT_LOCATORS = 16
# §29.4: exactly these persisted fields, stored canonical.
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
POSIX_CLASS_RANGES = {
    "alnum": ((0x30, 0x39), (0x41, 0x5A), (0x61, 0x7A)),
    "alpha": ((0x41, 0x5A), (0x61, 0x7A)),
    "blank": ((0x09, 0x09), (0x20, 0x20)),
    "cntrl": ((0x00, 0x1F), (0x7F, 0x7F)),
    "digit": ((0x30, 0x39),),
    "graph": ((0x21, 0x7E),),
    "lower": ((0x61, 0x7A),),
    "print": ((0x20, 0x7E),),
    "punct": (
        (0x21, 0x2F),
        (0x3A, 0x40),
        (0x5B, 0x60),
        (0x7B, 0x7E),
    ),
    "space": ((0x09, 0x0D), (0x20, 0x20)),
    "upper": ((0x41, 0x5A),),
    "xdigit": ((0x30, 0x39), (0x41, 0x46), (0x61, 0x66)),
}


@dataclass(frozen=True)
class ArtifactLocator:
    """One validated inert locator, persisted field shape."""

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
        """§13.1 order: local first, then stored bytes."""

        field, value = self.stored_key
        return (0 if field == "path" else 1, value.encode("utf-8"))


def _forbidden_supplied_form(value: str, *, uri_authority: bool = False) -> bool:
    """Reject spellings §29.4 never accepts; `uri_authority` admits rule 18."""

    if "\\" in value or value.startswith("//"):
        return True
    if WINDOWS_DRIVE.match(value) is None:
        return False
    return not (uri_authority and AUTHORITY_URI.match(value) is not None)


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


def _class_regex(
    segment: str,
    index: int,
    *,
    folded: bool,
) -> tuple[str, int] | None:
    """One wildmatch bracket (unknown class → no match, unterminated → literal)."""

    cursor = index + 1
    negated = segment[cursor : cursor + 1] in {"!", "^"}
    if negated:
        cursor += 1

    members: set[int] = set()
    previous: int | None = None
    if segment[cursor : cursor + 1] == "]":
        previous = ord("]")
        members.add(previous)
        cursor += 1

    while cursor < len(segment):
        char = segment[cursor]
        if char == "]":
            if negated:
                members = set(range(256)).difference(members)
            # WM_PATHNAME: no `/` even from a negated bracket.
            members.discard(ord("/"))
            if not members:
                return "(?!)", cursor + 1
            ranges: list[tuple[int, int]] = []
            start = end = min(members)
            for value in sorted(members)[1:]:
                if value == end + 1:
                    end = value
                    continue
                ranges.append((start, end))
                start = end = value
            ranges.append((start, end))
            encoded = "".join(
                (
                    f"\\x{start:02x}"
                    if start == end
                    else f"\\x{start:02x}-\\x{end:02x}"
                )
                for start, end in ranges
            )
            return f"[{encoded}]", cursor + 1
        if segment.startswith("[:", cursor):
            class_end = segment.find(":]", cursor + 2)
            if class_end != -1:
                name = segment[cursor + 2 : class_end]
                class_ranges = POSIX_CLASS_RANGES.get(name)
                if class_ranges is None:
                    return "(?!)", len(segment)
                if folded and name == "upper":
                    class_ranges = ((0x61, 0x7A),)
                for start, end in class_ranges:
                    members.update(range(start, end + 1))
                previous = None
                cursor = class_end + 2
                continue
        if (
            char == "-"
            and previous is not None
            and cursor + 1 < len(segment)
            and segment[cursor + 1] != "]"
        ):
            endpoint = ord(segment[cursor + 1])
            if previous <= endpoint:
                members.update(range(previous, endpoint + 1))
            # Reversed range keeps only its start, as git does.
            previous = None
            cursor += 2
            continue
        previous = ord(char)
        members.add(previous)
        cursor += 1
    return None


def _utf8_byte_view(value: str) -> str:
    """One regex character per UTF-8 byte, `/` unchanged."""

    return value.encode("utf-8").decode("latin-1")


def _segment_regex(segment: str, *, folded: bool = False) -> str:
    """One gitignore segment; wildcards never cross `/`."""

    parts: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            while index < len(segment) and segment[index] == "*":
                index += 1
            parts.append("[^/]*")
            continue
        if char == "?":
            parts.append("[^/]")
        elif char == "[":
            translated = _class_regex(segment, index, folded=folded)
            if translated is not None:
                expression, index = translated
                parts.append(expression)
                continue
            parts.append(re.escape(char))
        else:
            parts.append(re.escape(char))
        index += 1
    return "".join(parts)


@lru_cache(maxsize=256)
def _ignore_matcher(
    pattern: str,
    *,
    folded: bool = False,
) -> tuple[re.Pattern[str], bool, bool]:
    """One §29.4 pattern → (regex, anchored, directory_only); byte view so wildcard width follows wildmatch."""

    # Gitignore drops unescaped trailing spaces.
    without_trailing_spaces = pattern.rstrip(" ")
    directory_only = without_trailing_spaces.endswith("/")
    # Trailing `/` still anchors.
    anchored = "/" in without_trailing_spaces
    normalized = (
        without_trailing_spaces[:-1]
        if directory_only
        else without_trailing_spaces
    )
    raw_segments = normalized.split("/")
    # Consecutive separators never match in gitignore.
    if any(not segment for segment in raw_segments):
        return re.compile(r"(?!)"), anchored, directory_only
    segments: list[str] = []
    for segment in raw_segments:
        # Collapse `**` runs: same meaning, no combinatorial matching.
        if segment == "**" and segments[-1:] == ["**"]:
            continue
        segments.append(segment)
    parts: list[str] = [] if anchored else ["(?:[^/]+/)*"]
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if segment == "**":
            parts.append(".+" if last else "(?:[^/]+/)*")
            continue
        parts.append(
            _segment_regex(_utf8_byte_view(segment), folded=folded)
        )
        if not last:
            parts.append("/")
    return re.compile("".join(parts)), anchored, directory_only


def _match_prefixes(value: str, *, include_whole: bool) -> tuple[str, ...]:
    """The path and each ancestor, as gitignore compares."""

    segments = value.split("/")
    upper = len(segments) if include_whole else len(segments) - 1
    return tuple("/".join(segments[:count]) for count in range(1, upper + 1))


def _ignored(path: Path, *, config: WorkspaceConfig, folded: bool) -> bool:
    # §29.4: root-anchored so capture and the §15 re-check agree from any cwd;
    # unanchored patterns apply at any depth, outside the root too.
    try:
        relative: str | None = path.relative_to(config.root).as_posix()
    except ValueError:
        relative = None
    absolute = path.as_posix().lstrip("/")
    # A directory-only rule covers the directory itself.
    selected_is_directory = path.is_dir()
    for pattern in config.ignore_paths:
        compared_pattern = pattern.casefold() if folded else pattern
        matcher, anchored, directory_only = _ignore_matcher(
            compared_pattern,
            folded=folded,
        )
        if anchored:
            targets = (relative,)
        else:
            targets = (relative if relative is not None else absolute,)
        for target in targets:
            if target is None:
                continue
            compared = target.casefold() if folded else target
            for prefix in _match_prefixes(
                _utf8_byte_view(compared),
                include_whole=not directory_only or selected_is_directory,
            ):
                if matcher.fullmatch(prefix):
                    return True
    return False


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
    """RFC 3986 syntax check, bytes unchanged."""

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
    """Classify and authorize inert locators without opening them."""

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
    # §13.1: by stored locator, not input order.
    return tuple(sorted(accepted, key=lambda locator: locator.order_key))


def reauthorize_prompt_locators(
    payload: object, *, config: WorkspaceConfig
) -> None:
    """Fail closed if a persisted locator cannot enter a §15 prompt."""

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
                    # Same order as capture-time authorization, or the re-check diverges.
                    windows_form = _forbidden_supplied_form(
                        child, uri_authority=True
                    )
                    if scheme is not None and scheme != "file" and not windows_form:
                        continue
                    try:
                        if windows_form:
                            raise ArtifactLocatorUnsupportedPathError()
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
def _not_utf8() -> InvalidInputError:
    failure = InvalidInputError()
    failure.diagnostic_class = "input_not_utf8"
    failure.public_message = "The selected source is not valid UTF-8."
    return failure


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
        raise _not_utf8() from error


class PayloadFile:
    """One §14.5 payload, re-read by rewinding the proved descriptor (§19.4 rule 4: no size cap).

    Newline translation is off: a CR inside a JSONL record must survive."""

    def __init__(self, stream: BinaryIO) -> None:
        self._text = io.TextIOWrapper(stream, encoding="utf-8", newline="\n")
        self._digest: str | None = None

    def _rewind(self) -> None:
        try:
            self._text.seek(0)
        except (OSError, ValueError) as error:
            raise InvalidInputError() from error

    def lines(self) -> Iterator[str]:
        """LF-delimited lines; `digest` is set only by an exhausted pass."""

        self._digest = None
        self._rewind()
        running = hashlib.sha256()
        try:
            for line in self._text:
                running.update(line.encode("utf-8"))
                yield line.rstrip("\n")
        except UnicodeDecodeError as error:
            raise _not_utf8() from error
        except OSError as error:
            raise InvalidInputError() from error
        self._digest = running.hexdigest()

    def text(self) -> str:
        """Whole payload for single-document contracts; no digest."""

        self._digest = None
        self._rewind()
        try:
            return self._text.read()
        except UnicodeDecodeError as error:
            raise _not_utf8() from error
        except OSError as error:
            raise InvalidInputError() from error

    def identity(self) -> tuple[int, int]:
        """(size, mtime) staleness pair; `digest` is the exact answer."""

        try:
            status = os.fstat(self._text.fileno())
        except OSError as error:
            raise InvalidInputError() from error
        return (status.st_size, status.st_mtime_ns)

    def digest(self) -> str | None:
        """The SHA-256 of the last exhausted `lines` pass, or None."""

        return self._digest

    def release(self) -> None:
        """Drop the decoder; the owner closes the descriptor."""

        self._text.detach()


def _authorize_selected_file(
    supplied: str, *, config: WorkspaceConfig
) -> tuple[Path, str]:
    """§29.4 rules 4–14: (path to open, canonical path to persist)."""

    if _forbidden_supplied_form(supplied):
        raise ForbiddenPathError()
    try:
        resolved = Path(supplied).resolve(strict=True)
    except OSError as error:
        raise InvalidInputError() from error
    folded = _case_insensitive_lookup(resolved)
    if not resolved.is_file() or _mandatory_denied(resolved, folded=folded):
        raise ForbiddenPathError()
    if _ignored(resolved, config=config, folded=folded):
        raise ForbiddenPathError()
    try:
        return resolved, validate_posix_path(resolved.as_posix())
    except (UnicodeError, ValueError, TypeError) as error:
        raise ForbiddenPathError() from error


@contextmanager
def _open_selected_file(resolved: Path) -> Iterator[BinaryIO]:
    """Open and prove the opened object is the authorized file."""

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        opened = os.fstat(descriptor)
        current = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(opened, current):
            raise ForbiddenPathError()
        stream = os.fdopen(descriptor, "rb", closefd=False)
    except ForbiddenPathError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise InvalidInputError() from error
    try:
        with stream:
            yield stream
    finally:
        os.close(descriptor)


def _read_selected_file(
    resolved: Path, reader: Callable[[BinaryIO], str]
) -> str:
    with _open_selected_file(resolved) as stream:
        return reader(stream)


def read_capture_file(
    supplied: str, *, config: WorkspaceConfig
) -> tuple[str, str | None]:
    if supplied == "-":
        stream = getattr(sys.stdin, "buffer", None)
        if stream is None:
            raise InvalidInputError()
        return _read_bounded_utf8(stream), None
    resolved, canonical = _authorize_selected_file(supplied, config=config)
    return _read_selected_file(resolved, _read_bounded_utf8), canonical


def read_document_file(
    supplied: str, *, config: WorkspaceConfig
) -> tuple[str, str]:
    """Read one §14.5 `import file` document (no stdin form)."""

    resolved, canonical = _authorize_selected_file(supplied, config=config)
    return _read_selected_file(resolved, _read_bounded_utf8), canonical


@contextmanager
def open_payload_file(
    supplied: str, *, config: WorkspaceConfig
) -> Iterator[tuple[PayloadFile, Path]]:
    """Open one §14.5 payload with its §29.4 rule 8 root."""

    resolved, _ = _authorize_selected_file(supplied, config=config)
    with _open_selected_file(resolved) as stream:
        payload = PayloadFile(stream)
        try:
            yield payload, resolved.parent
        finally:
            payload.release()


def validate_remote_locator(value: str) -> str:
    """§29.4 rule 18: absolute URI, bytes unchanged."""

    try:
        validate_structural(value)
    except (UnicodeError, ValueError, TypeError) as error:
        raise ArtifactLocatorInvalidError() from error
    if _forbidden_supplied_form(value, uri_authority=True):
        raise ArtifactLocatorUnsupportedPathError()
    scheme_match = URI_SCHEME.match(value)
    if scheme_match is None:
        raise ArtifactLocatorInvalidError()
    if value[: scheme_match.end() - 1].casefold() == "file":
        raise ArtifactLocatorUnsupportedPathError()
    try:
        _validate_absolute_uri(value)
    except ValueError as error:
        raise ArtifactLocatorInvalidError() from error
    return value


def authorize_payload_locator(
    value: str, *, payload_root: Path, config: WorkspaceConfig
) -> str:
    """§29.4 rule 8: authorize an embedded locator unopened; returns the canonical path."""

    if _forbidden_supplied_form(value) or WINDOWS_DRIVE.match(value) is not None:
        raise PayloadLocatorError("payload_locator_path_unsupported")
    try:
        validate_posix_path(value)
    except (UnicodeError, ValueError, TypeError) as error:
        raise PayloadLocatorError("payload_locator_path_unsupported") from error
    if URI_SCHEME.match(value) is not None or value.startswith("/"):
        raise PayloadLocatorError("payload_locator_non_selected")
    if any(part == ".." for part in PurePosixPath(value).parts):
        raise PayloadLocatorError("payload_locator_non_selected")
    try:
        root = payload_root.resolve(strict=True)
        resolved = (root / value).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PayloadLocatorError("payload_locator_unresolved") from error
    if not resolved.is_file():
        raise PayloadLocatorError("payload_locator_unresolved")
    # Containment after symlink resolution.
    if resolved != root and root not in resolved.parents:
        raise PayloadLocatorError("payload_locator_non_selected")
    folded = _case_insensitive_lookup(resolved)
    if _mandatory_denied(resolved, folded=folded):
        raise PayloadLocatorError("payload_locator_denied")
    if _ignored(resolved, config=config, folded=folded):
        raise PayloadLocatorError("payload_locator_ignored")
    try:
        return validate_posix_path(resolved.as_posix())
    except (UnicodeError, ValueError, TypeError) as error:
        raise PayloadLocatorError() from error
