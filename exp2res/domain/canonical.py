"""Canonical JSON serialization and hashing for validated domain payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib

from pydantic import BaseModel


_SIMPLE_ESCAPES = {
    '"': r'\"',
    "\\": r"\\",
    "\b": r"\b",
    "\f": r"\f",
    "\n": r"\n",
    "\r": r"\r",
    "\t": r"\t",
}


def _string(value: str) -> str:
    parts = ['"']
    for character in value:
        escaped = _SIMPLE_ESCAPES.get(character)
        if escaped is not None:
            parts.append(escaped)
        elif ord(character) < 0x20:
            parts.append(f"\\u00{ord(character):02x}")
        else:
            parts.append(character)
    parts.append('"')
    return "".join(parts)


def _datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("canonical serialization requires offset-aware datetimes")
    normalized = value.astimezone(timezone.utc)
    rendered = (
        f"{normalized.year:04d}-{normalized.month:02d}-{normalized.day:02d}"
        f"T{normalized.hour:02d}:{normalized.minute:02d}:{normalized.second:02d}"
        f".{normalized.microsecond:06d}Z"
    )
    return _string(rendered)


def _json(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return _datetime(value)
    if isinstance(value, list):
        return "[" + ",".join(_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return "{" + ",".join(
            f"{_string(key)}:{_json(value[key])}" for key in sorted(value)
        ) + "}"
    if isinstance(value, (float, Decimal)):
        raise TypeError("canonical serialization does not support non-integer numbers")
    raise TypeError(f"unsupported canonical serialization type: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one supported Python structure to the exact §11 byte form."""

    return _json(value).encode("utf-8")


def canonical_hash(value: object) -> str:
    """Return SHA-256 over the exact §11 byte form as lowercase hexadecimal."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_model_hash(value: BaseModel) -> str:
    """Hash one validated Pydantic model through the §11 canonical byte form.

    The §11 datetime normalization governs hash bytes only: callers that
    transmit or store the model keep their own offset-preserving
    serialization and never reuse these bytes.
    """

    return canonical_hash(value.model_dump(mode="python"))
