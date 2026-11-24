"""Lead-ins and family leads are prefixes, never a free pass (arch §8.6, eval GC-02)."""

from __future__ import annotations

import pytest
from app.response.reviewer import _check_uncited
from app.response.templates import ADVICE_REFUSAL, CANONICAL_LEAD

LARGE_CAP = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
FACT = "Exit load of 1% if redeemed within 1 year."


@pytest.mark.parametrize(
    "text",
    [
        f"Per the scheme page: {FACT}",
        f"From the scheme information: {FACT}",
        f"For the equity schemes (HDFC Large Cap, HDFC Mid Cap, HDFC Small Cap): {FACT}",
        f"For HDFC's equity schemes in this list (HDFC Large Cap, HDFC Mid Cap): {FACT} "
        f"{CANONICAL_LEAD}",
        f"{ADVICE_REFUSAL} {FACT}",
    ],
)
def test_grounded_text_passes(text: str) -> None:
    assert _check_uncited(text, citation=LARGE_CAP, row=4, facts=None) == []


@pytest.mark.parametrize(
    "text",
    [
        # A lead-in followed by a claim that is not in the corpus.
        "Per the scheme page: Exit load: Nil.",
        "From the scheme information: returns of 18% a year.",
        # A family lead naming a scheme outside the corpus.
        f"For the equity schemes (HDFC Large Cap, HDFC Flexi Cap): {FACT}",
        # A family lead followed by an ungrounded claim.
        "For the equity schemes (HDFC Large Cap, HDFC Mid Cap): charges are 2%.",
        # A corpus fact with an ungrounded claim appended in the same sentence.
        "Exit load of 1% if redeemed within 1 year and returns are 20%.",
        # A template sentence with extra text in it.
        f"{CANONICAL_LEAD[:-1]} and the fee is 3%.",
    ],
)
def test_ungrounded_text_fails(text: str) -> None:
    assert _check_uncited(text, citation=LARGE_CAP, row=4, facts=None) == ["UNCITED_CLAIM"]
