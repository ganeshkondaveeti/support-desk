"""P9 backend support: health fields for the UI header and SPA fallback."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.main import SpaStaticFiles, create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    return TestClient(
        create_app(
            load_facts=True,
            enable_scheduler=False,
            enable_bootstrap=False,
            enable_worker=False,
        )
    )


def test_health_exposes_ui_header_fields() -> None:
    body = _client().get("/api/health").json()
    assert body["corpus_last_checked"] == {"A": "28-Jun-2026", "B": "01-Sep-2026"}
    assert "groww.in" in body["public_domains"]
    assert body["public_domains"] == sorted(body["public_domains"])
    assert "https://www.amfiindia.com/investor-corner" in body["edu_urls"]


@pytest.fixture()
def spa_client(tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()
    app.mount("/", SpaStaticFiles(directory=tmp_path, html=True), name="ui")
    return TestClient(app)


def test_spa_fallback_serves_index_for_client_routes(spa_client: TestClient) -> None:
    for path in ("/", "/queue", "/queue/tkt_abc", "/sources/drift"):
        res = spa_client.get(path)
        assert res.status_code == 200, path
        assert "<title>ui</title>" in res.text


def test_spa_fallback_keeps_404_for_assets_and_api(spa_client: TestClient) -> None:
    assert spa_client.get("/assets/app.js").status_code == 200
    assert spa_client.get("/assets/missing.js").status_code == 404
    assert spa_client.get("/api/nope").status_code == 404


def test_api_ticket_keeps_pii_types_found_at_the_edge(api_client) -> None:
    """The API redacts first; ingress must still record what was removed (P9 bug fix)."""
    res = api_client.post(
        "/api/tickets",
        json={
            "text": "Why was I charged an exit load? My PAN is ABCDE1234F, call me on 9876543210."
        },
    )
    assert res.status_code == 201
    created = res.json()
    assert created["pii_types_found"] == ["PAN", "PHONE"]
    detail = api_client.get(f"/api/tickets/{created['id']}").json()
    assert detail["pii_types_found"] == ["PAN", "PHONE"]
    assert "[PAN]" in detail["question_redacted"] and "[PHONE]" in detail["question_redacted"]
    assert "ABCDE1234F" not in str(detail) and "9876543210" not in str(detail)
    compliance = [a for a in detail["agent_outputs"] if a["agent"] == "compliance"][-1]
    assert compliance["written_output"]["pii_detected"] is True
    # A PII flag routes to Compliance (arch §8.5).
    assert "Compliance" in (detail["route_team"] or "")
