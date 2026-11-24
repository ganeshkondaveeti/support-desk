"""Environment settings (architecture §16)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Secrets stay in env / host secret store."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_env: Literal["development", "test", "production"] = "development"
    admin_token: str = ""
    log_text: bool = False
    eval_faults: str = ""

    # --- LLM (D2: Groq primary, Gemini fallback, CuesOnly offline) ---
    llm_provider: Literal["groq", "gemini", "cues_only"] = "cues_only"
    llm_model: str = "openai/gpt-oss-120b"
    llm_api_key: str = ""
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # --- Database ---
    database_url: str = "sqlite:///./data/state/support_desk.sqlite3"

    # --- Scheduler ---
    weekly_cron: str = "0 9 * * 1"
    tz: str = "Asia/Kolkata"

    # --- MCP (D1: TypeScript server over stdio) ---
    mcp_backend: Literal["local", "google"] = "local"
    mcp_server_cmd: str = "node mcp-server/dist/index.js"
    local_doc_path: str = "samples/appended_doc_entry.md"
    product_team_emails: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:3000/oauth2callback"
    google_token_path: str = "./mcp-server/data/google-token.json"
    google_token_json: str = ""
    google_docs_id: str = ""

    # --- Fee explainer (D5 / arch §7.7) ---
    explainer_schemes: list[str] = Field(default_factory=lambda: ["large_cap", "gold_fof"])

    # --- Trend thresholds (arch §7.4) ---
    trend_rising_ratio: float = 1.5
    trend_rising_min_sum: int = 5
    trend_recurring_week_ratio: float = 0.5
    trend_recurring_min_count: int = 20
    trend_min_week_days: int = 4

    # --- Repeat-question bridge (arch §9.1) ---
    repeat_min: int = 5
    repeat_window_days: int = 7

    @field_validator("explainer_schemes", mode="before")
    @classmethod
    def _split_schemes(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
