"""PII redaction and scan (architecture §6.1, edge-cases G5/G13)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --- Tokens (allow-listed; a second scan treats them as clean) ---
TOKEN_BY_TYPE: dict[str, str] = {
    "EMAIL": "[EMAIL]",
    "PAN": "[PAN]",
    "AADHAAR": "[AADHAAR]",
    "PHONE": "[PHONE]",
    "OTP": "[OTP]",
    "ACCOUNT": "[ACCOUNT]",
    "UPI": "[UPI]",
    "IFSC": "[IFSC]",
}
PII_TYPES: frozenset[str] = frozenset(TOKEN_BY_TYPE)
_TOKEN_RE = re.compile(r"\[(?:EMAIL|PAN|AADHAAR|PHONE|OTP|ACCOUNT|UPI|IFSC)\]")

# --- Confusables (Cyrillic / Greek → Latin A–Z); NFKC does not fold these ---
_CONFUSABLES: dict[str, str] = {
    "А": "A",
    "В": "B",
    "Е": "E",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Т": "T",
    "Х": "X",
    "а": "A",
    "е": "E",
    "о": "O",
    "р": "P",
    "с": "C",
    "х": "X",
    "Α": "A",
    "Β": "B",
    "Ε": "E",
    "Ζ": "Z",
    "Η": "H",
    "Ι": "I",
    "Κ": "K",
    "Μ": "M",
    "Ν": "N",
    "Ο": "O",
    "Ρ": "P",
    "Τ": "T",
    "Υ": "Y",
    "Χ": "X",
}

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)

# Non-ASCII decimal digits → ASCII (covers Devanagari and other Nd chars after NFKC gaps)
_DIGIT_MAP: dict[str, str] = {}
for _code in range(0x110000):
    _ch = chr(_code)
    if unicodedata.category(_ch) == "Nd":
        _digit = unicodedata.digit(_ch, -1)
        if 0 <= _digit <= 9 and _ch != str(_digit):
            _DIGIT_MAP[_ch] = str(_digit)

# --- Patterns (applied in order) ---
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)
_AADHAAR = re.compile(r"\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b")
_PHONE = re.compile(r"(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}\b")
_OTP = re.compile(
    r"\b(?:otp|one[- ]time (?:password|code)|verification code|pin|code)\b"
    r"(?:\s+is|\s*[:=])?\W{0,10}\d{4,8}\b",
    re.IGNORECASE,
)
_ACCOUNT_LABEL = re.compile(
    r"(?:a/c|acct|account|folio|ac no|bank)",
    re.IGNORECASE,
)
_ACCOUNT_LABELED = re.compile(r"\b\d{9,18}\b")
# Bare 11–18 digits must be a standalone token — not a digit run inside a
# hex review id such as baba26480501938e (EC-PII-17 / P3 pulse cites).
_ACCOUNT_BARE = re.compile(r"(?<![0-9A-Za-z])\d{11,18}(?![0-9A-Za-z])")
_UPI = re.compile(r"\b[\w.-]+@(?:ok\w+|ybl|paytm|upi|ibl|axl)\b", re.IGNORECASE)
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")

_PHONE_CONTEXT = re.compile(
    r"(?:call|mobile|phone|whatsapp|contact|number|no\.)",
    re.IGNORECASE,
)
_TEN_DIGITS = re.compile(r"(?<!\d)\d(?:[\s-]?\d){9}(?!\d)")

_PAN_COMPACT_WINDOW = re.compile(
    r"[A-Z]{5}[\s-]*\d{4}[\s-]*[A-Z]",
    re.IGNORECASE,
)

# Verhoeff tables for optional Aadhaar checksum
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


@dataclass(frozen=True, slots=True, repr=False)
class RedactionResult:
    """Redacted text and the PII types found. Holds no reference to the original."""

    text: str
    types: frozenset[str]

    def __post_init__(self) -> None:
        unknown = self.types - PII_TYPES
        if unknown:
            raise ValueError(f"unknown PII types: {sorted(unknown)}")

    def __repr__(self) -> str:
        return f"RedactionResult(types={sorted(self.types)!r}, text={self.text!r})"


def normalize_for_scan(text: str) -> str:
    """Pre-normalization (G5): NFKC, strip ZW, map digits, confusables, collapse space."""
    if not isinstance(text, str):
        raise TypeError("pii expects a str")
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZERO_WIDTH)
    chars: list[str] = []
    for ch in text:
        if ch in _DIGIT_MAP:
            chars.append(_DIGIT_MAP[ch])
        elif ch in _CONFUSABLES:
            chars.append(_CONFUSABLES[ch])
        else:
            chars.append(ch)
    text = "".join(chars)
    return re.sub(r"\s+", " ", text).strip()


def redact(text: str) -> RedactionResult:
    """Normalize, redact PII, return typed tokens. Original is not retained."""
    view = normalize_for_scan(text)
    redacted, found = _apply_all(view)
    return RedactionResult(text=redacted, types=frozenset(found))


def scan(text: str) -> list[str]:
    """Return sorted PII type names found. Empty list means clean."""
    view = normalize_for_scan(text)
    # Existing redaction tokens are allow-listed: skip detection inside them.
    if _TOKEN_RE.search(view) and not _has_raw_pii(view):
        return []
    _, found = _apply_all(view)
    return sorted(found)


def _has_raw_pii(text: str) -> bool:
    """True if any pattern would match outside existing tokens."""
    masked = _TOKEN_RE.sub(" ", text)
    _, found = _apply_all(masked)
    return bool(found)


def _apply_all(text: str) -> tuple[str, set[str]]:
    found: set[str] = set()
    out = text

    def _sub(pattern: re.Pattern[str], pii_type: str, src: str) -> str:
        def repl(match: re.Match[str]) -> str:
            found.add(pii_type)
            return TOKEN_BY_TYPE[pii_type]

        return pattern.sub(repl, src)

    out = _sub(_EMAIL, "EMAIL", out)
    out = _sub(_PAN, "PAN", out)
    out = _redact_aadhaar(out, found)
    out = _sub(_PHONE, "PHONE", out)
    out = _redact_phone_context(out, found)
    out = _sub(_OTP, "OTP", out)
    out = _redact_account(out, found)
    out = _sub(_UPI, "UPI", out)
    out = _sub(_IFSC, "IFSC", out)
    out = _redact_pan_compact(out, found)
    return out, found


def _verhoeff_ok(digits: str) -> bool:
    checksum = 0
    for i, ch in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[i % 8][int(ch)]]
    return checksum == 0


def _redact_aadhaar(text: str, found: set[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) != 12 or not _verhoeff_ok(digits):
            return match.group(0)  # leave for ACCOUNT / later passes
        found.add("AADHAAR")
        return TOKEN_BY_TYPE["AADHAAR"]

    return _AADHAAR.sub(repl, text)


def _redact_phone_context(text: str, found: set[str]) -> str:
    """G13: 10-digit run within 20 chars of a phone context word → [PHONE]."""

    def repl(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) != 10:
            return match.group(0)
        start, end = match.start(), match.end()
        window = text[max(0, start - 20) : min(len(text), end + 20)]
        if not _PHONE_CONTEXT.search(window):
            return match.group(0)
        found.add("PHONE")
        return TOKEN_BY_TYPE["PHONE"]

    return _TEN_DIGITS.sub(repl, text)


def _redact_account(text: str, found: set[str]) -> str:
    # Labeled 9–18 digits within 30 chars of an account cue.
    pieces: list[str] = []
    last = 0
    for match in _ACCOUNT_LABELED.finditer(text):
        start, end = match.start(), match.end()
        window = text[max(0, start - 30) : min(len(text), end + 30)]
        if _ACCOUNT_LABEL.search(window):
            pieces.append(text[last:start])
            pieces.append(TOKEN_BY_TYPE["ACCOUNT"])
            found.add("ACCOUNT")
            last = end
    pieces.append(text[last:])
    out = "".join(pieces)

    # Bare 11–18 digit runs (no label required).
    def bare(match: re.Match[str]) -> str:
        found.add("ACCOUNT")
        return TOKEN_BY_TYPE["ACCOUNT"]

    return _ACCOUNT_BARE.sub(bare, out)


def _redact_pan_compact(text: str, found: set[str]) -> str:
    """Second pass: spaces/hyphens inside a PAN-shaped window."""

    def repl(match: re.Match[str]) -> str:
        compact = re.sub(r"[\s-]+", "", match.group(0)).upper()
        if _PAN.fullmatch(compact):
            found.add("PAN")
            return TOKEN_BY_TYPE["PAN"]
        return match.group(0)

    # Only consider windows of roughly PAN length with separators.
    return _PAN_COMPACT_WINDOW.sub(repl, text)
