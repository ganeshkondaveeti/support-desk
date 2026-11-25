"""The eval PII sweep (GC-10) catches real leaks but not digits inside other numbers."""

from __future__ import annotations

import pytest
from evals.sweep import _search_text

TERMS = ["482913", "9876543210", "50100234567890", "ABCDE1234F", "abcde1234f"]
TYPES = {"482913": "OTP", "9876543210": "PHONE", "50100234567890": "ACCOUNT"}


def _types(text: str) -> list[str]:
    return [h.pii_type for h in _search_text(text, TERMS, TYPES, "loc")]


@pytest.mark.parametrize(
    "text",
    [
        "OTP 482913",
        "my otp is 482913.",
        '{"q": "otp:482913"}',
        "call me on 9876543210",
        "a/c 50100234567890 please",
        "PAN ABCDE1234F",
    ],
)
def test_real_leaks_are_found(text: str) -> None:
    assert _types(text), text


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-25T04:52:51.482913+00:00",  # microseconds in a timestamp
        "latency 0.0004829134",  # inside a float
        "id 1482913 and 4829130",  # inside longer numbers
        "98765432101",  # an 11-digit number containing the phone
    ],
)
def test_digits_inside_other_numbers_are_not_leaks(text: str) -> None:
    assert _types(text) == [], text
