"""CLI for the response loop (P6.9).

Examples::

    python -m app.response.cli ask "What is the exit load on HDFC Large Cap?"
    python -m app.response.cli refine <ticket_id> shorter
    python -m app.response.cli approve <ticket_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import clear_settings_cache, get_settings
from app.corpus.loader import load_corpus
from app.db import models
from app.db.session import init_db, session_scope
from app.llm.client import reset_llm_client
from app.response.faults import clear_ticket_fault, set_ticket_fault
from app.response.graph import compile_graph, reset_graph
from app.response.human import approve_ticket, edit_ticket, reject_ticket
from app.response.ingress import create_ticket
from app.response.refinement import RefinementError, refine_ticket


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.response.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="Submit a question through the response loop")
    ask.add_argument("question", help="Raw question text (redacted on ingress)")
    ask.add_argument("--channel", default="cli")
    ask.add_argument("--fault", default=None, help="X-Eval-Ticket fault name (APP_ENV=test)")
    ask.add_argument("--db", default=None, help="Override DATABASE_URL sqlite path")

    refine = sub.add_parser("refine", help="Scoped refinement on an existing ticket")
    refine.add_argument("ticket_id")
    refine.add_argument(
        "kind",
        choices=["shorter", "different_scheme", "check_fee_issue", "other"],
    )
    refine.add_argument("hint", nargs="?", default="")
    refine.add_argument("--db", default=None)

    approve = sub.add_parser("approve", help="Approve & send the pending reply")
    approve.add_argument("ticket_id")
    approve.add_argument("--actor", default="cli")
    approve.add_argument("--db", default=None)

    reject = sub.add_parser("reject", help="Reject the ticket and its pending reply_send")
    reject.add_argument("ticket_id")
    reject.add_argument("--actor", default="cli")
    reject.add_argument("--db", default=None)

    edit = sub.add_parser("edit", help="Human-edit the draft and re-run the reviewer")
    edit.add_argument("ticket_id")
    edit.add_argument("text")
    edit.add_argument("--actor", default="cli")
    edit.add_argument("--db", default=None)

    args = parser.parse_args(argv)
    db_url = _resolve_db(getattr(args, "db", None))
    load_corpus()
    init_db(db_url)
    reset_graph()
    reset_llm_client()
    ckpt = Path(db_url.removeprefix("sqlite:///")).parent / "checkpoints.sqlite3"
    compiled = compile_graph(path=ckpt)

    if args.cmd == "ask":
        return _cmd_ask(args, db_url, compiled)
    if args.cmd == "refine":
        return _cmd_refine(args, db_url, compiled)
    if args.cmd == "approve":
        return _cmd_approve(args, db_url)
    if args.cmd == "reject":
        return _cmd_reject(args, db_url)
    if args.cmd == "edit":
        return _cmd_edit(args, db_url)
    return 1


def _cmd_ask(args: argparse.Namespace, db_url: str, compiled: object) -> int:
    if args.fault:
        set_ticket_fault(args.fault)
    try:
        with session_scope(db_url) as session:
            ticket = create_ticket(
                args.question,
                channel=args.channel,
                session=session,
                compiled=compiled,
            )
            draft = session.scalars(
                select(models.DraftVersion).where(
                    models.DraftVersion.ticket_id == ticket.id,
                    models.DraftVersion.is_current.is_(True),
                )
            ).first()
            payload = {
                "ticket_id": ticket.id,
                "status": ticket.status,
                "priority": ticket.priority,
                "route_team": ticket.route_team,
                "rework_count": ticket.rework_count,
                "pii_types": ticket.pii_types_found,
                "question_redacted": ticket.question_redacted,
                "draft": None
                if draft is None
                else {
                    "reply_text": draft.reply_text,
                    "citation_url": draft.citation_url,
                    "last_checked": draft.last_checked,
                    "routing_note": draft.routing_note,
                    "disagreement": draft.disagreement,
                    "reviewer_result": draft.reviewer_result,
                    "origin": draft.origin,
                },
            }
            print(json.dumps(payload, indent=2, default=str))
            return 0
    finally:
        clear_ticket_fault()


def _cmd_refine(args: argparse.Namespace, db_url: str, compiled: object) -> int:
    with session_scope(db_url) as session:
        try:
            ticket = refine_ticket(
                session,
                args.ticket_id,
                args.kind,
                args.hint,
                compiled=compiled,
            )
        except RefinementError as exc:
            print(json.dumps({"error": exc.code, "message": str(exc), "status": exc.status_code}))
            return 1 if exc.status_code != 409 else 409
        draft = session.scalars(
            select(models.DraftVersion).where(
                models.DraftVersion.ticket_id == ticket.id,
                models.DraftVersion.is_current.is_(True),
            )
        ).first()
        print(
            json.dumps(
                {
                    "ticket_id": ticket.id,
                    "status": ticket.status,
                    "refinement_count": ticket.refinement_count,
                    "reply_text": None if draft is None else draft.reply_text,
                },
                indent=2,
            )
        )
    return 0


def _cmd_approve(args: argparse.Namespace, db_url: str) -> int:
    with session_scope(db_url) as session:
        action = approve_ticket(session, args.ticket_id, args.actor)
        print(
            json.dumps(
                {
                    "action_id": action.id,
                    "status": action.status,
                    "result": action.result,
                },
                indent=2,
                default=str,
            )
        )
    return 0


def _cmd_reject(args: argparse.Namespace, db_url: str) -> int:
    with session_scope(db_url) as session:
        ticket = reject_ticket(session, args.ticket_id, args.actor)
        print(json.dumps({"ticket_id": ticket.id, "status": ticket.status}, indent=2))
    return 0


def _cmd_edit(args: argparse.Namespace, db_url: str) -> int:
    with session_scope(db_url) as session:
        draft = edit_ticket(session, args.ticket_id, args.text, args.actor)
        ticket = session.get(models.Ticket, args.ticket_id)
        print(
            json.dumps(
                {
                    "draft_id": draft.id,
                    "ticket_status": None if ticket is None else ticket.status,
                    "reviewer": draft.reviewer_result,
                    "reply_text": draft.reply_text,
                },
                indent=2,
                default=str,
            )
        )
    return 0


def _resolve_db(override: str | None) -> str:
    if override:
        path = Path(override).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"
    clear_settings_cache()
    return get_settings().database_url


if __name__ == "__main__":
    sys.exit(main())
