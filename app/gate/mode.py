"""Gate mode (arch §16): "full", or "read_only" when the MCP writer is unavailable."""

from __future__ import annotations

import threading
from typing import Literal

GateMode = Literal["full", "read_only"]

_lock = threading.Lock()
_mode: GateMode = "full"
_reason = ""

# Kinds that need the MCP server. reply_send writes to the outbox and never does.
MCP_KINDS = frozenset({"doc_append", "email_draft"})


def set_gate_mode(mode: GateMode, reason: str = "") -> None:
    global _mode, _reason
    with _lock:
        _mode = mode
        _reason = reason


def gate_mode() -> GateMode:
    with _lock:
        return _mode


def gate_reason() -> str:
    with _lock:
        return _reason
