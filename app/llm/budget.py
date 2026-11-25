"""Client-side rate limiter for the LLM provider (Groq free tier).

Groq's free tier for openai/gpt-oss-120b allows 30 requests/min, 8K tokens/min,
1K requests/day and 200K tokens/day. Bursts past the per-minute limits get
throttled (HTTP 429); the daily limits, once spent, fail every call until the
window rolls over. This limiter keeps the app under both, with headroom:

- Before a call, ``acquire`` reserves an estimated token count. It waits (up to
  ``max_wait``) for per-minute capacity, and refuses at once when the day's
  budget is spent, so callers fall back to the rules immediately.
- After a call, ``settle`` replaces the estimate with the provider's usage.
- On a 429, ``cool_down`` pauses all calls until the provider's ``retry-after``.

Counters are in memory (one process, ``--workers 1``) and reset on restart.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

_MINUTE = 60.0
_DAY = 86_400.0


@dataclass(slots=True)
class _Entry:
    at: float
    tokens: int


class Reservation:
    """Handle for one reserved call; settle it with the real usage."""

    __slots__ = ("_entry",)

    def __init__(self, entry: _Entry) -> None:
        self._entry = entry


class RateLimiter:
    def __init__(
        self,
        *,
        rpm: int,
        tpm: int,
        rpd: int,
        tpd: int,
        headroom: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rpm = max(1, int(rpm * headroom))
        self.tpm = max(1, int(tpm * headroom))
        self.rpd = max(1, int(rpd * headroom))
        self.tpd = max(1, int(tpd * headroom))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._calls: deque[_Entry] = deque()
        self._blocked_until = 0.0

    # -- internals (lock held) --------------------------------------------

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0].at >= _DAY:
            self._calls.popleft()

    def _window(self, now: float, span: float) -> tuple[int, int]:
        requests = 0
        tokens = 0
        for entry in reversed(self._calls):
            if now - entry.at >= span:
                break
            requests += 1
            tokens += entry.tokens
        return requests, tokens

    def _minute_wait(self, now: float, tokens: int) -> float:
        """Seconds until a call of ``tokens`` fits the per-minute budget."""
        wait = max(0.0, self._blocked_until - now)
        requests, used = self._window(now, _MINUTE)
        if requests < self.rpm and used + tokens <= self.tpm:
            return wait
        # Walk the minute window oldest-first until enough has expired.
        in_minute = [e for e in self._calls if now - e.at < _MINUTE]
        freed_requests = 0
        freed_tokens = 0
        for entry in in_minute:
            freed_requests += 1
            freed_tokens += entry.tokens
            if requests - freed_requests < self.rpm and used - freed_tokens + tokens <= self.tpm:
                return max(wait, entry.at + _MINUTE - now)
        return max(wait, _MINUTE)

    # -- public API ---------------------------------------------------------

    def day_exhausted(self, tokens: int = 0) -> bool:
        with self._lock:
            now = self._clock()
            self._prune(now)
            requests, used = self._window(now, _DAY)
            return requests >= self.rpd or used + tokens > self.tpd

    def acquire(self, tokens: int, *, max_wait: float) -> Reservation | None:
        """Reserve one call of ~``tokens``; None if over the day budget or waiting too long."""
        deadline = self._clock() + max_wait
        while True:
            with self._lock:
                now = self._clock()
                self._prune(now)
                day_requests, day_tokens = self._window(now, _DAY)
                if day_requests >= self.rpd or day_tokens + tokens > self.tpd:
                    return None
                wait = self._minute_wait(now, tokens)
                if wait <= 0:
                    entry = _Entry(at=now, tokens=tokens)
                    self._calls.append(entry)
                    return Reservation(entry)
                if now + wait > deadline:
                    return None
            self._sleep(min(wait, 1.0))

    def settle(self, reservation: Reservation, actual_tokens: int | None) -> None:
        if actual_tokens is None or actual_tokens < 0:
            return
        with self._lock:
            reservation._entry.tokens = int(actual_tokens)

    def cool_down(self, seconds: float) -> None:
        """Pause every call for ``seconds`` (the provider's retry-after)."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + max(0.0, seconds))

    def usage(self) -> dict[str, int]:
        with self._lock:
            now = self._clock()
            self._prune(now)
            m_req, m_tok = self._window(now, _MINUTE)
            d_req, d_tok = self._window(now, _DAY)
        return {
            "minute_requests": m_req,
            "minute_tokens": m_tok,
            "day_requests": d_req,
            "day_tokens": d_tok,
        }


_LIMITER: RateLimiter | None = None
_LIMITER_LOCK = threading.Lock()


def get_limiter() -> RateLimiter:
    """Process-wide limiter built from settings (LLM_RPM / LLM_TPM / LLM_RPD / LLM_TPD)."""
    global _LIMITER
    with _LIMITER_LOCK:
        if _LIMITER is None:
            from app.config import get_settings

            s = get_settings()
            _LIMITER = RateLimiter(
                rpm=s.llm_rpm,
                tpm=s.llm_tpm,
                rpd=s.llm_rpd,
                tpd=s.llm_tpd,
                headroom=s.llm_headroom,
            )
        return _LIMITER


def reset_limiter() -> None:
    global _LIMITER
    with _LIMITER_LOCK:
        _LIMITER = None


def estimate_tokens(text: str, max_completion: int) -> int:
    """Conservative pre-call estimate: ~3 characters per token, plus the completion cap."""
    return len(text) // 3 + max_completion
