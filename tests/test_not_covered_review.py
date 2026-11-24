"""Row-5 "not covered" replies pass the reviewer; tampered ones don't (arch §8.5, §8.6)."""

from __future__ import annotations

import pytest
from app.response.reviewer import _check_uncited
from app.response.templates import is_not_covered_search_sentence, not_covered_reply

LC = "We searched: HDFC Large Cap — sections: Exit load"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"schemes": ["large_cap"], "sections": ["Exit load"], "snapshots": ["B"]},
        {"schemes": ["elss_tax_saver", "gold_fof"], "snapshots": ["A"]},
    ],
)
def test_not_covered_template_has_no_uncited_claim(kwargs: dict) -> None:
    text = not_covered_reply(**kwargs)
    assert _check_uncited(text, citation=None, row=5, facts=None) == []


@pytest.mark.parametrize(
    "sentence",
    [
        # Extra claim after the list.
        f"{LC} (scheme pages as of 01-Sep-2026). Returns 12%.",
        # A fund that is not in the corpus.
        "We searched: HDFC Flexi Cap — sections: Exit load (scheme pages as of 01-Sep-2026).",
        # A section that is not canonical.
        "We searched: HDFC Large Cap — sections: Returns 18% (scheme pages as of 01-Sep-2026).",
        # A date that is not a snapshot date.
        f"{LC} (scheme pages as of 01-Jan-2027).",
        # Extra text inside the parentheses.
        f"{LC} (scheme pages as of 01-Sep-2026, up 5%).",
        # Duplicates.
        "We searched: HDFC Large Cap, HDFC Large Cap — sections: Exit load"
        " (scheme pages as of 01-Sep-2026).",
    ],
)
def test_tampered_search_sentence_is_not_exempt(sentence: str) -> None:
    text = "This isn't covered by the scheme information we hold. " + sentence
    assert _check_uncited(text, citation=None, row=5, facts=None) == ["UNCITED_CLAIM"]


def test_search_sentence_matcher() -> None:
    good = (
        "We searched: HDFC Large Cap, HDFC Mid Cap — sections: Exit load, Stamp duty and tax"
        " (scheme pages as of 28-Jun-2026 and 01-Sep-2026)."
    )
    assert is_not_covered_search_sentence(good)
    assert not is_not_covered_search_sentence(good.replace("HDFC Mid Cap", "HDFC Flexi Cap"))
    assert not is_not_covered_search_sentence(good.replace("01-Sep-2026", "02-Sep-2026"))
    assert not is_not_covered_search_sentence(good[:-1])  # no closing period
