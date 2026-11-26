"""Build the committed `samples/` artifacts from real runs of the app (plan P12.2–P12.4).

Nothing in `samples/` is written by hand. This script:

1. Writes `samples/reviews_sample.csv`: 50 reviews (10 per theme), redacted by the
   same `pii.redact()` the weekly loop uses, with the weekly loop's theme assignment.
2. Starts the full app (first-boot seeding on) on a throwaway SQLite DB with
   `MCP_BACKEND=local`, then over HTTP:
   - runs the weekly loop once more, so the pulse sees the seeded tickets'
     repeat questions (the first-boot run happens before seeding);
   - submits four questions (exit load with PII, ELSS exit load, advice, out of scope);
   - approves the pulse's doc_append and email_draft and the fee explainer's
     doc_append (which promotes it to canonical); the TypeScript MCP server's local
     backend writes the doc entry and the email draft;
   - submits a fifth question that reuses the canonical explainer.
3. Copies the MCP outputs to `samples/appended_doc_entry.md` and `samples/email_draft.md`
   and renders `samples/five_questions_with_drafts.md` from the API's ticket detail.

Needs `mcp-server/dist` (`cd mcp-server && npm ci && npm run build`).

    uv run python scripts/build_samples.py                 # rules only (deterministic)
    uv run python scripts/build_samples.py --backend groq  # live agent picks (~16 calls, ~2 min)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
IST = ZoneInfo("Asia/Kolkata")
TOKEN = "samples-admin-token"
PER_THEME = 10
DONE = {"awaiting_human", "blocked", "rejected", "sent"}

# (label, question). The PII values are the eval harness's planted fakes; they're
# redacted at ingress and never reach the DB, the logs or this script's output.
QUESTIONS_BEFORE: list[tuple[str, str]] = [
    (
        "Exit load, with PII in the question",
        "Hi, my PAN is ABCDE1234F and my mobile is 9876543210. What is the exit load "
        "on HDFC Large Cap Fund?",
    ),
    ("ELSS exit load (corpus says Nil)", "Is there an exit load on HDFC ELSS Tax Saver Fund?"),
    (
        "Advice request",
        "Should I move my money from HDFC Mid Cap Fund to HDFC Small Cap Fund for "
        "better returns this year?",
    ),
    (
        "Out of scope",
        "My SIP instalment failed but the money was debited from my bank. When will I "
        "get the refund?",
    ),
]
QUESTION_AFTER: tuple[str, str] = (
    "Canonical reuse (after the fee explainer was approved)",
    "How much exit load is charged on HDFC Mid Cap Fund if I redeem early?",
)


# --------------------------------------------------------------------------- CSV


def build_reviews_csv(out: Path) -> dict[str, int]:
    """50 redacted reviews, 10 per theme, spread evenly across each theme's dates."""
    from app.weekly.cluster import THEME_PRIORITY, cluster_reviews
    from app.weekly.ingest import load_reviews

    ingest = load_reviews()
    clustered = cluster_reviews(
        ingest.reviews, window_start=ingest.window_start, window_end=ingest.window_end
    )
    by_id = {r.id: r for r in ingest.reviews}
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for theme_id in THEME_PRIORITY:
        theme = next(t for t in clustered.themes if t.id == theme_id)
        # Readable rows only, oldest first; then evenly spaced picks.
        members = sorted(
            (by_id[rid] for rid in theme.review_ids if len(by_id[rid].text) >= 40),
            key=lambda r: (r.date, r.id),
        )
        step = len(members) / PER_THEME
        picks = [members[int(i * step)] for i in range(PER_THEME)]
        counts[theme_id] = len(picks)
        for review in picks:
            rows.append(
                {
                    "id": review.id,
                    "source": review.source,
                    "rating": review.rating,
                    "title": review.title,
                    "text": review.text,  # already pii.redact()-ed by load_reviews
                    "date": review.date.isoformat(),
                    "version": review.version or "",
                    "theme_id": theme_id,
                }
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return counts


# ------------------------------------------------------------------- live app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class App:
    def __init__(self, tmp: Path, backend: str) -> None:
        self.tmp = tmp
        self.out = tmp / "mcp-out"
        self.out.mkdir()
        self.pace_s = 25.0 if backend == "groq" else 0.0
        port = _free_port()
        env = os.environ.copy()
        env.update(
            {
                "APP_ENV": "development",
                "ADMIN_TOKEN": TOKEN,
                "DATABASE_URL": f"sqlite:///{tmp / 'samples.sqlite3'}",
                "MCP_BACKEND": "local",
                "LOCAL_DOC_PATH": str(self.out / "appended_doc_entry.md"),
                "LOCAL_EMAIL_PATH": str(self.out / "email_draft.md"),
                "PRODUCT_TEAM_EMAILS": "product-team@example.com",
                "LLM_PROVIDER": "groq" if backend == "groq" else "cues_only",
                "EVAL_FAULTS": "",
                "PYTHONPATH": str(ROOT),
            }
        )
        if backend == "groq":
            from app.config import get_settings  # reads .env too

            settings = get_settings()
            if not (settings.groq_api_key or settings.llm_api_key):
                raise SystemExit("--backend groq needs GROQ_API_KEY (env or .env)")
        self.log = (tmp / "uvicorn.log").open("wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.http = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=300,
        )

    def call(self, method: str, path: str, want: int = 200, **kw: Any) -> Any:
        res = self.http.request(method, path, **kw)
        if res.status_code != want:
            raise RuntimeError(f"{method} {path} → {res.status_code}: {res.text[:300]}")
        return res.json() if res.content else None

    def wait(self, fn: Any, what: str, timeout: float = 600) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"app exited; see {self.tmp / 'uvicorn.log'}")
            try:
                got = fn()
            except httpx.HTTPError:
                got = None
            if got:
                return got
            time.sleep(0.5)
        raise RuntimeError(f"timed out waiting for {what}")

    def close(self) -> None:
        self.http.close()
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()

    # --- flows ---

    def ask(self, text: str) -> dict[str, Any]:
        # Groq's free tier allows 8K tokens/min (~17 agent calls). Pause between
        # tickets so every pick is the model's, not the over-budget rules fallback.
        time.sleep(self.pace_s)
        created = self.call("POST", "/api/tickets", 201, json={"text": text, "channel": "samples"})
        tid = created["id"]
        return self.wait(
            lambda: (t := self.call("GET", f"/api/tickets/{tid}"))["status"] in DONE and t,
            f"ticket {tid}",
        )

    def weekly_run(self) -> dict[str, Any]:
        run_id = self.call("POST", "/api/weekly/run", json={})["run_id"]
        detail = self.wait(
            lambda: (
                (d := self.call("GET", f"/api/weekly/runs/{run_id}"))["status"]
                in {"awaiting_approval", "failed"}
                and d
            ),
            f"weekly run {run_id}",
        )
        if detail["status"] != "awaiting_approval":
            raise RuntimeError(f"weekly run {run_id} ended {detail['status']}: {detail['error']}")
        return detail

    def approve_run_actions(self, detail: dict[str, Any]) -> list[dict[str, Any]]:
        pending = self.call("GET", "/api/actions", params={"status": "pending", "limit": 500})
        wanted = {
            ("pulse", detail["pulse"]["id"], "doc_append"),
            ("fee_explainer", detail["explainer"]["id"], "doc_append"),
            ("pulse", detail["pulse"]["id"], "email_draft"),
        }
        by_key = {(a["subject_type"], a["subject_id"], a["kind"]): a for a in pending["items"]}
        approved = []
        for key in sorted(wanted, key=lambda k: (k[2] != "doc_append", k[0] != "pulse")):
            action = by_key.get(key)
            if action is None:
                raise RuntimeError(f"no pending action for {key}")
            done = self.call("POST", f"/api/actions/{action['id']}/approve")
            if done["status"] != "executed":
                raise RuntimeError(f"action {action['id']} ended {done['status']}")
            approved.append(done)
        return approved


