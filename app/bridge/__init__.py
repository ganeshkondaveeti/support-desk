"""Cross-loop bridge: repeat questions ↔ pulse; explainer ↔ canonical reply."""

from app.bridge.canonical import (
    PromoteResult,
    lookup,
    promote,
    reconcile_pending_promotions,
    record_reuse,
    reuse_log,
)
from app.bridge.repeats import (
    compliance_class,
    fingerprint,
    in_week_repeats,
    normalize,
    record_ticket_fingerprint,
    repeat_count,
    repeat_questions,
    simhash_bucket,
)

__all__ = [
    "PromoteResult",
    "compliance_class",
    "fingerprint",
    "in_week_repeats",
    "lookup",
    "normalize",
    "promote",
    "reconcile_pending_promotions",
    "record_reuse",
    "record_ticket_fingerprint",
    "repeat_count",
    "repeat_questions",
    "reuse_log",
    "simhash_bucket",
]
