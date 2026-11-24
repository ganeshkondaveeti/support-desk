"""Startup checks (arch §16, plan P11.4).

- PII self-test: redact planted samples. Failure stops the app, because every
  other guarantee depends on redaction working.
- MCP handshake: spawn the server and list its tools. Failure does not stop the
  app; the gate switches to read-only so doc/email approvals are paused while
  reply approvals (outbox only) keep working.
"""

from __future__ import annotations

import logging

from app.gate.mode import set_gate_mode
from app.guardrails.pii import redact, scan

logger = logging.getLogger(__name__)

# Fake but well-formed values: (text, literal that must disappear, expected token).
_PLANTED: tuple[tuple[str, str, str], ...] = (
    ("My PAN is ABCDE1234F", "ABCDE1234F", "[PAN]"),
    ("call me on 9876543210", "9876543210", "[PHONE]"),
    # A checksum-valid (Verhoeff) number; the detector rejects invalid ones.
    ("aadhaar 2345 6789 0124", "2345 6789 0124", "[AADHAAR]"),
    ("account no 50100234567890", "50100234567890", "[ACCOUNT]"),
    ("mail me at someone@example.com", "someone@example.com", "[EMAIL]"),
    ("my pan abcde1234f lowercase", "abcde1234f", "[PAN]"),
)

EXPECTED_TOOLS = frozenset({"google_docs_append", "gmail_draft"})


class StartupCheckError(RuntimeError):
    """A check that must pass before the app serves traffic."""


def pii_self_test() -> None:
    """Raise StartupCheckError if redaction misses any planted sample."""
    for text, literal, token in _PLANTED:
        result = redact(text)
        if literal in result.text or token not in result.text or scan(result.text):
            # Never log the sample itself; the token name is enough.
            raise StartupCheckError(f"PII self-test failed for {token}")
    logger.info("startup pii self-test passed samples=%d", len(_PLANTED))


def mcp_handshake() -> bool:
    """
    Start the MCP stdio server and check it exposes exactly the two expected tools.

    Sets the gate mode and returns True when the gate is fully writable.
    """
    from app.gate.executor import list_mcp_tools

    try:
        tools = set(list_mcp_tools())
    except Exception as exc:  # noqa: BLE001 — any failure means read-only, never a crash
        reason = f"MCP server unavailable ({type(exc).__name__})"
        set_gate_mode("read_only", reason)
        logger.warning("startup mcp handshake failed: %s; gate is read-only", reason)
        return False
    if tools != EXPECTED_TOOLS:
        reason = f"MCP server exposes unexpected tools: {sorted(tools)}"
        set_gate_mode("read_only", reason)
        logger.warning("startup mcp handshake: %s; gate is read-only", reason)
        return False
    set_gate_mode("full")
    logger.info("startup mcp handshake ok tools=%s", sorted(tools))
    return True
