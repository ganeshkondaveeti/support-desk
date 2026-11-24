"""P0 exit criterion: GET /api/health → {"status": "ok"}."""

from app.config import Settings, get_settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_health_ok() -> None:
    client = TestClient(create_app(load_facts=True))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_defaults() -> None:
    get_settings.cache_clear()
    settings = Settings()
    assert settings.repeat_min == 5
    assert settings.repeat_window_days == 7
    assert settings.explainer_schemes == ["large_cap", "gold_fof"]
    assert settings.mcp_backend == "local"
    assert settings.trend_rising_ratio == 1.5
    assert settings.trend_rising_min_sum == 5
    assert settings.trend_recurring_week_ratio == 0.5
    assert settings.trend_recurring_min_count == 20
    assert settings.trend_min_week_days == 4
