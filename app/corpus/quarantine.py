"""Data-quality quarantine for snapshot B (architecture §5.1)."""

from __future__ import annotations

import re

# Exact / patterned sentences and fields that must never be quoted.
LAUNCH_1999 = re.compile(
    r"This scheme was made available to investors on 10 Dec 1999\.?",
    re.IGNORECASE,
)
AMC_WIDE_AUM = re.compile(
    r"[^.]*Asset Under Management\s*\(AUM\)\s*of\s*₹\s*9,67,910\s*Cr[^.]*\.?",
    re.IGNORECASE,
)
RATING_LABEL = re.compile(r"^Rating$", re.IGNORECASE)
RATING_LINE = re.compile(r"^Rating\s*[:\n]\s*\d+\s*$", re.IGNORECASE | re.MULTILINE)

QUARANTINE_PATTERNS: tuple[re.Pattern[str], ...] = (LAUNCH_1999, AMC_WIDE_AUM)


def is_quarantined_sentence(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if LAUNCH_1999.fullmatch(stripped) or LAUNCH_1999.search(stripped):
        # Whole sentence about the 1999 launch date.
        if "10 Dec 1999" in stripped:
            return True
    if "9,67,910" in stripped and "AUM" in stripped.upper().replace(" ", ""):
        return True
    if "Asset Under Management" in stripped and "9,67,910" in stripped:
        return True
    return False


def strip_quarantined(text: str) -> tuple[str, list[str]]:
    """Remove quarantined sentences. Returns (clean_text, removed)."""
    removed: list[str] = []
    # Split lightly on sentence boundaries; keep separators.
    parts = re.split(r"(?<=[.])\s+", text)
    kept: list[str] = []
    for part in parts:
        candidate = part.strip()
        if not candidate:
            continue
        if is_quarantined_sentence(candidate) or _contains_amc_aum(candidate):
            removed.append(candidate)
            continue
        if LAUNCH_1999.search(candidate):
            cleaned = LAUNCH_1999.sub("", candidate).strip(" ;")
            removed.append(candidate)
            if cleaned and not is_quarantined_sentence(cleaned):
                kept.append(cleaned)
            continue
        if AMC_WIDE_AUM.search(candidate):
            cleaned = AMC_WIDE_AUM.sub("", candidate).strip(" ;")
            removed.append(candidate)
            if cleaned:
                kept.append(cleaned)
            continue
        kept.append(candidate)
    return collapse_ws(" ".join(kept)), removed


def drop_rating_pairs(lines: list[str]) -> list[str]:
    """Drop `Rating` / value pairs from Fund Details label lines."""
    out: list[str] = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if RATING_LABEL.match(line.strip()):
            skip_next = True
            continue
        out.append(line)
    return out


def assert_no_quarantine(text: str) -> None:
    """Raise if quarantined content remains in normalized text."""
    if "10 Dec 1999" in text:
        raise ValueError(f"quarantined launch date survived: {text[:120]!r}")
    if "9,67,910" in text:
        raise ValueError(f"quarantined AMC-wide AUM survived: {text[:120]!r}")
    if re.search(r"\bRating\s*:\s*\d+", text, re.I):
        raise ValueError(f"quarantined Rating survived: {text[:120]!r}")


def collapse_ws(text: str) -> str:
    text = re.sub(r"[;]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" ;")


def _contains_amc_aum(text: str) -> bool:
    return "9,67,910" in text and ("Asset Under Management" in text or "AUM" in text)
