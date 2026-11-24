"""ASGI app factory for the eval harness (no scheduler / bootstrap / seed worker)."""

from __future__ import annotations

from app.main import create_app

app = create_app(
    load_facts=True,
    enable_scheduler=False,
    enable_bootstrap=False,
    enable_worker=False,
)
