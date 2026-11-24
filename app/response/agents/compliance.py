"""Compliance agent: model flag ORed with regex detectors (arch §8.4)."""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.guardrails.scope import check_advice, check_buy_sell, check_performance
from app.llm.client import LLMClient, get_llm_client, match_allowed
from app.response.state import ComplianceOut

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "compliance.md"

FLAGS = ("advice", "performance_claim", "none")


class ComplianceFlag(StrEnum):
    advice = "advice"
    performance_claim = "performance_claim"
    none = "none"


# Ported / tightened cues from M3 + scope validators (question side).
_ACTION = (
    r"(?:redeem|buy|sell|hold|invest|exit|switch|stay\s+invested|stay|continue|keep|"
    r"stop|pause|withdraw|top\s+up)"
)
_MODAL = r"(?:should|shall|do|can|could|would|must|may|ought\s+to)"
_SUBJECT = r"(?:i|we|one|someone)"
_SCHEME_WORD = r"(?:scheme|fund|sip|etf|fof|investment)"
_SCHEME_REF = (
    rf"(?:hdfc\b[^.?!]{{0,30}}?|{_SCHEME_WORD}|large\s*cap|mid\s*cap|small\s*cap|gold|silver|elss)"
)

_ADVICE_CUES: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b{_MODAL}\s+{_SUBJECT}\s+(?:\w+\s+){{0,3}}?{_ACTION}\b", re.I),
    re.compile(rf"\b{_ACTION}\s+or\s+not\b|\b(?:to\s+)?{_ACTION}\s+or\s+{_ACTION}\s*\?", re.I),
    re.compile(
        rf"\b(?:whether|if)\s+(?:to|{_SUBJECT}\s+{_MODAL})\s+(?:\w+\s+){{0,2}}?{_ACTION}\b",
        re.I,
    ),
    re.compile(
        rf"\bis\s+(?:this\s+|that\s+|it\s+|the\s+)?{_SCHEME_REF}\b[^.?!]{{0,30}}?"
        r"\b(?:good|bad|safe|risky|worth|better|right|wise)\b",
        re.I,
    ),
    re.compile(rf"\b(?:a\s+)?good\s+(?:{_SCHEME_WORD}|idea|time|choice|bet)\b", re.I),
    re.compile(r"\bworth\s+(?:it|investing|buying|holding|keeping|continuing|staying)\b", re.I),
    re.compile(r"\bstay(?:ing)?\s+invested\b", re.I),
    re.compile(
        r"\b(?:recommend|advi[cs]e|suggest)\w*\b[^.?!]{0,40}?"
        rf"\b(?:{_ACTION}|which\s+{_SCHEME_WORD}|what\s+(?:should|to)\s+{_SUBJECT}\s+do)\b"
        rf"|\b(?:which|what)\s+{_SCHEME_WORD}\s+(?:should|to|is\s+best|is\s+better)\b"
        r"|\bbest\s+(?:scheme|fund|etf|fof)s?\b",
        re.I,
    ),
)

_PERFORMANCE_CUES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:cagr|xirr|irr|absolute\s+returns?|rolling\s+returns?|trailing\s+returns?)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:what|how\s+much|how\s+many|show|tell|expect(?:ed|ing)?|get|earn)\b"
        r"[^.?!]{0,30}?\breturns?\b"
        rf"|\breturns?\s+(?:on|of|from|for)\s+(?:the\s+|this\s+|my\s+)?"
        rf"(?:{_SCHEME_REF}|capital|money)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:rate\s+of|annual(?:i[sz]ed)?|yearly|expected|past|historical|average|"
        r"total|\d+[\s-]?(?:year|yr|y|month|m))\s+returns?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:past|historical|fund|scheme|recent|\d+[\s-]?(?:year|yr))\s+performance\b"
        r"|\bhow\s+(?:is|has|did|does|will|would)\b[^.?!]{0,40}?\bperform(?:ed|ing|s)?\b"
        r"|\btrack\s+record\b",
        re.I,
    ),
)


def run_compliance(
    question: str,
    pii_types: list[str] | tuple[str, ...] = (),
    *,
    llm: LLMClient | None = None,
) -> tuple[ComplianceOut, dict[str, Any]]:
    if not isinstance(question, str):
        raise TypeError("compliance expects a str")
    client = llm or get_llm_client()
    client = LLMClient(backend=client.backend, call_label="compliance")

    prompt = PROMPT_PATH.read_text(encoding="utf-8") + "\n\nQuestion:\n" + question
    result = client.choose(prompt, ComplianceFlag, timeout=8.0)

    via_cues = False
    model_advice = False
    model_perf = False
    flag = "none"

    if result.validated is None and (
        result.raw == "ERROR" or result.backend == "cues_only" or result.error is not None
    ):
        via_cues = True
        cue_advice, cue_perf = _cue_flags(question)
        model_advice, model_perf = cue_advice, cue_perf
        if cue_advice:
            flag = "advice"
        elif cue_perf:
            flag = "performance_claim"
    elif isinstance(result.validated, ComplianceFlag):
        flag = result.validated.value
        model_advice = flag == "advice"
        model_perf = flag == "performance_claim"
    elif isinstance(result.validated, dict):
        matched = match_allowed(result.validated.get("flag"), FLAGS)
        flag = matched or "none"
        model_advice = flag == "advice"
        model_perf = flag == "performance_claim"
    else:
        flag = "none"

    regex_advice = _regex_advice(question)
    regex_perf = _regex_performance(question)
    advice = model_advice or regex_advice
    performance_claim = model_perf or regex_perf
    types = [str(t) for t in pii_types]
    output: ComplianceOut = {
        "advice": advice,
        "performance_claim": performance_claim,
        "pii_detected": bool(types),
        "pii_types": types,
        "flag": "advice" if advice else ("performance_claim" if performance_claim else "none"),
    }
    metrics = {
        "agent": "compliance",
        "model_raw_choice": result.raw,
        "validated_choice": flag,
        "written_output": dict(output),
        "latency_ms": result.latency_ms,
        "via": "cues" if via_cues else "model",
    }
    logger.info(
        "compliance advice=%s perf=%s pii=%s",
        advice,
        performance_claim,
        bool(types),
    )
    return output, metrics


def _cue_flags(text: str) -> tuple[bool, bool]:
    advice = any(c.search(text) for c in _ADVICE_CUES) or _regex_advice(text)
    perf = any(c.search(text) for c in _PERFORMANCE_CUES) or _regex_performance(text)
    return advice, perf


def _regex_advice(text: str) -> bool:
    if check_advice(text) or check_buy_sell(text):
        return True
    return any(c.search(text) for c in _ADVICE_CUES)


def _regex_performance(text: str) -> bool:
    if check_performance(text):
        return True
    return any(c.search(text) for c in _PERFORMANCE_CUES)


def compliance_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node: reads question + pii_types only."""
    question = state["question"]
    pii_types = list(state.get("pii_types") or [])
    output, metrics = run_compliance(question, pii_types)
    return {"compliance": output, "agent_runs": {"compliance": metrics}}
