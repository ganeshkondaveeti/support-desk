"""The committed samples/ artifacts (plan P12.2–P12.4) stay valid and PII-free.

They're produced by `scripts/build_samples.py`; these checks catch a stale or
hand-edited sample.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest
from app.guardrails.pii import scan
from app.weekly.cluster import THEME_PRIORITY, cluster_reviews
from app.weekly.ingest import load_reviews

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
FILES = (
    "reviews_sample.csv",
    "five_questions_with_drafts.md",
    "appended_doc_entry.md",
    "email_draft.md",
)


def _planted_literals() -> list[str]:
    data = json.loads((ROOT / "evals" / "fixtures" / "planted_pii.json").read_text())
    out: list[str] = []
    for item in data["literals"]:
        out.append(item["value"])
        out.extend(item.get("variants") or [])
    return out


@pytest.mark.parametrize("name", FILES)
def test_sample_exists_and_has_no_planted_pii(name: str) -> None:
    text = (SAMPLES / name).read_text(encoding="utf-8")
    assert text.strip()
    for literal in _planted_literals():
        assert literal not in text, f"{name} contains a planted PII value"


def test_reviews_csv_is_50_redacted_rows_over_all_five_themes() -> None:
    path = SAMPLES / "reviews_sample.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 50
    assert Counter(r["theme_id"] for r in rows) == {t: 10 for t in THEME_PRIORITY}
    for row in rows:
        assert not scan(row["title"]) and not scan(row["text"]), row["id"]

    # The weekly loop reads the CSV like the JSON (arch §5.1) and assigns the same themes.
    ingest = load_reviews(path)
    assert len(ingest.reviews) == 50
    clustered = cluster_reviews(
        ingest.reviews, window_start=ingest.window_start, window_end=ingest.window_end
    )
    assert clustered.assignment == {r["id"]: r["theme_id"] for r in rows}


def test_five_questions_cover_the_required_cases() -> None:
    text = (SAMPLES / "five_questions_with_drafts.md").read_text(encoding="utf-8")
    assert text.count("\n## ") == 6  # five questions + the reuse log
    assert "[PAN]" in text and "[PHONE]" in text  # question 1 was redacted at ingress
    assert "Per the scheme page: Nil." in text  # ELSS exit load
    assert "We can't offer investment advice" in text  # advice refusal (row 1)
    assert "This isn't covered by the scheme information we hold." in text  # row 5
    assert "draft origin `canonical`" in text  # canonical reuse (row 3)
    assert "**Reviewer:** `pass`" in text


def test_doc_and_email_came_from_the_mcp_local_backend() -> None:
    doc = (SAMPLES / "appended_doc_entry.md").read_text(encoding="utf-8")
    # One idempotency marker per approved doc_append (arch §10.3).
    assert doc.count("<!-- action:") == 2
    assert "## Weekly pulse" in doc and "## Fee explainer" in doc
    email = (SAMPLES / "email_draft.md").read_text(encoding="utf-8")
    assert email.startswith("<!-- action:") and "# Email draft" in email
    eml = (SAMPLES / "email_draft.eml").read_text(encoding="utf-8")
    assert "X-Support-Desk-Action-Id:" in eml
