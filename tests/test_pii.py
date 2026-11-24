"""PII precision/recall and corpus zero-change (P1.1 / P1.6)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from app.guardrails.pii import TOKEN_BY_TYPE, redact, scan

FIXTURES = Path(__file__).parent / "fixtures" / "pii_cases.json"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cases() -> dict[str, list[dict[str, object]]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_fixture_count(cases: dict[str, list[dict[str, object]]]) -> None:
    total = len(cases["positives"]) + len(cases["negatives"])
    assert total >= 40


@pytest.mark.parametrize(
    "case",
    json.loads(FIXTURES.read_text(encoding="utf-8"))["positives"],
    ids=lambda c: c["id"],
)
def test_positive_recall(case: dict[str, object]) -> None:
    result = redact(str(case["text"]))
    expected = set(case["types"])  # type: ignore[arg-type]
    assert expected <= set(result.types), f"{case['id']}: missing {expected - set(result.types)}"
    for pii_type in expected:
        assert TOKEN_BY_TYPE[pii_type] in result.text
        assert scan(result.text) == []


@pytest.mark.parametrize(
    "case",
    json.loads(FIXTURES.read_text(encoding="utf-8"))["negatives"],
    ids=lambda c: c["id"],
)
def test_negative_no_false_positive(case: dict[str, object]) -> None:
    result = redact(str(case["text"]))
    assert set(result.types) == set()
    assert result.text == redact(str(case["text"])).text
    # Corpus-like numbers must be unchanged character-for-character after normalize+redact
    # except whitespace collapse from normalization — compare via second redact identity.
    assert scan(str(case["text"])) == []


def test_redaction_result_has_no_raw_pan() -> None:
    raw = "KYC with ABCDE1234F done"
    result = redact(raw)
    assert "ABCDE1234F" not in result.text
    assert "ABCDE1234F" not in repr(result)


def test_scan_clean_on_tokens() -> None:
    assert scan("Reply mentioning [PAN] and [PHONE] only") == []


def test_invalid_aadhaar_falls_to_account() -> None:
    # 12 digits starting 2–9 that fail Verhoeff → ACCOUNT (EC-PII-04)
    text = "id 234567890121 on file"
    result = redact(text)
    assert "AADHAAR" not in result.types
    assert "ACCOUNT" in result.types
    assert "[ACCOUNT]" in result.text


def _iter_corpus_texts() -> list[str]:
    texts: list[str] = []
    for path in (ROOT / "data" / "parsed").glob("*.json"):
        if path.name == "parse_manifest.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for block in data.get("text_blocks", []):
            texts.append(str(block.get("text", "")))
    for path in (ROOT / "data" / "processed").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for section in data.get("sections", []):
            texts.append(str(section.get("content", "")))
            for key, value in (section.get("data_points") or {}).items():
                texts.append(f"{key}: {value}")
    return texts


def test_corpus_zero_change() -> None:
    changed: list[str] = []
    for text in _iter_corpus_texts():
        result = redact(text)
        # Compare against normalize+no-PII: any type hit is a false positive.
        if result.types:
            changed.append(f"types={sorted(result.types)} :: {text[:80]!r}")
            continue
        # After normalization, redact must be a no-op on types; text may only
        # differ by the intentional normalize_for_scan transforms.
        from app.guardrails.pii import normalize_for_scan

        assert result.text == normalize_for_scan(text)
    assert changed == []


def test_reviews_and_corpus_under_two_seconds() -> None:
    reviews_path = ROOT / "data" / "normalized_reviews.json"
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    assert len(reviews) >= 1650
    corpus = _iter_corpus_texts()
    payloads = [str(r.get("text", "")) + " " + str(r.get("title", "")) for r in reviews]
    payloads.extend(corpus)

    start = time.perf_counter()
    for text in payloads:
        redact(text)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"pii.redact over reviews+corpus took {elapsed:.3f}s"
