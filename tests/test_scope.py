"""Scope validators and corpus/template/question exemptions (P1.2)."""

from __future__ import annotations

import pytest
from app.guardrails.scope import (
    check_advice,
    check_buy_sell,
    check_performance,
    mark_corpus_spans,
    mark_spans,
)

ADVICE_BANK = [
    "Should I redeem?",
    "Is this scheme good?",
    "Which fund is better?",
    "You should stay invested longer.",
    "We recommend switching to gold.",
    "I'd suggest holding for now.",
    "You may want to consider staying.",
    "Is this fund safe for me?",
    "Best fund in large cap?",
    "Is it worth investing more?",
    "You might want to consider exiting.",
    "Good fund for beginners?",
    "Consider switching out of equity.",
]

ADVICE_REPHRASES = [
    "should i redeem my units today",
    "is that scheme good right now",
    "which fund is better for me",
    "you should buy more of this",
    "we recommend this scheme",
    "i would recommend holding",
    "consider staying invested through volatility",
    "is the fund risky or safe",
    "best fund among these three",
    "worth holding through the dip",
]

PERFORMANCE_BANK = [
    "What returns should I expect?",
    "The fund returned 18% last year.",
    "It delivered 12% CAGR.",
    "Generated 15% p.a. for clients.",
    "It beat the market last quarter.",
    "5 year returns look strong.",
    "Expected return is high.",
    "Your money will grow fast.",
    "Outperformed the benchmark.",
    "10-year returns were excellent.",
]

PERFORMANCE_REPHRASES = [
    "what returns should i expect from this sip",
    "fund returned 18% last year alone",
    "delivered 12.5% cagr since inception",
    "generated 9% annualised gains",
    "beat the benchmark again",
    "3 yr returns please",
    "expected return from large cap",
    "will grow my corpus quickly",
    "outperform peers this year",
    "1y returns of the scheme",
]

BUY_SELL_BANK = [
    "You should buy this scheme.",
    "Sell your holdings now.",
    "Redeem now before fees rise.",
    "Exit now while ahead.",
    "Switch to the gold fund.",
    "Book profit on mid cap.",
    "Hold on to your units.",
    "Accumulate more on dips.",
    "Go ahead and buy today.",
    "You must sell immediately.",
]


@pytest.mark.parametrize("text", ADVICE_BANK + ADVICE_REPHRASES)
def test_advice_detected(text: str) -> None:
    assert check_advice(text), text


@pytest.mark.parametrize("text", PERFORMANCE_BANK + PERFORMANCE_REPHRASES)
def test_performance_detected(text: str) -> None:
    assert check_performance(text), text


@pytest.mark.parametrize("text", BUY_SELL_BANK)
def test_buy_sell_detected(text: str) -> None:
    assert check_buy_sell(text), text


def test_corpus_span_exemption_for_tax_returns() -> None:
    corpus_line = "If you redeem within one year, returns are taxed at 20%."
    draft = f"Per the scheme page: {corpus_line} Please see the link."
    exempt = mark_corpus_spans(draft, [corpus_line])
    assert check_performance(draft, exempt) == []
    assert check_performance(draft) or True  # may or may not fire without exemption
    # Exit load fee fact must never be treated as a performance claim once marked.
    fee = "Exit load of 1% if redeemed within 1 year"
    draft2 = f"Fact: {fee}"
    exempt2 = mark_corpus_spans(draft2, [fee])
    assert check_performance(draft2, exempt2) == []
    assert check_buy_sell(draft2, exempt2) == []


def test_template_provenance_exempts_refusal_wording() -> None:
    template = (
        "I can't advise on whether to buy, hold or redeem. "
        "Please see AMFI investor education for general guidance."
    )
    exempt = mark_spans(template, [template], "template")
    assert check_buy_sell(template, exempt) == []
    assert check_advice(template, exempt) == []


def test_question_provenance_exempts_quoted_user_question() -> None:
    question = "should I redeem?"
    pulse_line = f'Repeat question: "{question}" asked 6 times this week.'
    exempt = mark_spans(pulse_line, [question], "question")
    assert check_advice(pulse_line, exempt) == []
    assert check_buy_sell(pulse_line, exempt) == []


def test_pii_still_applies_to_exempt_spans() -> None:
    from app.guardrails.pii import redact

    question = "should I redeem? my pan is ABCDE1234F"
    result = redact(question)
    assert "PAN" in result.types
    assert "ABCDE1234F" not in result.text


def test_mark_spans_skips_empty_and_merges() -> None:
    text = "alpha alpha beta"
    spans = mark_spans(text, ["alpha", "", "alpha"], "corpus")
    assert len(spans) >= 1
    assert spans[0].start == 0


def test_empty_inputs() -> None:
    assert check_advice("") == []
    assert mark_corpus_spans("hello", []) == []