# ------------------------------------------------------------------ markdown


def _agent_line(outputs: list[dict[str, Any]], agent: str) -> str:
    rows = [o for o in outputs if o["agent"] == agent]
    if not rows:
        return "—"
    last = max(rows, key=lambda o: (o["attempt"], o["id"]))
    choice = last.get("validated_choice") or "null"
    raw = last.get("model_raw_choice")
    # Same reading as the UI's ticket panel: no model pick means the code rules decided.
    how = "no model pick; code rules decided" if raw in (None, "", "ERROR") else "model pick"
    return f"`{choice}` ({how})"


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())


def render_question(n: int, label: str, ticket: dict[str, Any]) -> str:
    draft = ticket.get("current_draft") or {}
    reviewer = draft.get("reviewer_result") or {}
    routing = draft.get("routing_note") or {}
    reviewer_line = f"`{reviewer.get('status', '—')}`"
    if reviewer.get("failures"):
        reviewer_line += f" (failures: {', '.join(reviewer['failures'])})"
    if reviewer.get("warnings"):
        reviewer_line += f" (warnings: {', '.join(reviewer['warnings'])})"
    if ticket.get("rework_count"):
        reviewer_line += f", after {ticket['rework_count']} rework round"
    pii = ", ".join(ticket.get("pii_types_found") or []) or "none"
    routing_bits = [
        f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}"
        for k, v in routing.items()
        if v not in (None, "", [], {})
    ]
    lines = [
        f"## {n}. {label}",
        "",
        "**Question (as stored, after redaction)**",
        "",
        _quote(ticket["question_redacted"]),
        "",
        f"- **PII found and redacted:** {pii}",
        f"- **Status:** `{ticket['status']}` · priority `{ticket.get('priority')}` · "
        f"team `{ticket.get('route_team')}`",
        f"- **Decision-table row:** {draft.get('row')} · draft origin `{draft.get('origin')}`",
        f"- **Facts agent:** {_agent_line(ticket['agent_outputs'], 'facts')}",
        f"- **Pattern agent:** {_agent_line(ticket['agent_outputs'], 'pattern')}",
        f"- **Compliance agent:** {_agent_line(ticket['agent_outputs'], 'compliance')}",
        "",
        "**Draft reply**",
        "",
        _quote(draft.get("reply_text") or "(no draft)"),
        "",
        f"- **Citation:** {draft.get('citation_url') or '—'}",
        f"- **Last checked:** {draft.get('last_checked') or '—'}",
        "- **Routing note** (internal, never sent):",
        *(f"  - {bit}" for bit in routing_bits or ["—"]),
        f"- **Disagreement:** {draft.get('disagreement') or 'none'}",
        f"- **Reviewer:** {reviewer_line}",
        "",
    ]
    return "\n".join(lines)


