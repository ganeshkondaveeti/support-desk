"""Smoke test against a running Support Desk (plan P11.6).

Checks: health, first-boot seeding, the UI shell and a client route, one manual
weekly run, one ticket, and one approval of that ticket's reply. Standard library
only, so it runs anywhere:

    ADMIN_TOKEN=... python scripts/smoke.py https://support-desk.onrender.com

Exit code 0 when every check passes, 1 otherwise. It writes demo data (one weekly
run, one ticket, one outbox reply), so point it at a demo instance only.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

TIMEOUT = 60


class SmokeError(RuntimeError):
    pass


def _request(base: str, method: str, path: str, body: Any = None) -> tuple[int, Any, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("ADMIN_TOKEN", "")
    if token and method != "GET":
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            raw = res.read().decode()
            ctype = res.headers.get("Content-Type", "")
            return res.status, (json.loads(raw) if "json" in ctype else None), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        return exc.code, None, raw
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # Not reachable yet (cold start, deploy in progress): status 0, callers retry.
        return 0, None, str(exc)


def _ok(base: str, method: str, path: str, body: Any = None, want: int = 200) -> Any:
    status, payload, raw = _request(base, method, path, body)
    if status != want:
        raise SmokeError(f"{method} {path} → {status} (want {want}): {raw[:200]}")
    return payload


def _poll(fn: Any, what: str, timeout: float = 180) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(2)
    raise SmokeError(f"timed out waiting for {what}")


def run(base: str) -> list[str]:
    base = base.rstrip("/")
    passed: list[str] = []

    health = _poll(
        lambda: (h := _request(base, "GET", "/api/health")[1] or {}).get("status") == "ok" and h,
        "healthy /api/health",
    )
    if not health.get("corpus_loaded"):
        raise SmokeError("corpus not loaded")
    passed.append(f"health ok (gate_mode={health.get('gate_mode')})")

    _poll(
        lambda: (_request(base, "GET", "/api/health")[1] or {}).get("seeded"),
        "first-boot seeding",
    )
    passed.append("first-boot seeding done")

    status, _, html = _request(base, "GET", "/")
    if status != 200 or '<div id="root">' not in html:
        raise SmokeError("UI shell not served at /")
    status, _, html = _request(base, "GET", "/approvals")
    if status != 200 or '<div id="root">' not in html:
        raise SmokeError("client route /approvals not served (SPA fallback)")
    passed.append("UI served at / and /approvals")

    run_resp = _ok(base, "POST", "/api/weekly/run", {})
    run_id = run_resp["run_id"]
    detail = _poll(
        lambda: (
            (d := _ok(base, "GET", f"/api/weekly/runs/{run_id}")).get("status")
            in {"awaiting_approval", "failed"}
            and d
        ),
        "weekly run to finish",
    )
    if detail["status"] != "awaiting_approval":
        raise SmokeError(f"weekly run {run_id} ended {detail['status']}: {detail.get('error')}")
    passed.append(f"weekly run {run_id} awaiting_approval")

    ticket = _ok(
        base, "POST", "/api/tickets", {"text": "What is the exit load on HDFC Large Cap?"}, want=201
    )
    tid = ticket["id"]
    tdetail = _ok(base, "GET", f"/api/tickets/{tid}")
    if not tdetail.get("current_draft"):
        raise SmokeError(f"ticket {tid} has no draft")
    action_id = tdetail.get("pending_reply_send_id")
    if not action_id:
        raise SmokeError(f"ticket {tid} has no pending reply (status {tdetail.get('status')})")
    passed.append(f"ticket {tid} drafted ({tdetail['status']})")

    approved = _ok(base, "POST", f"/api/actions/{action_id}/approve")
    if approved.get("status") != "executed":
        raise SmokeError(f"approval of {action_id} ended {approved.get('status')}")
    passed.append(f"reply {action_id} approved and written to the outbox")
    return passed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/smoke.py <base-url>", file=sys.stderr)
        return 2
    try:
        for line in run(argv[1]):
            print(f"PASS  {line}")
    except SmokeError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    print("smoke: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
