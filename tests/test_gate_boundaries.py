"""P4.7: nothing outside gate/executor may import mcp_client or write outbox_replies."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

_MCP_IMPORT = re.compile(
    r"^\s*(from\s+app\.gate\.mcp_client\s+import|import\s+app\.gate\.mcp_client)\b",
    re.MULTILINE,
)
_OUTBOX_WRITE = re.compile(
    r"models\.OutboxReply\s*\(|OutboxReply\s*\(",
)


def _py_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if p.is_file())


def test_mcp_client_imported_only_from_executor() -> None:
    offenders: list[str] = []
    allowed = {
        (APP / "gate" / "executor.py").resolve(),
        (APP / "gate" / "mcp_client.py").resolve(),
    }
    for path in _py_files():
        if path.resolve() in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if _MCP_IMPORT.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"mcp_client imported outside executor: {offenders}"


def test_outbox_replies_written_only_in_executor() -> None:
    offenders: list[str] = []
    allowed = {(APP / "gate" / "executor.py").resolve()}
    for path in _py_files():
        if path.resolve() in allowed:
            continue
        # models.py defines the class; alembic / snapshots only reference the table.
        if path.name in {"models.py", "snapshots.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if _OUTBOX_WRITE.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"OutboxReply constructed outside executor: {offenders}"
