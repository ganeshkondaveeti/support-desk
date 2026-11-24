"""Post-redaction free-text checks (architecture §12 G6)."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.api.schemas import MAX_FREE_TEXT_CHARS
from app.guardrails.pii import RedactionResult, redact


def redact_and_check(raw: str, *, field: str = "text") -> RedactionResult:
    """
    Redact first, then enforce non-empty and ≤ 2,000 characters.

    Raises HTTP 400 with a generic message (never echoes the input).
    """
    if not isinstance(raw, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} must be a string",
        )
    result = redact(raw)
    cleaned = result.text.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} must not be empty",
        )
    if len(cleaned) > MAX_FREE_TEXT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} exceeds maximum length",
        )
    return RedactionResult(text=cleaned, types=result.types)
