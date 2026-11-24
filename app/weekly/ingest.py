"""Review ingest: JSON/CSV → Review, redact, dedupe, IST window (arch §7.2)."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.guardrails.pii import RedactionResult, redact

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_REVIEWS_PATH = Path(__file__).resolve().parents[2] / "data" / "normalized_reviews.json"


@dataclass(frozen=True, slots=True)
class Review:
    id: str
    source: str
    rating: int
    title: str
    text: str
    date: datetime
    version: str | None
    pii_types: tuple[str, ...] = ()
    title_had_pii: bool = False
    text_had_pii: bool = False


@dataclass(slots=True)
class IngestResult:
    reviews: list[Review]
    window_start: date
    window_end: date
    dropped_invalid: int = 0
    dropped_duplicate: int = 0
    dropped_after_as_of: int = 0
    source_path: str = ""
    notes: list[str] = field(default_factory=list)


def load_reviews(
    path: Path | str | None = None,
    *,
    as_of: date | None = None,
) -> IngestResult:
    """Parse JSON or CSV, redact, dedupe, optionally clip to ``date <= as_of``."""
    src = Path(path) if path is not None else DEFAULT_REVIEWS_PATH
    raw_rows = _read_rows(src)
    reviews: list[Review] = []
    dropped_invalid = 0
    dropped_after = 0
    seen: set[str] = set()
    dropped_dup = 0

    for row in raw_rows:
        parsed = _parse_row(row)
        if parsed is None:
            dropped_invalid += 1
            continue
        review_id, source, rating, title, text, dt, version = parsed
        if as_of is not None and dt.astimezone(IST).date() > as_of:
            dropped_after += 1
            continue
        if review_id in seen:
            dropped_dup += 1
            continue
        seen.add(review_id)

        title_res = redact(title)
        text_res = redact(text)
        reviews.append(
            Review(
                id=review_id,
                source=source,
                rating=rating,
                title=title_res.text,
                text=text_res.text,
                date=dt,
                version=version,
                pii_types=tuple(sorted(set(title_res.types) | set(text_res.types))),
                title_had_pii=bool(title_res.types),
                text_had_pii=bool(text_res.types),
            )
        )

    if not reviews:
        raise ValueError(f"no valid reviews loaded from {src}")

    dates = [r.date.astimezone(IST).date() for r in reviews]
    return IngestResult(
        reviews=reviews,
        window_start=min(dates),
        window_end=max(dates),
        dropped_invalid=dropped_invalid,
        dropped_duplicate=dropped_dup,
        dropped_after_as_of=dropped_after,
        source_path=str(src),
    )


def redact_fields(title: str, text: str) -> tuple[RedactionResult, RedactionResult]:
    return redact(title), redact(text)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("reviews"), list):
            return [row for row in payload["reviews"] if isinstance(row, dict)]
        raise ValueError(f"unsupported JSON shape in {path}")
    if suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    raise ValueError(f"unsupported review file type: {suffix}")


def _parse_row(
    row: dict[str, Any],
) -> tuple[str, str, int, str, str, datetime, str | None] | None:
    review_id = str(row.get("id") or row.get("reviewId") or "").strip()
    text = row.get("text") if "text" in row else row.get("content")
    if not review_id or text is None or str(text).strip() == "":
        return None
    raw_date = row.get("date") or row.get("at")
    dt = _parse_date(raw_date)
    if dt is None:
        return None
    try:
        rating = int(row.get("rating") if row.get("rating") is not None else row.get("score"))
    except (TypeError, ValueError):
        return None
    if rating < 1 or rating > 5:
        return None
    title = str(row.get("title") or "")
    source = str(row.get("source") or "play_store")
    # version is optional — 227 reviews omit the key entirely (F2 / P3.1).
    version_raw = row.get("version")
    version = None if version_raw is None or version_raw == "" else str(version_raw)
    return review_id, source, rating, title, str(text), dt, version


def _parse_date(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=IST)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt
