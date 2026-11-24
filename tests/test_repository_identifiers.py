"""Review ids and fingerprints are identifiers, not PII (storage guard, arch §5.3)."""

from __future__ import annotations

import pytest
from app.db import models
from app.db.repository import PIILeakError, scan_text_fields

# Real review ids from data/normalized_reviews.json that look like a PAN / a phone.
PAN_LIKE_ID = "6fbdbed5872ed7ab"
PHONE_LIKE_ID = "ce697b8881475718"


def test_pulse_citing_pan_like_review_ids_is_stored() -> None:
    pulse = models.Pulse(
        body_md=f'> "Charts freeze." — review {PAN_LIKE_ID}\n> "Slow." — review {PHONE_LIKE_ID}',
        quotes=[{"review_id": PAN_LIKE_ID, "text": "Charts freeze."}],
        repeat_questions=[{"representative_text": "why?", "fingerprint": "a" * 40}],
        action_ideas=[],
    )
    scan_text_fields(pulse)  # must not raise


def test_routing_note_review_sample_is_not_scanned_as_pii() -> None:
    draft = models.DraftVersion(
        reply_text="Exit load of 1% if redeemed within 1 year.",
        routing_note={"evidence": {"review_ids_sample": [PAN_LIKE_ID, PHONE_LIKE_ID]}},
        reviewer_result={},
    )
    scan_text_fields(draft)


@pytest.mark.parametrize(
    "body",
    [
        "PAN ABCDE1234F in the pulse",
        f"review {PAN_LIKE_ID} and call 9876543210",
        "quoted: my pan is abcde1234f",
    ],
)
def test_real_pii_is_still_blocked(body: str) -> None:
    with pytest.raises(PIILeakError):
        scan_text_fields(
            models.Pulse(body_md=body, quotes=[], repeat_questions=[], action_ideas=[])
        )


def test_pii_inside_quote_text_is_still_blocked() -> None:
    pulse = models.Pulse(
        body_md="ok",
        quotes=[{"review_id": PAN_LIKE_ID, "text": "call me on 9876543210"}],
        repeat_questions=[],
        action_ideas=[],
    )
    with pytest.raises(PIILeakError):
        scan_text_fields(pulse)
