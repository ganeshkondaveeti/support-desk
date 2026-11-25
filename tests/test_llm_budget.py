"""LLM rate budget (Groq free tier) and rules-only seeding."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.config import clear_settings_cache
from app.corpus.loader import load_corpus
from app.db.session import init_db, reset_engine, session_scope
from app.llm import budget
from app.llm import client as llm_mod
from app.llm.budget import RateLimiter
from app.llm.client import GroqBackend, LLMBudgetExceeded, LLMClient, rules_only
from app.response.graph import compile_graph, reset_graph
from app.response.ingress import create_ticket
from app.response.seed import seed_tickets


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: FakeClock, **kw: Any) -> RateLimiter:
    args = {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 200_000, "headroom": 1.0}
    args.update(kw)
    return RateLimiter(**args, clock=clock, sleep=clock.sleep)


def test_minute_token_budget_paces_calls() -> None:
    clock = FakeClock()
    lim = _limiter(clock, tpm=1000)
    assert lim.acquire(600, max_wait=0) is not None
    # 600 + 600 > 1000: no capacity now, and no waiting allowed.
    assert lim.acquire(600, max_wait=0) is None
    # Allowed to wait: it sleeps until the first call leaves the minute window.
    start = clock.now
    assert lim.acquire(600, max_wait=90) is not None
    assert clock.now - start == pytest.approx(60.0, abs=1.0)


def test_requests_per_minute_cap() -> None:
    clock = FakeClock()
    lim = _limiter(clock, rpm=2)
    assert lim.acquire(10, max_wait=0) and lim.acquire(10, max_wait=0)
    assert lim.acquire(10, max_wait=0) is None


def test_day_budget_refuses_immediately() -> None:
    clock = FakeClock()
    lim = _limiter(clock, tpd=1000, tpm=10_000)
    assert lim.acquire(900, max_wait=0) is not None
    start = clock.now
    assert lim.acquire(200, max_wait=600) is None  # no waiting: the day is spent
    assert clock.now == start
    assert lim.day_exhausted(200) and not lim.day_exhausted(50)


def test_settle_uses_actual_usage_and_headroom() -> None:
    clock = FakeClock()
    lim = _limiter(clock, tpm=1000, headroom=0.8)  # effective 800
    res = lim.acquire(700, max_wait=0)
    assert res is not None
    lim.settle(res, 100)  # the real call was small
    assert lim.acquire(600, max_wait=0) is not None


class _RateLimitError(Exception):
    status_code = 429

    def __init__(self, retry_after: str) -> None:
        super().__init__("rate limited")
        self.response = SimpleNamespace(headers={"retry-after": retry_after})


class _FakeGroq:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kw: Any) -> Any:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
            usage=SimpleNamespace(total_tokens=450),
        )


@pytest.fixture()
def fresh_limiter(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    lim = _limiter(clock)
    monkeypatch.setattr(budget, "_LIMITER", lim)
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: lim)
    monkeypatch.setattr(llm_mod.time, "sleep", clock.sleep)
    monkeypatch.setattr(llm_mod.time, "monotonic", clock)
    return clock


def test_429_waits_once_for_retry_after_then_succeeds(fresh_limiter: FakeClock) -> None:
    fake = _FakeGroq([_RateLimitError("2"), '{"choice": "advice"}'])
    backend = GroqBackend(api_key="k", model="openai/gpt-oss-120b", client=fake, max_wait=20)
    assert backend.complete("prompt", timeout=8) == '{"choice": "advice"}'
    assert fake.calls == 2


def test_repeated_429_falls_back_to_rules(fresh_limiter: FakeClock) -> None:
    fake = _FakeGroq([_RateLimitError("2"), _RateLimitError("2")])
    backend = GroqBackend(api_key="k", model="openai/gpt-oss-120b", client=fake, max_wait=20)
    with pytest.raises(LLMBudgetExceeded):
        backend.complete("prompt", timeout=8)
    assert fake.calls == 2  # never more than one retry


def test_over_budget_choose_returns_error_without_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    lim = _limiter(clock, rpd=1)
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: lim)
    fake = _FakeGroq(['{"choice": "advice"}', '{"choice": "advice"}'])
    client = LLMClient(backend=GroqBackend(api_key="k", model="m", client=fake, max_wait=0))
    first = client.choose("p", {"type": "object"})
    second = client.choose("p", {"type": "object"})
    assert first.raw != "ERROR"  # the first call reached the provider
    assert second.raw == "ERROR" and second.error == "LLMBudgetExceeded"
    assert fake.calls == 1


class _RecordingBackend:
    """Stands in for Groq; records calls and always fails, so agents use the rules."""

    name = "groq"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str, *, timeout: float) -> str:
        self.calls += 1
        raise RuntimeError("recorded")


@pytest.fixture()
def graph_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_FOR_SEED", "false")
    clear_settings_cache()
    reset_engine()
    reset_graph()
    load_corpus()
    url = f"sqlite:///{tmp_path / 'seed.sqlite3'}"
    init_db(url)
    recorder = _RecordingBackend()
    monkeypatch.setattr(llm_mod, "_CLIENT", LLMClient(backend=recorder))
    yield url, recorder, compile_graph(path=tmp_path / "checkpoints.sqlite3")
    reset_engine()
    reset_graph()
    clear_settings_cache()


def test_seeding_makes_no_model_calls(graph_db) -> None:
    url, recorder, compiled = graph_db
    with session_scope(url) as session:
        ids = seed_tickets(session, limit=3, compiled=compiled)
    assert len(ids) == 3
    assert recorder.calls == 0  # the rules handled every seed ticket

    # Outside the seed scope a normal ticket still asks the model: 3 agents, 1 call each.
    with session_scope(url) as session:
        create_ticket(
            "What is the exit load on HDFC Large Cap?", session=session, compiled=compiled
        )
    assert recorder.calls == 3


def test_rules_only_scope_is_restored() -> None:
    marker = LLMClient(backend=_RecordingBackend())
    llm_mod._CLIENT = marker
    try:
        with rules_only():
            assert llm_mod.get_llm_client() is not marker
        assert llm_mod.get_llm_client() is marker
    finally:
        llm_mod.reset_llm_client()
