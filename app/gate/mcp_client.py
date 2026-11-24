"""Stdio MCP client session to the TypeScript server (architecture §10.4)."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

EXPECTED_TOOLS = frozenset({"google_docs_append", "gmail_draft"})


class McpClientError(RuntimeError):
    """MCP spawn, handshake, or tool-call failure."""


class McpClient:
    """
    One long-lived stdio session per process; restart on failure.

    Sync callers use ``call_tool``; the session runs on a background loop.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._cm: Any | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._start_error: BaseException | None = None

    @property
    def connected(self) -> bool:
        return self._session is not None and self._ready.is_set() and self._start_error is None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run_loop,
                name="mcp-stdio",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=30):
            raise McpClientError("MCP client failed to start within 30s")
        if self._start_error is not None:
            raise McpClientError(
                f"MCP client start failed: {self._start_error}"
            ) from self._start_error

    def stop(self) -> None:
        with self._lock:
            loop = self._loop
            if loop is not None and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                try:
                    fut.result(timeout=10)
                except Exception:  # noqa: BLE001
                    logger.exception("MCP shutdown error")
                loop.call_soon_threadsafe(loop.stop)
            thread = self._thread
        if thread is not None:
            thread.join(timeout=10)
        self._thread = None
        self._loop = None
        self._session = None
        self._cm = None
        self._ready.clear()

    def list_tools(self) -> list[str]:
        self._ensure()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(self._list_tools(), self._loop)
        return fut.result(timeout=30)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(self._call_tool(name, arguments), self._loop)
        try:
            return fut.result(timeout=60)
        except Exception as exc:
            logger.exception("MCP tool %s failed; restarting session", name)
            self._restart()
            raise McpClientError(f"MCP tool {name} failed: {exc}") from exc

    def google_docs_append(
        self,
        *,
        document_id: str,
        content: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.call_tool(
            "google_docs_append",
            {
                "document_id": document_id,
                "content": content,
                "idempotency_key": idempotency_key,
            },
        )

    def gmail_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        idempotency_key: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.call_tool(
            "gmail_draft",
            {
                "to": to,
                "cc": cc or [],
                "bcc": bcc or [],
                "subject": subject,
                "body": body,
                "idempotency_key": idempotency_key,
            },
        )

    def _ensure(self) -> None:
        if not self.connected:
            self.start()

    def _restart(self) -> None:
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            logger.exception("MCP stop during restart")
        self.start()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._connect())
            self._ready.set()
            loop.run_forever()
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(self._shutdown())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    async def _connect(self) -> None:
        params = self._server_params()
        self._cm = stdio_client(params)
        read, write = await self._cm.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        if names != EXPECTED_TOOLS:
            raise McpClientError(
                f"unexpected MCP tools: {sorted(names)}; expected {sorted(EXPECTED_TOOLS)}"
            )
        self._session = session
        logger.info(
            "MCP session ready; tools=%s backend=%s",
            sorted(names),
            self._settings.mcp_backend,
        )

    async def _shutdown(self) -> None:
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.exception("MCP session close")
            self._session = None
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.exception("MCP transport close")
            self._cm = None

    async def _list_tools(self) -> list[str]:
        assert self._session is not None
        result = await self._session.list_tools()
        return sorted(t.name for t in result.tools)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self._session is not None
        result = await self._session.call_tool(name, arguments)
        if result.is_error:
            text = _content_text(result.content)
            raise McpClientError(text or f"tool {name} returned is_error")
        text = _content_text(result.content)
        if not text:
            return {"raw": None}
        try:
            parsed: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        if parsed.get("success") is False:
            err = parsed.get("error") or {}
            raise McpClientError(str(err.get("message") or parsed))
        data = parsed.get("data")
        if isinstance(data, dict):
            return data
        return parsed

    def _server_params(self) -> StdioServerParameters:
        settings = self._settings
        parts = shlex.split(settings.mcp_server_cmd)
        if not parts:
            raise McpClientError("MCP_SERVER_CMD is empty")
        command, *args = parts
        root = Path.cwd()
        env = {
            "MCP_BACKEND": settings.mcp_backend,
            "LOCAL_DOC_PATH": str(
                Path(settings.local_doc_path)
                if Path(settings.local_doc_path).is_absolute()
                else root / settings.local_doc_path
            ),
            "LOCAL_EMAIL_PATH": str(
                Path(settings.local_email_path)
                if Path(settings.local_email_path).is_absolute()
                else root / settings.local_email_path
            ),
            "LOG_LEVEL": "error",
            "NODE_ENV": "production" if settings.app_env == "production" else "development",
        }
        if settings.mcp_backend == "google":
            env.update(
                {
                    "GOOGLE_CLIENT_ID": settings.google_client_id,
                    "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
                    "GOOGLE_REDIRECT_URI": settings.google_redirect_uri,
                    "GOOGLE_TOKEN_PATH": settings.google_token_path,
                    "GOOGLE_TOKEN_JSON": settings.google_token_json,
                }
            )
        else:
            # Placeholders so the TS config schema still parses if a module imports it.
            env.setdefault("GOOGLE_CLIENT_ID", "local-unused")
            env.setdefault("GOOGLE_CLIENT_SECRET", "local-unused")
            env.setdefault("GOOGLE_REDIRECT_URI", "http://localhost/unused")
        return StdioServerParameters(command=command, args=args, env=env, cwd=str(root))


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
        elif isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts)


_client: McpClient | None = None
_client_lock = threading.Lock()


def get_mcp_client(settings: Settings | None = None) -> McpClient:
    """Process-wide MCP client (Uvicorn --workers 1)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = McpClient(settings)
        return _client


def reset_mcp_client() -> None:
    """Test helper: stop and drop the singleton."""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.stop()
            except Exception:  # noqa: BLE001
                pass
            _client = None
