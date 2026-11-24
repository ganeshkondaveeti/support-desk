"""P4.7: only executor may import mcp_client or construct OutboxReply."""

from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

_MCP_IMPORT = re.compile(r"from\s+app\.gate\.mcp_client\s+import|import\s+app\.gate\.mcp_client")
_OUTBOX_CTOR = re.compile(r"OutboxReply\s*\(")


def test_mcp_client_import_only_in_executor() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if path.name in {"executor.py", "mcp_client.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if _MCP_IMPORT.search(text):
            offenders.append(str(path.relative_to(APP_ROOT.parent)))
    assert offenders == [], f"mcp_client imported outside executor: {offenders}"


def test_outbox_reply_constructed_only_in_executor() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if path.name in {"executor.py", "models.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if _OUTBOX_CTOR.search(text):
            offenders.append(str(path.relative_to(APP_ROOT.parent)))
    assert offenders == [], f"OutboxReply constructed outside executor: {offenders}"
