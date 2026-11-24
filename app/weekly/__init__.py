"""Weekly loop: reviews → themes → pulse + fee explainer."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.weekly.pipeline import WeeklyRunResult

__all__ = ["WeeklyRunResult", "run_weekly"]


def __getattr__(name: str) -> object:
    if name in {"WeeklyRunResult", "run_weekly"}:
        from app.weekly import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
