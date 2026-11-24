"""P8 API contract, auth, weekly lock, scheduler, validation (G6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from app.config import clear_settings_cache, get_settings
from app.db import models
from app.db.session import init_db, reset_engine, session_scope
from app.main import create_app
from app.scheduler import build_scheduler, parse_weekly_cron, scheduled_weekly_run
from app.weekly.pipeline import WeeklyRunLockError
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "docs" / "openapi.json"

EXPECTED_PATHS = {
    "/api/health",
    "/api/tickets",
    "/api/tickets/seed",
    "/api/tickets/{ticket_id}",
    "/api/tickets/{ticket_id}/edit",
    "/api/tickets/{ticket_id}/reject",
    "/api/tickets/{ticket_id}/refine",
    "/api/weekly/run",
    "/api/weekly/runs",
    "/api/weekly/runs/{run_id}",
    "/api/sources",
    "/api/actions",
    "/api/actions/{action_id}",
    "/api/actions/{action_id}/approve",
    "/api/actions/{action_id}/reject",
    "/api/explainers/canonical",
    "/api/audit",
}

MUTATING = [
    ("POST", "/api/tickets", {"text": "What is the exit load?"}),
    ("POST", "/api/tickets/seed", None),
    ("POST", "/api/weekly/run", {}),
]


@pytest.fixture()
def bare_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client without Authorization header (ADMIN_TOKEN set)."""
    db_path = tmp_path / "bare.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("APP_ENV", "test")
    clear_settings_cache()
    reset_engine()
    init_db()
    application = create_app(
        enable_scheduler=False,
        enable_bootstrap=False,
        enable_worker=False,
    )
    return TestClient(application)


def test_openapi_frozen_paths() -> None:
    assert OPENAPI_PATH.is_file(), "run: python scripts/export_openapi.py"
    schema = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    paths = set(schema.get("paths", {}))
    missing = EXPECTED_PATHS - paths
    assert not missing, f"openapi missing paths: {sorted(missing)}"


def test_health_contract(api_client: TestClient) -> None:
    res = api_client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["corpus_loaded"] is True
    assert body["db_reachable"] is True
    assert "pending_approvals" in body


def test_mutating_routes_require_token(bare_client: TestClient) -> None:
    for method, path, payload in MUTATING:
        kwargs = {}
        if payload is not None:
            kwargs["json"] = payload
        res = bare_client.request(method, path, **kwargs)
        assert res.status_code == 401, f"{method} {path} → {res.status_code}"
        assert "input" not in res.text.lower() or "unauthorized" in res.text.lower()


def test_read_routes_public(bare_client: TestClient) -> None:
    for path in ("/api/health", "/api/tickets", "/api/sources", "/api/actions", "/api/audit"):
        res = bare_client.get(path)
        assert res.status_code == 200, path


