"""Snapshot B normalization into the 7 canonical sections (architecture §5.1)."""

from __future__ import annotations

import re
from typing import Any

from app.corpus.quarantine import (
    assert_no_quarantine,
    collapse_ws,
    drop_rating_pairs,
    strip_quarantined,
)
from app.guardrails.allowlists import CANONICAL_SECTIONS

CANONICAL_SECTION_LIST: tuple[str, ...] = (
    "Key fund facts",
    "Exit load",
    "Stamp duty and tax",
    "Minimum investments",
    "Fund management",
    "Benchmark",
    "Investment objective",
)
assert set(CANONICAL_SECTION_LIST) == set(CANONICAL_SECTIONS)


def join_label_value_pairs(text: str) -> str:
    """Join `label\\nvalue` pairs as `label: value.` and collapse whitespace."""
    raw_lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in raw_lines if ln]
    lines = drop_rating_pairs(lines)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if i + 1 < len(lines) and _looks_like_label(line) and _looks_like_value(lines[i + 1]):
            value = lines[i + 1].rstrip(":")
            label = line.rstrip(":")
            out.append(f"{label}: {value}.")
            i += 2
            continue
        out.append(line if line.endswith((".", "!", "?")) else f"{line.rstrip(':')}.")
        i += 1
    return collapse_ws(" ".join(out))


def normalize_b_scheme(payload: dict[str, Any]) -> dict[str, str]:
    """
    Map a raw B JSON document to canonical section → normalized text.
    Only sections that can be derived are present; missing keys mean not covered.
    """
    by_name = {s["section_name"]: s.get("content") or "" for s in payload.get("sections", [])}
    # data_points are ignored entirely (quarantine rule 4).
    overview = by_name.get("Fund Overview", "")
    details = by_name.get("Fund Details", "")
    nav_aum = by_name.get("NAV & AUM", "")
    tax = by_name.get("Tax Info & Exit Load", "")

    overview_clean, _ = strip_quarantined(overview)
    overview_clean = collapse_ws(overview_clean.strip(" ;"))

    result: dict[str, str] = {}

    key_facts = _key_fund_facts(details, nav_aum, overview_clean)
    if key_facts:
        result["Key fund facts"] = key_facts

    exit_load = _exit_load(tax)
    if exit_load:
        result["Exit load"] = exit_load

    stamp = _stamp_duty_and_tax(tax)
    if stamp:
        result["Stamp duty and tax"] = stamp

    minimums = _minimum_investments(details, overview_clean)
    if minimums:
        result["Minimum investments"] = minimums

    manager = _fund_management(overview_clean)
    if manager:
        result["Fund management"] = manager

    # Benchmark / Investment objective: A only — never from B.
    for section, text in result.items():
        assert_no_quarantine(text)
        result[section] = _ensure_period(collapse_ws(text))
    return result


def _key_fund_facts(details: str, nav_aum: str, overview_clean: str) -> str:
    parts: list[str] = []
    detail_joined = join_label_value_pairs(details)
    # Keep fund size + expense ratio only; Min. for SIP belongs in Minimum investments.
    for label in ("Fund size (AUM)", "Expense ratio"):
        value = _labeled_value(detail_joined, label)
        if value:
            parts.append(f"{label}: {value}.")

    nav_joined = join_label_value_pairs(nav_aum)
    parts.extend(_normalize_nav_block(nav_joined))

    risk = _riskometer_sentence(overview_clean)
    if risk:
        parts.append(risk)

    return collapse_ws(" ".join(_ensure_period(p) for p in parts if p.strip()))


def _labeled_value(text: str, label: str) -> str | None:
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(.+?)(?:\.\s+[A-Z]|\.\s*$|\.$|$)",
        text,
        re.I,
    )
    if not match:
        return None
    return match.group(1).strip().rstrip(".")


def _normalize_nav_block(joined: str) -> list[str]:
    """Turn NAV label/value join into NAV + NAV date facts when date is present."""
    if not joined:
        return []
    # Patterns like: NAV: 31 Aug '26: ₹1,243.32.  or NAV: 31 Aug '26. ₹1,243.32.
    m = re.search(
        r"NAV:\s*(\d{1,2}\s+\w+\s+'?\d{2,4})\s*:?\s*(₹[\d,]+\.?\d*)",
        joined,
        re.I,
    )
    if m:
        date_raw, nav_val = m.group(1), m.group(2)
        return [f"NAV: {nav_val}.", f"NAV date: {_format_nav_date(date_raw)}."]
    m2 = re.search(r"NAV:\s*(₹[\d,]+\.?\d*)", joined, re.I)
    if m2:
        return [f"NAV: {m2.group(1)}."]
    return [joined]


