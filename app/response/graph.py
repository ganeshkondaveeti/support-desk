"""LangGraph response loop (architecture §8.2).

Topology:
  entry_router → redaction_guard → facts|pattern|compliance → orchestrator → reviewer
  reviewer pass → END
  reviewer fail & rework_count==0 → mark_rework → orchestrator
  reviewer fail & rework_count==1 → END (blocked)

Refinement entry:
  shorter|other → orchestrator
  different_scheme → facts → orchestrator
  check_fee_issue → pattern → orchestrator
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.guardrails.pii import scan
from app.response.agents.compliance import compliance_node
from app.response.agents.facts import facts_node
from app.response.agents.pattern import pattern_node
from app.response.orchestrator import orchestrator_node
from app.response.reviewer import reviewer_node
from app.response.state import TicketState

logger = logging.getLogger(__name__)

AGENT_NODES = ("facts", "pattern", "compliance")

_CHECKPOINTER: SqliteSaver | None = None
_CHECKPOINTER_CONN: sqlite3.Connection | None = None
_COMPILED: Any = None


class RedactionGuardError(RuntimeError):
    """Raised when redacted question still contains scannable PII."""


def entry_router(
    state: TicketState,
) -> Literal["redaction_guard", "orchestrator", "facts", "pattern"]:
    """Route new tickets through the guard; refinements to scoped nodes."""
    kind = state.get("refine_kind")
    if not kind:
        return "redaction_guard"
    if kind == "shorter":
        return "orchestrator"
    if kind == "different_scheme":
        return "facts"
    if kind == "check_fee_issue":
        return "pattern"
    return "orchestrator"


def redaction_guard(state: TicketState) -> dict[str, Any]:
    """Assert the question is already redacted (scan == [])."""
    question = state.get("question") or ""
    found = scan(question)
    if found:
        raise RedactionGuardError(f"question still contains PII types: {found}")
    return {}


def mark_rework(state: TicketState) -> dict[str, Any]:
    """First reviewer fail: bump rework_count and feed failure codes to orchestrator."""
    reviewer = state.get("reviewer") or {}
    failures = list(reviewer.get("failures") or [])
    logger.info("mark_rework failures=%s", failures)
    return {"rework_count": 1, "failures": failures}


def after_reviewer(
    state: TicketState,
) -> Literal["mark_rework", "__end__"]:
    """Pass → END; fail with rework_count==0 → rework; else END (blocked)."""
    reviewer = state.get("reviewer") or {}
    status = reviewer.get("status") or "fail"
    if status == "pass":
        return END
    rework_count = int(state.get("rework_count") or 0)
    if rework_count == 0:
        return "mark_rework"
    return END


def _fan_out(_state: TicketState) -> list[str]:
    return list(AGENT_NODES)


def build_graph() -> StateGraph:
    graph: StateGraph = StateGraph(TicketState)
    graph.add_node("redaction_guard", redaction_guard)
    graph.add_node("facts", facts_node)
    graph.add_node("pattern", pattern_node)
    graph.add_node("compliance", compliance_node)
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("mark_rework", mark_rework)

    graph.add_conditional_edges(
        START,
        entry_router,
        {
            "redaction_guard": "redaction_guard",
            "orchestrator": "orchestrator",
            "facts": "facts",
            "pattern": "pattern",
        },
    )
    graph.add_conditional_edges(
        "redaction_guard",
        _fan_out,
        {name: name for name in AGENT_NODES},
    )
    for name in AGENT_NODES:
        graph.add_edge(name, "orchestrator")
    graph.add_edge("orchestrator", "reviewer")
    graph.add_conditional_edges(
        "reviewer",
        after_reviewer,
        {"mark_rework": "mark_rework", END: END},
    )
    graph.add_edge("mark_rework", "orchestrator")
    return graph


def checkpoint_path() -> Path:
    settings = get_settings()
    db_url = settings.database_url
    if db_url.startswith("sqlite:///"):
        state_dir = Path(db_url.removeprefix("sqlite:///")).resolve().parent
    else:
        state_dir = Path("data/state").resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "checkpoints.sqlite3"


def get_checkpointer(*, path: Path | None = None) -> SqliteSaver:
    global _CHECKPOINTER, _CHECKPOINTER_CONN
    if _CHECKPOINTER is not None and path is None:
        return _CHECKPOINTER
    db_path = path or checkpoint_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    if path is None:
        _CHECKPOINTER = saver
        _CHECKPOINTER_CONN = conn
    return saver


def reset_graph() -> None:
    global _COMPILED, _CHECKPOINTER, _CHECKPOINTER_CONN
    _COMPILED = None
    if _CHECKPOINTER_CONN is not None:
        try:
            _CHECKPOINTER_CONN.close()
        except Exception:  # noqa: BLE001
            pass
    _CHECKPOINTER = None
    _CHECKPOINTER_CONN = None


def compile_graph(*, checkpointer: SqliteSaver | None = None, path: Path | None = None) -> Any:
    global _COMPILED
    if _COMPILED is not None and checkpointer is None and path is None:
        return _COMPILED
    saver = checkpointer or get_checkpointer(path=path)
    compiled = build_graph().compile(checkpointer=saver)
    if checkpointer is None and path is None:
        _COMPILED = compiled
    return compiled


def invoke_ticket(
    *,
    ticket_id: str,
    question: str,
    pii_types: list[str],
    refine_kind: str | None = None,
    refine_hint: str | None = None,
    refinement_count: int = 0,
    db_session: Any = None,
    compiled: Any = None,
) -> dict[str, Any]:
    """Run the graph for one ticket. ``thread_id = ticket_id``."""
    app = compiled or compile_graph()
    payload: TicketState = {
        "ticket_id": ticket_id,
        "question": question,
        "pii_types": list(pii_types),
        "refine_kind": refine_kind,
        "refine_hint": refine_hint,
        # Each new ticket / refinement pass gets its own single rework allowance.
        "rework_count": 0,
        "refinement_count": refinement_count,
        "failures": [],
        "agent_runs": {},
    }
    _ = db_session
    config = {"configurable": {"thread_id": ticket_id}}
    result = app.invoke(payload, config=config)
    return result


async def ainvoke_ticket(
    *,
    ticket_id: str,
    question: str,
    pii_types: list[str],
    refine_kind: str | None = None,
    refine_hint: str | None = None,
    refinement_count: int = 0,
    db_session: Any = None,
    compiled: Any = None,
) -> dict[str, Any]:
    """Async wrapper. Sync SqliteSaver → invoke."""
    return invoke_ticket(
        ticket_id=ticket_id,
        question=question,
        pii_types=pii_types,
        refine_kind=refine_kind,
        refine_hint=refine_hint,
        refinement_count=refinement_count,
        db_session=db_session,
        compiled=compiled,
    )
