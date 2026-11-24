"""Load committed eval fixtures (eval.md §3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / "fixtures"
QUESTIONS_DIR = ROOT / "questions"
EXPECTED_DIFFS_DIR = ROOT / "expected_diffs"


def load_jsonl(name: str) -> list[dict[str, Any]]:
    """Load ``questions/{name}.jsonl`` or ``{name}`` if it already includes a path."""
    path = Path(name)
    if path.suffix == ".jsonl" and "/" not in name:
        path = QUESTIONS_DIR / name
    elif not path.is_absolute() and not path.exists():
        candidate = QUESTIONS_DIR / f"{name}.jsonl"
        if candidate.is_file():
            path = candidate
        else:
            path = ROOT / name
    text = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_planted_pii() -> dict[str, Any]:
    path = ROOT / "planted_pii.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("planted_pii.json must be a JSON object")
    return payload


def load_expected_diff(case_id: str) -> dict[str, Any]:
    path = EXPECTED_DIFFS_DIR / f"{case_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"expected diff not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def load_synthetic_reviews_path() -> Path:
    path = ROOT / "reviews_synthetic.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing fixture: {path}")
    return path
