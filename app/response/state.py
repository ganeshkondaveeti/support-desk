"""TicketState and agent output TypedDicts (architecture §8.3)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict


def _last_write(left: Any, right: Any) -> Any:
    """Last-write-wins reducer (avoids InvalidUpdateError on parallel keys)."""
    return right if right is not None else left


def _merge_runs(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Merge per-agent run records written in the same superstep."""
    return {**(left or {}), **(right or {})}


class FactsOut(TypedDict):
    scheme_id: str | None
    section: str | None
    covered: bool
    answer_sentences: list[str]
    citation_url: str | None
    snapshot: Literal["A", "B"] | None
    last_checked: str | None
    searched: dict[str, Any]
    multi_scheme: NotRequired[list[str]]
    family: NotRequired[str | None]
    family_schemes: NotRequired[list[str]]


class PatternOut(TypedDict):
    theme_id: str
    trend: Literal["recurring", "rising", "one_off"]
    review_count: int
    window: tuple[str, str] | list[str]
    review_ids_sample: list[str]
    in_week_repeats: int
    repeat_window: tuple[str, str] | list[str]


class ComplianceOut(TypedDict):
    advice: bool
    performance_claim: bool
    pii_detected: bool
    pii_types: list[str]
    flag: NotRequired[str]


class DraftOut(TypedDict, total=False):
    reply_text: str
    citation_url: str | None
    last_checked: str | None
    row: int | None
    origin: str
    routing_note: dict[str, Any]
    disagreement: str | None
    close_on_send: bool
    edu_url: str | None


class ReviewerOut(TypedDict, total=False):
    status: Literal["pass", "fail", "blocked"]
    failures: list[str]
    warnings: list[str]
    flags: list[str]


class TicketState(TypedDict, total=False):
    ticket_id: str
    question: str  # REDACTED ONLY
    pii_types: list[str]
    refine_kind: str | None
    refine_hint: str | None
    facts: Annotated[FactsOut | None, _last_write]
    pattern: Annotated[PatternOut | None, _last_write]
    compliance: Annotated[ComplianceOut | None, _last_write]
    draft: Annotated[DraftOut | None, _last_write]
    reviewer: Annotated[ReviewerOut | None, _last_write]
    rework_count: int
    refinement_count: int
    failures: list[str]
    agent_runs: Annotated[dict[str, Any], _merge_runs]
