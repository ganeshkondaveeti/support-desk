"""Thin HTTP client for the assembled app (eval.md §2)."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx


class EvalHttpError(Exception):
    """Unexpected HTTP status from the eval API client."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body!r}")


class EvalClient:
    def __init__(self, base_url: str, token: str, timeout: float = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EvalClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(
        self,
        *,
        eval_ticket: str | None = None,
        eval_now: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if eval_ticket:
            headers["X-Eval-Ticket"] = eval_ticket
        if eval_now:
            headers["X-Eval-Now"] = eval_now
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: int | tuple[int, ...] = 200,
        **kwargs: Any,
    ) -> Any:
        res = self._client.request(method, path, **kwargs)
        ok = (expected,) if isinstance(expected, int) else expected
        if res.status_code not in ok:
            try:
                body: Any = res.json()
            except Exception:
                body = res.text
            raise EvalHttpError(res.status_code, body)
        if res.status_code == 204 or not res.content:
            return None
        return res.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def wait_health(self, timeout: float = 60) -> dict[str, Any]:
        """Poll ``/api/health`` until status ok and corpus_loaded."""
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.health()
            if last.get("status") == "ok" and last.get("corpus_loaded") is True:
                return last
            time.sleep(0.5)
        return last

    def create_ticket(
        self,
        text: str,
        channel: str = "eval",
        *,
        eval_ticket: str | None = None,
        eval_now: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/tickets",
            expected=201,
            json={"text": text, "channel": channel},
            headers=self._headers(eval_ticket=eval_ticket, eval_now=eval_now),
        )

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tickets/{ticket_id}")

    def list_tickets(self, **params: Any) -> dict[str, Any]:
        """All tickets matching ``params`` (status, team, priority), across pages."""
        return self._paged("/api/tickets", dict(params))

    def _paged(self, path: str, query: dict[str, Any], page: int = 500) -> dict[str, Any]:
        query.pop("offset", None)
        query.pop("limit", None)
        items: list[Any] = []
        total = 0
        while True:
            body = self._request("GET", path, params={**query, "limit": page, "offset": len(items)})
            batch = list(body.get("items") or [])
            total = int(body.get("total") or 0)
            items.extend(batch)
            if not batch or len(items) >= total:
                break
        return {"items": items, "total": total}

    def edit_ticket(
        self,
        ticket_id: str,
        text: str,
        *,
        eval_ticket: str | None = None,
        eval_now: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/tickets/{ticket_id}/edit",
            json={"text": text},
            headers=self._headers(eval_ticket=eval_ticket, eval_now=eval_now),
        )

    def reject_ticket(self, ticket_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/tickets/{ticket_id}/reject", json={})

    def refine_ticket(
        self,
        ticket_id: str,
        kind: str,
        *,
        hint: str = "",
        eval_ticket: str | None = None,
        eval_now: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/tickets/{ticket_id}/refine",
            json={"kind": kind, "hint": hint},
            headers=self._headers(eval_ticket=eval_ticket, eval_now=eval_now),
        )

    def list_actions(self, *, status_filter: str | None = None, **params: Any) -> dict[str, Any]:
        """
        All actions matching ``status_filter`` (``""`` = every status), across pages.

        The API's query parameter is ``status`` and it defaults to ``pending`` when
        omitted, so the filter is always sent explicitly.
        """
        query = dict(params)
        query["status"] = "" if status_filter is None else status_filter
        return self._paged("/api/actions", query)

    def get_action(self, action_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/actions/{action_id}")

    def approve_action(self, action_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/actions/{action_id}/approve")

    def reject_action(self, action_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/actions/{action_id}/reject")

    def weekly_run(
        self,
        *,
        as_of: date | str | None = None,
        reviews_path: str | None = None,
        trigger: str | None = None,
        eval_ticket: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if as_of is not None:
            body["as_of"] = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
        if reviews_path is not None:
            body["reviews_path"] = reviews_path
        if trigger is not None:
            body["trigger"] = trigger
        return self._request(
            "POST",
            "/api/weekly/run",
            json=body,
            headers=self._headers(eval_ticket=eval_ticket),
        )

    def get_weekly_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/weekly/runs/{run_id}")

    def list_weekly_runs(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", "/api/weekly/runs", params=params)

    def get_explainers(self) -> dict[str, Any]:
        return self._request("GET", "/api/explainers/canonical")

    def get_sources(self) -> dict[str, Any]:
        return self._request("GET", "/api/sources")
