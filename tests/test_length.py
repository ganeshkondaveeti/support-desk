"""Length counters (P1.4)."""

from __future__ import annotations

from app.guardrails.length import count_bullets, count_sentences, count_words


def test_count_sentences_handles_abbreviations_and_money() -> None:
    text = (
        "Min SIP is Rs. 100. Expense ratio is 1.02% p.a. "
        "See e.g. the factsheet for ₹1,000.50 examples. Done."
    )
    assert count_sentences(text) == 4


def test_count_sentences_decimal_not_split() -> None:
    assert count_sentences("NAV is 1243.32 today.") == 1
    assert count_sentences("Stamp duty is 0.005%. That is low.") == 2


def test_count_words_strips_markdown_and_review_tags() -> None:
    md = "## Pulse\n\nFees rose this week. [r_12] **charges** and *brokerage*."
    # Pulse Fees rose this week. charges and brokerage. → 8 tokens
    assert count_words(md) == 8
    assert count_words("Hello **world** [r_99]") == 2


def test_count_bullets_list_and_markdown() -> None:
    assert count_bullets(["a", "b", "", "c"]) == 3
    md = "- Exit load of 1%.\n- Expense ratio: 1.02%.\n* Stamp duty: 0.005%.\n"
    assert count_bullets(md) == 3


def test_empty_text() -> None:
    assert count_sentences("") == 0
    assert count_words("   ") == 0
    assert count_sentences("Hello.") == 1
