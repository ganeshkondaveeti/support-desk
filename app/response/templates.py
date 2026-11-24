"""Fixed reply templates (architecture §8.5). Ported from M1 refusal wording."""

from __future__ import annotations

import re

from app.corpus.loader import A_LAST_CHECKED, B_LAST_CHECKED
from app.corpus.normalize import CANONICAL_SECTION_LIST
from app.guardrails.allowlists import EDU_URLS

# Primary educational link for advice refusals (arch §8.5 row 1).
AMFI_EDU_URL = "https://www.amfiindia.com/investor-corner"
SEBI_EDU_URL = "https://investor.sebi.gov.in/"

assert AMFI_EDU_URL in EDU_URLS
assert SEBI_EDU_URL in EDU_URLS

ADVICE_REFUSAL = (
    "We can't offer investment advice or recommend whether to buy, hold or redeem. "
    "For general guidance on mutual funds, please see the AMFI investor education page."
)

PERFORMANCE_REFUSAL = (
    "We can't share or project return figures. "
    "You can view the scheme's official details on its page."
)

CANONICAL_LEAD = "This is our standard explanation of this charge."

FACT_LEAD_INS: tuple[str, ...] = (
    "Per the scheme page:",
    "From the scheme information:",
)

FAMILY_LEAD = "For the {family} schemes ({schemes}):"

DISAGREEMENT_BANNER = (
    "Facts agent has a clean, cited answer, but Pattern shows {repeats} identical "
    "questions this week ({trend}). Draft provided; ticket should NOT be closed — "
    "escalated to Product (Pricing)."
)

SCHEME_DISPLAY: dict[str, str] = {
    "large_cap": "HDFC Large Cap",
    "mid_cap": "HDFC Mid Cap",
    "small_cap": "HDFC Small Cap",
    "gold_fof": "HDFC Gold ETF FoF",
    "silver_fof": "HDFC Silver ETF FoF",
    "elss_tax_saver": "HDFC ELSS Tax Saver",
}


def not_covered_reply(
    *,
    schemes: list[str] | None = None,
    sections: list[str] | None = None,
    snapshots: list[str] | None = None,
) -> str:
    """Row-5 template. Lists schemes / sections / snapshot dates searched."""
    scheme_list = schemes or sorted(SCHEME_DISPLAY)
    section_list = sections or list(CANONICAL_SECTION_LIST)
    snap = snapshots or ["A", "B"]
    dates: list[str] = []
    if "A" in snap:
        dates.append(A_LAST_CHECKED)
    if "B" in snap:
        dates.append(B_LAST_CHECKED)
    if not dates:
        dates = [A_LAST_CHECKED, B_LAST_CHECKED]
    scheme_names = [SCHEME_DISPLAY.get(s, s) for s in scheme_list]
    return (
        "This isn't covered by the scheme information we hold. "
        f"We searched: {', '.join(scheme_names)} — sections: {', '.join(section_list)} "
        f"(scheme pages as of {' and '.join(dates)})."
    )


_SEARCHED_RE = re.compile(
    r"^We searched: (?P<schemes>.+?) — sections: (?P<sections>.+?) "
    r"\(scheme pages as of (?P<dates>.+?)\)\.$"
)


def is_not_covered_search_sentence(sentence: str) -> bool:
    """
    True only for the row-5 "We searched: …" sentence as ``not_covered_reply`` builds it.

    Every scheme name, section and date must come from the fixed lists, so any added
    claim (a number, another fund, extra text) fails the reviewer's uncited check.
    """
    match = _SEARCHED_RE.match(sentence.strip())
    if match is None:
        return False
    schemes = match.group("schemes").split(", ")
    sections = match.group("sections").split(", ")
    dates = match.group("dates").split(" and ")
    known_schemes = set(SCHEME_DISPLAY.values())
    known_sections = set(CANONICAL_SECTION_LIST)
    known_dates = {A_LAST_CHECKED, B_LAST_CHECKED}
    return (
        len(set(schemes)) == len(schemes)
        and set(schemes) <= known_schemes
        and len(set(sections)) == len(sections)
        and set(sections) <= known_sections
        and len(set(dates)) == len(dates)
        and set(dates) <= known_dates
    )


def all_template_sentences() -> frozenset[str]:
    """Exact template sentences the reviewer treats as cited (arch §8.6)."""
    sentences = {
        ADVICE_REFUSAL,
        PERFORMANCE_REFUSAL,
        CANONICAL_LEAD,
        *FACT_LEAD_INS,
    }
    # Family lead-ins vary by family name; match the fixed stem.
    sentences.add("For the equity schemes")
    sentences.add("For the fof schemes")
    # Canonical family reuse lead (arch §9.2).
    sentences.add("For HDFC's equity schemes in this list")
    sentences.add("For HDFC's fof schemes in this list")
    # Not-covered bodies vary; the fixed stem is enough for substring match.
    sentences.add("This isn't covered by the scheme information we hold.")
    return frozenset(sentences)


TEMPLATE_SENTENCES: frozenset[str] = all_template_sentences()
