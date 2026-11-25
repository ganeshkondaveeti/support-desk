"""Closed-list LLM client: models pick, code writes (architecture §8.4, D2).

`choose` makes exactly one provider call, validates against an Enum or object
schema, and never retries. Malformed or failed calls map to a null choice
(`raw="ERROR"`). CuesOnly is the offline/test backend.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol

from app.config import Settings, get_settings
from app.llm.budget import estimate_tokens, get_limiter

logger = logging.getLogger(__name__)

BackendName = Literal["groq", "gemini", "cues_only"]

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
GROQ_MAX_COMPLETION_TOKENS = 128
GROQ_REASONING_EFFORT = "low"
_LOW_REASONING_MODELS = frozenset(
    {
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
    }
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_NULL_WORDS = frozenset({"", "null", "none"})

# Process-wide call counter for P5 tests (exactly 1 call per agent node).
_CALL_COUNTS: dict[str, int] = {}
_CALL_HOOK: Callable[[str], None] | None = None


@dataclass(frozen=True, slots=True)
class ChooseResult:
    """One choose() outcome. `validated` is None on ERROR / invalid enum."""

    raw: str
    validated: Any
    latency_ms: int
    backend: BackendName
    error: str | None = None


class LLMBackend(Protocol):
    name: BackendName

    def complete(self, prompt: str, *, timeout: float) -> str:
        """Return raw model text. Raise on transport / empty failure."""


class CuesOnlyBackend:
    """Offline/test backend. Always yields ERROR so agents use cue tables."""

    name: BackendName = "cues_only"

    def __init__(self, *, fuzz: str | None = None) -> None:
        self._fuzz = fuzz

    def complete(self, prompt: str, *, timeout: float) -> str:
        _ = (prompt, timeout)
        if self._fuzz is not None:
            return self._fuzz
        raise RuntimeError("CuesOnly: no model call")


class GroqBackend:
    name: BackendName = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any = None,
        max_wait: float | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("GROQ_API_KEY is empty")
        self.model = model.strip() or DEFAULT_GROQ_MODEL
        if client is None:
            from groq import Groq

            # No hidden SDK retries: the limiter paces calls, and a 429 is handled
            # once below using the provider's retry-after.
            client = Groq(api_key=api_key.strip(), max_retries=0)
        self._client = client
        self._max_wait = get_settings().llm_max_wait_s if max_wait is None else max_wait

    def complete(self, prompt: str, *, timeout: float) -> str:
        """One model call, paced by the shared rate limiter (app/llm/budget.py)."""
        limiter = get_limiter()
        estimate = estimate_tokens(prompt, GROQ_MAX_COMPLETION_TOKENS)
        deadline = time.monotonic() + self._max_wait
        for attempt in (1, 2):
            reservation = limiter.acquire(estimate, max_wait=max(0.0, deadline - time.monotonic()))
            if reservation is None:
                raise LLMBudgetExceeded("LLM rate budget spent; using the rules")
            try:
                completion = self._create(prompt, timeout=timeout)
            except Exception as exc:
                limiter.settle(reservation, 0)  # a rejected call used no tokens
                retry_after = _rate_limit_retry_after(exc)
                if retry_after is None:
                    raise
                limiter.cool_down(retry_after)
                if attempt == 2 or time.monotonic() + retry_after > deadline:
                    raise LLMBudgetExceeded("provider rate limit (429)") from exc
                logger.info("llm rate limited; waiting %.1fs once", retry_after)
                continue
            usage = getattr(completion, "usage", None)
            limiter.settle(reservation, getattr(usage, "total_tokens", None))
            content = completion.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError("groq response is empty")
            return content
        raise LLMBudgetExceeded("provider rate limit (429)")  # pragma: no cover

    def _create(self, prompt: str, *, timeout: float) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Reply with one JSON object only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "seed": 0,
            "max_completion_tokens": GROQ_MAX_COMPLETION_TOKENS,
            "response_format": {"type": "json_object"},
            "timeout": timeout,
        }
        if self.model in _LOW_REASONING_MODELS:
            kwargs["reasoning_effort"] = GROQ_REASONING_EFFORT
        return self._client.chat.completions.create(**kwargs)


class LLMBudgetExceeded(RuntimeError):
    """The call was not made: over the rate budget. Callers fall back to the rules."""


def _rate_limit_retry_after(exc: Exception) -> float | None:
    """Seconds to wait if ``exc`` is a provider 429, else None."""
    status = getattr(exc, "status_code", None)
    if status != 429:
        return None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return max(0.5, float(raw)) if raw is not None else 5.0
    except (TypeError, ValueError):
        return 5.0


class GeminiBackend:
    name: BackendName = "gemini"

    def __init__(self, *, api_key: str, model: str, client: Any = None) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is empty")
        self.model = model.strip() or DEFAULT_GEMINI_MODEL
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key.strip())
        self._client = client

    def complete(self, prompt: str, *, timeout: float) -> str:
        _ = timeout
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "temperature": 0,
                "seed": 0,
                "response_mime_type": "application/json",
            },
        )
        content = getattr(response, "text", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("gemini response is empty")
        return content


@dataclass
class LLMClient:
    """Provider-backed choose() with enum validation and no retry."""

    backend: LLMBackend
    call_label: str = "llm"

    def choose(
        self,
        prompt: str,
        schema: type[Enum] | dict[str, Any],
        *,
        timeout: float = 8.0,
    ) -> ChooseResult:
        """One call. Invalid / failed → validated=None, raw='ERROR' or raw text."""
        _record_call(self.call_label)
        started = time.perf_counter()
        # Eval fault: llm_down forces ERROR without a provider call (APP_ENV=test).
        try:
            from app.response.faults import fault_applies

            if fault_applies("llm_down"):
                latency = int((time.perf_counter() - started) * 1000)
                return ChooseResult(
                    raw="ERROR",
                    validated=None,
                    latency_ms=latency,
                    backend=self.backend.name,
                    error="llm_down",
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            raw_text = self.backend.complete(prompt, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — one-call contract: never retry
            latency = int((time.perf_counter() - started) * 1000)
            logger.warning(
                "llm.choose failed backend=%s err=%s",
                self.backend.name,
                type(exc).__name__,
            )
            return ChooseResult(
                raw="ERROR",
                validated=None,
                latency_ms=latency,
                backend=self.backend.name,
                error=type(exc).__name__,
            )

        latency = int((time.perf_counter() - started) * 1000)
        try:
            payload = parse_json_object(raw_text)
            validated = validate_against_schema(payload, schema)
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("llm.choose invalid output: %s", type(exc).__name__)
            return ChooseResult(
                raw=raw_text.strip()[:500] or "ERROR",
                validated=None,
                latency_ms=latency,
                backend=self.backend.name,
                error=type(exc).__name__,
            )
        return ChooseResult(
            raw=raw_text.strip()[:500],
            validated=validated,
            latency_ms=latency,
            backend=self.backend.name,
        )


_CLIENT: LLMClient | None = None
_RULES_ONLY: ContextVar[bool] = ContextVar("llm_rules_only", default=False)
_RULES_CLIENT: LLMClient | None = None  # built on first use


@contextmanager
def rules_only() -> Iterator[None]:
    """
    Within this scope agents use the rules (CuesOnly), not the model.

    Used for seed/demo tickets: on the free host they are re-created on every
    wake-up, and spending ~120 model calls each time would use up the daily quota.
    """
    token = _RULES_ONLY.set(True)
    try:
        yield
    finally:
        _RULES_ONLY.reset(token)


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """Process-wide client from settings (D2); the rules backend inside ``rules_only()``."""
    global _CLIENT, _RULES_CLIENT
    if _RULES_ONLY.get():
        if _RULES_CLIENT is None:
            _RULES_CLIENT = LLMClient(backend=CuesOnlyBackend())
        return _RULES_CLIENT
    if _CLIENT is not None and settings is None:
        return _CLIENT
    cfg = settings or get_settings()
    backend = build_backend(cfg)
    client = LLMClient(backend=backend)
    if settings is None:
        _CLIENT = client
    return client


def reset_llm_client() -> None:
    global _CLIENT
    _CLIENT = None


def build_backend(settings: Settings) -> LLMBackend:
    provider = settings.llm_provider
    if provider == "cues_only":
        return CuesOnlyBackend()
    if provider == "groq":
        key = settings.groq_api_key or settings.llm_api_key
        model = settings.llm_model or DEFAULT_GROQ_MODEL
        return GroqBackend(api_key=key, model=model)
    if provider == "gemini":
        key = settings.gemini_api_key or settings.llm_api_key
        model = settings.llm_model or DEFAULT_GEMINI_MODEL
        return GeminiBackend(api_key=key, model=model)
    raise ValueError(f"unknown llm_provider {provider!r}")


def reset_call_counts() -> None:
    _CALL_COUNTS.clear()


def call_counts() -> dict[str, int]:
    return dict(_CALL_COUNTS)


def set_call_hook(hook: Callable[[str], None] | None) -> None:
    global _CALL_HOOK
    _CALL_HOOK = hook


def _record_call(label: str) -> None:
    _CALL_COUNTS[label] = _CALL_COUNTS.get(label, 0) + 1
    if _CALL_HOOK is not None:
        _CALL_HOOK(label)


def parse_json_object(content: str) -> dict[str, Any]:
    text = _FENCE.sub("", content.strip()).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response is not a JSON object") from None
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model response must be a JSON object")
    return payload


def validate_against_schema(payload: Mapping[str, Any], schema: type[Enum] | dict[str, Any]) -> Any:
    """Validate payload against an Enum (single `value`/`choice` key) or field dict."""
    if isinstance(schema, type) and issubclass(schema, Enum):
        return _match_enum_payload(payload, schema)

    if not isinstance(schema, dict):
        raise TypeError("schema must be an Enum type or a dict of field specs")

    out: dict[str, Any] = {}
    for key, spec in schema.items():
        raw = payload.get(key)
        if isinstance(spec, type) and issubclass(spec, Enum):
            out[key] = match_enum(raw, spec)
        elif isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
            out[key] = match_allowed(raw, list(spec))
        else:
            raise TypeError(f"unsupported schema field {key!r}: {spec!r}")
    return out


def _match_enum_payload(payload: Mapping[str, Any], enum_cls: type[Enum]) -> Enum | None:
    for key in ("value", "choice", "flag", "theme_id"):
        if key in payload:
            return match_enum(payload[key], enum_cls)
    if len(payload) == 1:
        return match_enum(next(iter(payload.values())), enum_cls)
    raise ValueError("enum payload missing value")


def match_enum(value: object, enum_cls: type[Enum]) -> Enum | None:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.casefold() in _NULL_WORDS:
        return None
    for member in enum_cls:
        member_val = str(member.value)
        if text == member.value or text == member.name or text.casefold() == member_val.casefold():
            return member
    return None


def match_allowed(value: object, allowed: Sequence[str]) -> str | None:
    if value is None or not isinstance(value, str):
        return None
    text = value.strip()
    if text.casefold() in _NULL_WORDS:
        return None
    for item in allowed:
        if text == item or text.casefold() == item.casefold():
            return item
    return None


@dataclass
class CallMetrics:
    """Per-call metrics stored alongside agent outputs."""

    latency_ms: int = 0
    raw_choice: str = "ERROR"
    validated_choice: str | None = None
    backend: str = "cues_only"
    extras: dict[str, Any] = field(default_factory=dict)