def render_questions(
    items: list[tuple[str, dict[str, Any]]], backend: str, canonical: dict[str, Any]
) -> str:
    stamp = datetime.now(tz=IST).strftime("%Y-%m-%d %H:%M IST")
    head = [
        "# Five sample questions with their drafts",
        "",
        "> **Facts-only. No investment advice.**",
        "",
        f"Generated by `scripts/build_samples.py` on {stamp} against a fresh instance "
        f"(agents backend: `{'groq' if backend == 'groq' else 'cues_only'}`). Each entry "
        "is the API's ticket detail: the redacted question, the three agents' picks, the "
        "orchestrator's draft with its citation and last-checked date, the routing note "
        "and the reviewer's result. None of these replies has been sent; each one waits "
        "in the approval queue as a `reply_send` action.",
        "",
        "Questions 1–4 ran before any fee explainer was approved. Question 5 ran after "
        "the fee explainer's doc append was approved, so the orchestrator reused the "
        "canonical explainer (decision-table row 3).",
        "",
    ]
    body = [render_question(i, label, t) for i, (label, t) in enumerate(items, start=1)]
    active = canonical.get("active") or {}
    reuse = [e for e in active.get("reuse_log") or []]
    tail = [
        "## Canonical explainer reuse log",
        "",
        f"Active canonical explainer `{active.get('id')}` (approved action "
        f"`{active.get('approved_action_id')}`) covers "
        f"{len(active.get('covers') or [])} (scheme, section) pairs and was reused by "
        f"{len(reuse)} ticket(s) in this run.",
        "",
    ]
    return "\n".join(head + body + tail)


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backend", choices=("cues", "groq"), default="cues")
    args = parser.parse_args(argv)

    if not (ROOT / "mcp-server" / "dist" / "index.js").is_file():
        raise SystemExit("build the MCP server first: cd mcp-server && npm ci && npm run build")

    counts = build_reviews_csv(SAMPLES / "reviews_sample.csv")
    print(f"reviews_sample.csv: {sum(counts.values())} rows {counts}")

    with tempfile.TemporaryDirectory(prefix="samples-") as tmp_name:
        app = App(Path(tmp_name), args.backend)
        try:
            app.wait(
                lambda: (h := app.call("GET", "/api/health")).get("seeded") and h,
                "first-boot seeding",
            )
            detail = app.weekly_run()
            repeats = detail["pulse"].get("repeat_questions") or []
            print(
                f"weekly run {detail['id']}: pulse {detail['pulse']['word_count']} words, "
                f"{len(repeats)} repeat-question cluster(s)"
            )

            answered = [(label, app.ask(q)) for label, q in QUESTIONS_BEFORE]
            approved = app.approve_run_actions(detail)
            for a in approved:
                print(f"approved {a['kind']} {a['subject_type']} → {a['status']}")
            canonical = app.call("GET", "/api/explainers/canonical")
            if not canonical.get("active"):
                raise RuntimeError("fee explainer approval did not promote a canonical explainer")
            label, q = QUESTION_AFTER
            answered.append((label, app.ask(q)))
            last = answered[-1][1]["current_draft"] or {}
            if last.get("origin") != "canonical":
                raise RuntimeError(f"question 5 did not reuse the canonical explainer: {last}")
            canonical = app.call("GET", "/api/explainers/canonical")
        finally:
            app.close()

        SAMPLES.mkdir(exist_ok=True)
        for name in ("appended_doc_entry.md", "email_draft.md", "email_draft.eml"):
            src = app.out / name
            if not src.is_file():
                raise RuntimeError(f"MCP local backend did not write {name}")
            shutil.copyfile(src, SAMPLES / name)
        (SAMPLES / "five_questions_with_drafts.md").write_text(
            render_questions(answered, args.backend, canonical), encoding="utf-8"
        )

    for label, t in answered:
        d = t.get("current_draft") or {}
        print(f"- {label}: {t['status']} row={d.get('row')} origin={d.get('origin')}")
    print("samples written to", SAMPLES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
