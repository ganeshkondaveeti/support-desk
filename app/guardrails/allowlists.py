"""Closed lists for model outputs and URL checks (architecture §6.3)."""

from __future__ import annotations

from collections.abc import Iterable

EDU_URLS: frozenset[str] = frozenset(
    {
        "https://www.amfiindia.com/investor-corner",
        "https://investor.sebi.gov.in/",
    }
)

PUBLIC_DOMAINS: frozenset[str] = frozenset(
    {
        "groww.in",
        "hdfcfund.com",
        "amfiindia.com",
        "sebi.gov.in",
    }
)

COMPLIANCE_FLAGS: frozenset[str] = frozenset({"advice", "performance_claim", "none"})

# Filled at startup by P2 / P3 loaders via register_*().
SCHEME_IDS: set[str] = set()
SECTIONS: set[str] = set()
THEME_IDS: set[str] = set()
CITATION_URLS: set[str] = set()

# Canonical section titles (arch §5.1). register_corpus may replace/extend.
CANONICAL_SECTIONS: frozenset[str] = frozenset(
    {
        "Key fund facts",
        "Exit load",
        "Stamp duty and tax",
        "Minimum investments",
        "Fund management",
        "Benchmark",
        "Investment objective",
    }
)

# Fixed weekly taxonomy (arch §7.3). register_themes may replace per run.
FIXED_THEME_IDS: frozenset[str] = frozenset(
    {
        "trading_charts",
        "money_movement",
        "reliability",
        "fees_brokerage",
        "other",
    }
)


def register_schemes(ids: Iterable[str]) -> None:
    SCHEME_IDS.clear()
    SCHEME_IDS.update(ids)


def register_sections(sections: Iterable[str]) -> None:
    SECTIONS.clear()
    SECTIONS.update(sections)


def register_themes(ids: Iterable[str]) -> None:
    THEME_IDS.clear()
    THEME_IDS.update(ids)


def register_citation_urls(urls: Iterable[str]) -> None:
    CITATION_URLS.clear()
    CITATION_URLS.update(urls)


def register_corpus(
    *,
    scheme_ids: Iterable[str],
    sections: Iterable[str],
    citation_urls: Iterable[str],
) -> None:
    """P2 loader entry point."""
    register_schemes(scheme_ids)
    register_sections(sections)
    register_citation_urls(citation_urls)


def is_public_url(url: str) -> bool:
    """True when the URL's host is under PUBLIC_DOMAINS (or an EDU_URLS exact match)."""
    if url in EDU_URLS:
        return True
    host = _host(url)
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in PUBLIC_DOMAINS)


def _host(url: str) -> str:
    # Avoid importing urllib for a tiny parse; strip scheme and path.
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host.lower()
