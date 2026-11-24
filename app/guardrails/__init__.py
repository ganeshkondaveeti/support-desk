"""Code-enforced scope rules (architecture §6)."""

from app.guardrails.pii import RedactionResult, redact, scan
from app.guardrails.scope import Violation, check_advice, check_buy_sell, check_performance

__all__ = [
    "RedactionResult",
    "Violation",
    "check_advice",
    "check_buy_sell",
    "check_performance",
    "redact",
    "scan",
]
