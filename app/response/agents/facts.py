"""Facts agent: model picks (scheme, section); code writes sentences (arch §8.4)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.corpus.loader import Corpus, load_corpus
from app.corpus.normalize import CANONICAL_SECTION_LIST
from app.corpus.sentences import select_sentences
from app.guardrails.allowlists import SCHEME_IDS
from app.llm.client import ChooseResult, LLMClient, get_llm_client, match_allowed
from app.response.state import FactsOut

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "facts.md"

EQUITY_FAMILY: tuple[str, ...] = ("large_cap", "mid_cap", "small_cap")
FOF_FAMILY: tuple[str, ...] = ("gold_fof", "silver_fof")
FAMILY_BY_ID: dict[str, str] = {
    **{sid: "equity" for sid in EQUITY_FAMILY},
    **{sid: "fof" for sid in FOF_FAMILY},
}

_SCHEME_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "gold_fof",
        re.compile(
            r"\bgold etf fund of fund\b|\bgold etf fof\b|\bgold etf\b|\bhdfc gold\b|\bgold fof\b",
            re.I,
        ),
    ),
    (
        "silver_fof",
        re.compile(r"\bsilver etf fof\b|\bsilver etf\b|\bsilver fof\b|\bhdfc silver\b", re.I),
    ),
    ("elss_tax_saver", re.compile(r"\belss\b|\btax[\s-]?saver\b", re.I)),
    ("large_cap", re.compile(r"\blarge[\s-]?cap\b", re.I)),
    ("mid_cap", re.compile(r"\bmid[\s-]?cap\b", re.I)),
    ("small_cap", re.compile(r"\bsmall[\s-]?cap\b", re.I)),
)

_SECTION_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Exit load", re.compile(r"\bexit[\s-]?load\b", re.I)),
    ("Benchmark", re.compile(r"\bbenchmark\b", re.I)),
    (
        "Investment objective",
        re.compile(
            r"\binvestment objective\b|\bobjective of\b|\bwhat(?:'s| is) the objective\b",
            re.I,
        ),
    ),
    ("Stamp duty and tax", re.compile(r"\bstamp\s*duty\b|\btax(?:es|ed|ation)?\b", re.I)),
    ("Fund management", re.compile(r"\bfund manag(?:er|ement)\b|\bwho manages\b", re.I)),
    (
        "Minimum investments",
        re.compile(
            r"\bminimum investment\b|\bmin(?:imum)?\.?\s+sip\b|\blumpsum\b|\blump\s*sum\b",
            re.I,
        ),
    ),
    (
        "Key fund facts",
        re.compile(
            r"\bkey fund facts\b|\bexpense ratio\b|\briskometer\b|\baum\b|\bfund size\b|\bnav\b",
            re.I,
        ),
    ),
)

_OTHER_AMC = re.compile(
    r"\b(?:nippon|sbi|icici|axis|kotak|mirae|uti|quant|motilal|tata|birla|"
    r"franklin|dsp|bandhan|edelweiss|canara|invesco|ppfas|parag|flexi[\s-]?cap|"
    r"bluechip)\b",
    re.I,
)

# G14: topics the corpus does not hold — force not covered.
_OUT_OF_SECTION: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("lock-in", re.compile(r"\block[\s-]?in\b", re.I)),
    (
        "returns_history",
        re.compile(
            r"\b(?:cagr|xirr|returns?\s+history|historical\s+returns?|"
            r"\d+[\s-]?(?:year|yr|y)\s+returns?|past\s+returns?)\b",
            re.I,
        ),
    ),
    (
        "holdings",
        re.compile(r"\b(?:holdings?|portfolio|top\s+stocks?|stock\s+list)\b", re.I),
    ),
    (
        "brokerage",
        re.compile(r"\b(?:brokerage|dp\s*charg(?:e|es)|demat\s+charg)\b", re.I),
    ),
    (
        "sip_mandate",
        re.compile(r"\b(?:sip\s+(?:date|mandate|auto)|nach\s+mandate|auto[\s-]?debit)\b", re.I),
    ),
    ("kyc", re.compile(r"\bkyc\b", re.I)),
    ("nominee", re.compile(r"\bnominee\b", re.I)),
)

_NAV_HISTORY = re.compile(r"\bnav history\b", re.I)


def run_facts(
    question: str,
    *,
    corpus: Corpus | None = None,
    llm: LLMClient | None = None,
    refine_hint: str | None = None,
) -> tuple[FactsOut, dict[str, Any]]:
    """Classify then write. Returns (FactsOut, agent_run metrics)."""
    if not isinstance(question, str):
        raise TypeError("facts expects a str")
    facts_corpus = corpus or load_corpus()
    client = llm or get_llm_client()
    client = LLMClient(backend=client.backend, call_label="facts")

    scheme_ids = sorted(SCHEME_IDS or facts_corpus.scheme_ids)
    sections = list(CANONICAL_SECTION_LIST)
    # Hint may name a different scheme (different_scheme refinement).
    searchable = question
    if refine_hint:
        searchable = f"{question}\n{refine_hint}"
    multi = detect_schemes(searchable)
    out_of_section = detect_out_of_section(question)
    outside_amc = bool(_OTHER_AMC.search(question)) and not re.search(r"\bhdfc\b", question, re.I)

    prompt = _render_prompt(scheme_ids, sections, facts_corpus) + "\n\nQuestion:\n" + question
    if refine_hint:
        prompt += f"\n\nUser clarification: {refine_hint}"
    schema = {"scheme_id": scheme_ids, "section": sections}
    result = client.choose(prompt, schema, timeout=8.0)

    scheme_id, section, via_cues = _resolve_picks(
        searchable,
        result,
        scheme_ids=scheme_ids,
        sections=sections,
        multi=multi,
        outside_amc=outside_amc,
        out_of_section=out_of_section,
    )

    output = _write(
        facts_corpus,
        searchable,
        scheme_id=scheme_id,
        section=section,
        multi=multi,
        out_of_section=out_of_section,
        outside_amc=outside_amc,
    )
    validated = None
    if scheme_id or section:
        validated = f"{scheme_id or 'null'}|{section or 'null'}"
    metrics = {
        "agent": "facts",
        "model_raw_choice": result.raw,
        "validated_choice": validated,
        "written_output": dict(output),
        "latency_ms": result.latency_ms,
        "via": "cues" if via_cues else "model",
    }
    logger.info("facts covered=%s scheme=%s section=%s", output["covered"], scheme_id, section)
    return output, metrics


def detect_schemes(text: str) -> list[str]:
    """In-corpus schemes named in the question (G10)."""
    found: list[str] = []
    for scheme_id, cue in _SCHEME_CUES:
        if cue.search(text) and scheme_id not in found:
            found.append(scheme_id)
    return found


def detect_out_of_section(text: str) -> str | None:
    for name, cue in _OUT_OF_SECTION:
        if cue.search(text):
            return name
    return None


def _resolve_picks(
    question: str,
    result: ChooseResult,
    *,
    scheme_ids: list[str],
    sections: list[str],
    multi: list[str],
    outside_amc: bool,
    out_of_section: str | None,
) -> tuple[str | None, str | None, bool]:
    use_cues = result.validated is None and (
        result.raw == "ERROR" or result.backend == "cues_only" or result.error is not None
    )
    if use_cues:
        scheme_id = _cue_scheme(question, multi)
        section = _cue_section(question) if scheme_id or multi or not outside_amc else None
        if out_of_section:
            return scheme_id, None, True
        return scheme_id, section, True

    validated = result.validated if isinstance(result.validated, dict) else {}
    scheme_id = match_allowed(validated.get("scheme_id"), scheme_ids)
    section = match_allowed(validated.get("section"), sections)
    if section == "Key fund facts" and _NAV_HISTORY.search(question):
        section = None
    if out_of_section:
        section = None
    if outside_amc:
        scheme_id = None
    if multi and scheme_id and scheme_id not in multi:
        # Prefer the model's pick only when it is one of the named schemes.
        scheme_id = multi[0]
    return scheme_id, section, False


def _cue_scheme(question: str, multi: list[str]) -> str | None:
    if len(multi) == 1:
        return multi[0]
    if len(multi) > 1:
        return multi[0]  # answer one; multi_scheme recorded separately
    found = [sid for sid, cue in _SCHEME_CUES if cue.search(question)]
    if len(found) == 1:
        return found[0]
    return None


def _cue_section(question: str) -> str | None:
    searchable = _NAV_HISTORY.sub(" ", question)
    for title, cue in _SECTION_CUES:
        if cue.search(searchable):
            return title
    return None


def _write(
    corpus: Corpus,
    question: str,
    *,
    scheme_id: str | None,
    section: str | None,
    multi: list[str],
    out_of_section: str | None,
    outside_amc: bool,
) -> FactsOut:
    searched_all = {
        "schemes": sorted(corpus.scheme_ids),
        "sections": list(CANONICAL_SECTION_LIST),
        "snapshots": ["A", "B"],
    }
    base: FactsOut = {
        "scheme_id": scheme_id,
        "section": section,
        "covered": False,
        "answer_sentences": [],
        "citation_url": None,
        "snapshot": None,
        "last_checked": None,
        "searched": searched_all,
        "multi_scheme": multi,
        "family": None,
        "family_schemes": [],
    }

    if out_of_section or outside_amc:
        return base

    if scheme_id and section:
        block = corpus.get(scheme_id, section)
        if block is None:
            base["searched"] = {
                "schemes": [scheme_id],
                "sections": [section],
                "snapshots": ["A", "B"],
                "missing": True,
            }
            return base
        sentences = select_sentences(block, question, limit=2)
        base.update(
            {
                "covered": True,
                "answer_sentences": sentences,
                "citation_url": block.source_url,
                "snapshot": block.snapshot,
                "last_checked": block.last_checked,
                "searched": {
                    "schemes": [scheme_id],
                    "sections": [section],
                    "snapshots": [block.snapshot],
                },
            }
        )
        return base

    # Scheme null + section present → family answer when text identical (arch §8.4).
    if section and not scheme_id:
        family_hit = _family_answer(corpus, section, question)
        if family_hit is not None:
            return family_hit
        return base

    return base


def _family_answer(corpus: Corpus, section: str, question: str) -> FactsOut | None:
    for family_name, members in (("equity", EQUITY_FAMILY), ("fof", FOF_FAMILY)):
        blocks = [corpus.get(sid, section) for sid in members]
        if any(b is None for b in blocks):
            continue
        norms = [_norm_text(b.text) for b in blocks if b is not None]
        if len(set(norms)) != 1:
            continue
        lead = blocks[0]
        assert lead is not None
        sentences = select_sentences(lead, question, limit=2)
        return {
            "scheme_id": lead.scheme_id,
            "section": section,
            "covered": True,
            "answer_sentences": sentences,
            "citation_url": lead.source_url,
            "snapshot": lead.snapshot,
            "last_checked": lead.last_checked,
            "searched": {
                "schemes": list(members),
                "sections": [section],
                "snapshots": [lead.snapshot],
            },
            "multi_scheme": [],
            "family": family_name,
            "family_schemes": list(members),
        }
    return None


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(".")).casefold()


def _render_prompt(scheme_ids: list[str], sections: list[str], corpus: Corpus) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    scheme_lines = []
    for sid in scheme_ids:
        name = corpus.scheme_names.get(sid, sid)
        scheme_lines.append(f"- {sid}: {name}")
    section_lines = [f"- {s}" for s in sections]
    return template.replace("{{scheme_ids}}", "\n".join(scheme_lines)).replace(
        "{{sections}}", "\n".join(section_lines)
    )


def facts_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node: reads only question (+ optional refine_hint); writes facts."""
    # Strip other agent keys so a buggy caller cannot leak them (P5 test).
    question = state["question"]
    hint = state.get("refine_hint") if state.get("refine_kind") == "different_scheme" else None
    output, metrics = run_facts(question, refine_hint=hint)
    return {"facts": output, "agent_runs": {"facts": metrics}}