def _format_nav_date(raw: str) -> str:
    # "31 Aug '26" → "31-Aug-2026"
    raw = raw.strip()
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+'?(\d{2,4})", raw)
    if not m:
        return raw
    day, mon, year = m.group(1), m.group(2)[:3].title(), m.group(3)
    if len(year) == 2:
        year = f"20{year}"
    return f"{int(day):02d}-{mon}-{year}"


def _exit_load(tax: str) -> str:
    lines = [ln.strip() for ln in tax.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if re.fullmatch(r"Exit load", line, re.I) and i + 1 < len(lines):
            return _ensure_period(collapse_ws(lines[i + 1]))
    # Fallback: first line containing "Exit load of" or bare Nil
    for line in lines:
        if re.search(r"Exit load of|\bNil\b", line, re.I) and not line.lower().startswith(
            "exit load, stamp"
        ):
            return _ensure_period(collapse_ws(line))
    return ""


def _stamp_duty_and_tax(tax: str) -> str:
    lines = [ln.strip() for ln in tax.splitlines() if ln.strip()]
    parts: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"Stamp duty on investment", line, re.I):
            if i + 1 < len(lines) and not re.match(
                r"Tax implication|Exit load|from July", lines[i + 1], re.I
            ):
                parts.append(f"Stamp duty on investment: {lines[i + 1].rstrip(':')}.")
                i += 2
                # Skip redundant "from July 1st 2020" artifact line.
                if i < len(lines) and re.match(r"from July", lines[i], re.I):
                    i += 1
                continue
            # Inline form
            inline = line.split(":", 1)
            if len(inline) == 2 and inline[1].strip():
                parts.append(f"Stamp duty on investment: {inline[1].strip()}.")
            i += 1
            continue
        if re.fullmatch(r"Tax implication", line, re.I) and i + 1 < len(lines):
            parts.append(_ensure_period(lines[i + 1]))
            i += 2
            continue
        i += 1
    return collapse_ws(" ".join(parts))


def _minimum_investments(details: str, overview_clean: str) -> str:
    parts: list[str] = []
    detail_joined = join_label_value_pairs(details)
    sip = _labeled_value(detail_joined, "Min. for SIP")
    if sip:
        parts.append(f"Min. for SIP: {sip}.")
    for sentence in _split_sentences(overview_clean):
        if re.search(r"Minimum (?:SIP|Lumpsum) Investment", sentence, re.I):
            parts.append(_ensure_period(sentence))
    return collapse_ws(" ".join(parts))


def _fund_management(overview_clean: str) -> str:
    for sentence in _split_sentences(overview_clean):
        if re.search(r"is the Current Fund Manager", sentence, re.I):
            return _ensure_period(sentence)
    return ""


def _riskometer_sentence(overview_clean: str) -> str:
    for sentence in _split_sentences(overview_clean):
        if re.search(r"\brated\b.+\brisk\b", sentence, re.I):
            return _ensure_period(sentence)
    return ""


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"(?<=[.])\s+", text) if p.strip()]


def _split_facts(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=\.)\s+", text) if p.strip()]


def _looks_like_label(line: str) -> bool:
    if re.search(r"\d", line) and not re.match(r"Min\.|Fund size|Expense|Rating|NAV", line, re.I):
        # Values with digits are rarely labels; allow known labels.
        if not re.match(
            r"(Min\.|Fund size|Expense ratio|Rating|NAV|Stamp duty)",
            line,
            re.I,
        ):
            return False
    return not line.endswith("%") and not line.startswith("₹")


def _looks_like_value(line: str) -> bool:
    if re.match(r"^(Min\.|Fund size|Expense|Rating|Exit load|Stamp|Tax)", line, re.I):
        return False
    return True


def _ensure_period(text: str) -> str:
    text = collapse_ws(text)
    if not text:
        return text
    if text.endswith((".", "!", "?")):
        return text
    return text + "."