def test_ticket_create_and_get(api_client: TestClient) -> None:
    res = api_client.post("/api/tickets", json={"text": "What is the expense ratio of large cap?"})
    assert res.status_code == 201
    tid = res.json()["id"]
    detail = api_client.get(f"/api/tickets/{tid}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == tid
    assert body["question_redacted"]
    assert len(body["agent_outputs"]) >= 1
    listed = api_client.get("/api/tickets")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1


def test_validation_error_strips_input(api_client: TestClient) -> None:
    # Wrong type for text → 422 without echoing planted PII.
    planted = "PAN ABCDE1234F and phone 9876543210"
    res = api_client.post("/api/tickets", json={"text": 12345, "planted": planted})
    assert res.status_code == 422
    raw = res.text
    assert "ABCDE1234F" not in raw
    assert "9876543210" not in raw
    assert "input" not in raw
    for err in res.json()["detail"]:
        assert set(err.keys()) <= {"loc", "type", "msg"}
        assert "input" not in err
        assert "ctx" not in err


def test_empty_text_after_redact(api_client: TestClient) -> None:
    res = api_client.post("/api/tickets", json={"text": "   "})
    assert res.status_code == 400


def test_weekly_run_and_list(api_client: TestClient) -> None:
    res = api_client.post("/api/weekly/run", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["run_id"]
    assert body["status"] in {"awaiting_approval", "failed"}
    runs = api_client.get("/api/weekly/runs")
    assert runs.status_code == 200
    assert runs.json()["total"] >= 1
    detail = api_client.get(f"/api/weekly/runs/{body['run_id']}")
    assert detail.status_code == 200
    assert "themes" in detail.json()


def test_weekly_run_overlap_409(api_client: TestClient, tmp_path: Path) -> None:
    # Plant a fresh running lock row, then trigger.
    with session_scope() as session:
        session.add(
            models.WeeklyRun(
                id="wr_lock_test_001",
                as_of=datetime.now(UTC).date(),
                trigger="manual",
                status="running",
                started_at=datetime.now(UTC),
                finished_at=None,
                input_hash="abc",
                error=None,
            )
        )

    res = api_client.post("/api/weekly/run", json={})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["run_id"] == "wr_lock_test_001"


def test_stale_lock_cleared(api_client: TestClient) -> None:
    stale_start = datetime.now(UTC) - timedelta(minutes=20)
    with session_scope() as session:
        session.add(
            models.WeeklyRun(
                id="wr_stale_lock_001",
                as_of=datetime.now(UTC).date(),
                trigger="manual",
                status="running",
                started_at=stale_start,
                finished_at=None,
                input_hash="stalehash",
                error=None,
            )
        )
    # Stale lock should be cleared; run proceeds (may noop if hash matches a prior run).
    res = api_client.post("/api/weekly/run", json={})
    assert res.status_code == 200
    with session_scope() as session:
        stale = session.get(models.WeeklyRun, "wr_stale_lock_001")
        assert stale is not None
        assert stale.status == "failed"
        assert stale.error and "TTL" in stale.error


def test_actions_and_audit(api_client: TestClient) -> None:
    run = api_client.post("/api/weekly/run", json={})
    assert run.status_code == 200
    actions = api_client.get("/api/actions?status=pending")
    assert actions.status_code == 200
    items = actions.json()["items"]
    assert len(items) >= 1
    action_id = items[0]["id"]
    one = api_client.get(f"/api/actions/{action_id}")
    assert one.status_code == 200
    assert "what_else_happens" in one.json()
    assert "destination" in one.json()
    # Reject leaves no side effect.
    rejected = api_client.post(f"/api/actions/{action_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    audit = api_client.get("/api/audit")
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1


def test_sources_and_explainers(api_client: TestClient) -> None:
    sources = api_client.get("/api/sources")
    assert sources.status_code == 200
    body = sources.json()
    assert len(body["blocks"]) >= 1
    assert "drift" in body
    explainers = api_client.get("/api/explainers/canonical")
    assert explainers.status_code == 200
    assert "active" in explainers.json()
    assert "history" in explainers.json()


def test_ticket_reject_edit_refine(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/tickets",
        json={"text": "What is the exit load for HDFC Large Cap?"},
    )
    assert created.status_code == 201
    tid = created.json()["id"]

    refined = api_client.post(
        f"/api/tickets/{tid}/refine",
        json={"kind": "shorter", "hint": ""},
    )
    assert refined.status_code == 200

    edited = api_client.post(
        f"/api/tickets/{tid}/edit",
        json={"text": "Expense ratio is listed in the scheme facts. See source."},
    )
    # Edit may pass or fail reviewer; either way HTTP 200 with detail.
    assert edited.status_code == 200

    rejected = api_client.post(f"/api/tickets/{tid}/reject", json={})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_seed_enqueue(api_client: TestClient) -> None:
    res = api_client.post("/api/tickets/seed")
    assert res.status_code == 202
    body = res.json()
    assert body["enqueued"] >= 1
    assert body["job_id"]
    assert body["status"] == "queued"


def test_scheduler_cron_parses_and_job_wired() -> None:
    settings = get_settings()
    trigger = parse_weekly_cron(settings.weekly_cron, timezone=settings.tz)
    assert trigger is not None
    scheduler = build_scheduler(settings)
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "weekly_loop"


def test_scheduler_job_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "sched.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("APP_ENV", "test")
    clear_settings_cache()
    reset_engine()
    init_db()

    with patch("app.scheduler.run_weekly") as mock_run:
        from datetime import date

        from app.weekly.pipeline import WeeklyRunResult

        mock_run.return_value = WeeklyRunResult(
            run_id="wr_sched",
            as_of=date(2026, 7, 4),
            status="awaiting_approval",
        )
        # Advance "now" into a Monday 09:00 IST window by invoking the job directly
        # (APScheduler clock freeze via DateTrigger is overkill; call the callable).
        out = scheduled_weekly_run()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["trigger"] == "cron"
        assert out["status"] == "awaiting_approval"

    # Lock conflict path
    with patch("app.scheduler.run_weekly", side_effect=WeeklyRunLockError("busy", run_id="wr_x")):
        out = scheduled_weekly_run()
        assert out["status"] == "conflict"
        assert out["run_id"] == "wr_x"

    reset_engine()
    clear_settings_cache()


def test_live_openapi_matches_routes() -> None:
    application = create_app(enable_scheduler=False, enable_bootstrap=False, enable_worker=False)
    paths = set(application.openapi().get("paths", {}))
    missing = EXPECTED_PATHS - paths
    assert not missing, sorted(missing)
