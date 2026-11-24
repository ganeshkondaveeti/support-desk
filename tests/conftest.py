"""Shared pytest fixtures for API / DB tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Ensure test env before Settings is first constructed in this process when possible.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LLM_PROVIDER", "cues_only")
os.environ.setdefault("MCP_BACKEND", "local")


@pytest.fixture()
def admin_token() -> str:
    return "test-admin-token"


@pytest.fixture()
def api_client(tmp_path: Path, admin_token: str, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """TestClient bound to a fresh temp SQLite DB with ADMIN_TOKEN set."""
    from app.config import clear_settings_cache
    from app.db.session import init_db, reset_engine
    from app.main import create_app
    from fastapi.testclient import TestClient

    db_path = tmp_path / "api.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_PROVIDER", "cues_only")
    clear_settings_cache()
    reset_engine()
    init_db()

    application = create_app(
        load_facts=True,
        enable_scheduler=False,
        enable_bootstrap=False,
        enable_worker=True,
    )
    with TestClient(application) as client:
        client.headers["Authorization"] = f"Bearer {admin_token}"
        yield client

    reset_engine()
    clear_settings_cache()
