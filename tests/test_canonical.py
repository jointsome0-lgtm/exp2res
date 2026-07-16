"""Unit tests for the §11 canonical hash serialization component."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from exp2res.domain.canonical import canonical_hash, canonical_json_bytes


def test_object_keys_sort_by_code_point_and_non_ascii_stays_utf8() -> None:
    assert canonical_json_bytes({"Ω": 3, "a": 1, "é": 2}) == (
        '{"a":1,"é":2,"Ω":3}'.encode("utf-8")
    )


def test_equal_datetime_instants_share_six_digit_utc_rendering() -> None:
    utc = datetime(2026, 7, 16, 7, 8, 9, tzinfo=timezone.utc)
    offset = datetime(
        2026,
        7,
        16,
        10,
        8,
        9,
        tzinfo=timezone(timedelta(hours=3)),
    )
    expected = b'"2026-07-16T07:08:09.000000Z"'
    assert canonical_json_bytes(utc) == expected
    assert canonical_json_bytes(offset) == expected


def test_strings_use_only_mandatory_escapes_and_raw_utf8() -> None:
    value = '"\\\b\f\n\r\t\x00\x1fé'
    expected = r'"\"\\\b\f\n\r\t\u0000\u001fé"'.encode("utf-8")
    assert canonical_json_bytes(value) == expected


def test_literals_bool_int_discrimination_and_minimal_integers() -> None:
    value = [True, 1, False, 0, None, -12, 12345678901234567890]
    assert canonical_json_bytes(value) == (
        b"[true,1,false,0,null,-12,12345678901234567890]"
    )
    digest = canonical_hash(value)
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    "value",
    [1.5, Decimal("1.5"), b"bytes", (1, 2), {1: "non-string key"}],
    ids=["float", "decimal", "bytes", "tuple", "non-string-key"],
)
def test_unsupported_values_fail_closed(value: object) -> None:
    with pytest.raises(TypeError):
        canonical_json_bytes(value)


def test_naive_datetime_fails_closed() -> None:
    with pytest.raises(ValueError, match="offset-aware"):
        canonical_json_bytes(datetime(2026, 7, 16, 7, 8, 9))
