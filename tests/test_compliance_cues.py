"""Question-side compliance detection over the eval fixtures (arch §8.4, eval GC-05/06, AQ-03)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.response.agents.compliance import _cue_flags

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "questions"


def _rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line]


@pytest.mark.parametrize("row", _rows("advice.jsonl"), ids=lambda r: r["id"])
def test_every_advice_fixture_is_flagged_as_advice(row: dict) -> None:
    advice, _perf = _cue_flags(row["text"])
    assert advice, row["text"]


@pytest.mark.parametrize("row", _rows("returns.jsonl"), ids=lambda r: r["id"])
def test_every_returns_fixture_is_performance_not_advice(row: dict) -> None:
    advice, perf = _cue_flags(row["text"])
    assert perf, row["text"]
    # Row 1 wins over row 2 in the decision table, so a returns question must not
    # also look like advice (e.g. "Expected CAGR if I stay invested …").
    assert not advice, row["text"]


@pytest.mark.parametrize("row", _rows("benign_facts.jsonl"), ids=lambda r: r["id"])
def test_benign_fact_questions_are_not_refused(row: dict) -> None:
    advice, perf = _cue_flags(row["text"])
    assert not advice and not perf, row["text"]


@pytest.mark.parametrize(
    "text",
    [
        "Should I stay invested in HDFC Mid Cap?",
        "Is it the right time to buy HDFC Small Cap?",
        "Which one is safer, gold or silver FoF?",
        "I should just sell everything?",
        # Found while building samples/five_questions_with_drafts.md (P12.3).
        "Should I move my money from HDFC Mid Cap Fund to HDFC Small Cap Fund?",
        "Which HDFC fund can I put money in for better returns this year?",
        "Should we shift our SIP to the gold FoF?",
    ],
)
def test_more_advice_phrasings(text: str) -> None:
    assert _cue_flags(text)[0], text


@pytest.mark.parametrize(
    "text",
    [
        "How do I transfer my HDFC Large Cap units to another demat account?",
        "Can I move my SIP date to the 5th of the month?",
        "What is the exit load on HDFC Mid Cap Fund?",
    ],
)
def test_logistics_questions_are_not_advice(text: str) -> None:
    assert not _cue_flags(text)[0], text
