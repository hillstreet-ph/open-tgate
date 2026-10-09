"""Open-Connect MCP client (https://open-connect.site/mcp).

Open-Connect is the org's MCP gateway. Open-TGate uses it as the single place to
reach managed integrations (Composio-backed connections) instead of embedding
per-provider SDKs. Only the API service talks to it, and only with the server's
gateway bearer key, so the MCP key never reaches the browser.

The client speaks the Streamable HTTP MCP transport: ``initialize`` followed by
``tools/list`` / ``tools/call``, with an optional ``notifications/initialized``.
It is intentionally tiny — no MCP SDK dependency — and degrades to a clear
``configured: false`` result when no key or endpoint is set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "open-tgate", "version": "1.0.0"}


class OpenConnectError(RuntimeError):
    """Raised when Open-Connect is unreachable or returns a JSON-RPC error."""


@dataclass
class OpenConnectResult:
    """A normalized MCP result the API can return without leaking transport details."""

    configured: bool
    ok: bool
    data: Any = None
    error: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "tools": self.tools,
        }


def _parse_sse(body: str) -> dict[str, Any] | None:
    """Return the first JSON-RPC message from a Streamable HTTP SSE body."""

    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            continue
    return None


def _error_message(response: httpx.Response) -> str:
    """Extract a human-readable message from an error response body."""

    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:200] or "request_failed"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "request_failed")
        if isinstance(error, str):
            return error
        if body.get("message"):
            return str(body["message"])
    return "request_failed"


class OpenConnectMCP:
    """Minimal MCP-over-HTTP client for the Open-Connect gateway."""

    def __init__(self, endpoint: str, key: str, *, timeout: float = 20.0) -> None:
        self._endpoint = (endpoint or "").strip()
        self._key = (key or "").strip()
        self._timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0

    @property
    def configured(self) -> bool:
        return bool(self._endpoint and self._key)

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "authorization": f"Bearer {self._key}",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params
        try:
            response = httpx.post(
                self._endpoint, headers=self._headers(), content=json.dumps(body), timeout=self._timeout
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The gateway answers auth/quota problems with a JSON body; surface
            # that message instead of a generic transport error.
            raise OpenConnectError(f"open_connect_http_{exc.response.status_code}: {_error_message(exc.response)}") from exc
        except httpx.HTTPError as exc:
            raise OpenConnectError(f"open_connect_unreachable: {exc}") from exc

        # Streamable HTTP may answer with SSE; fall back to plain JSON.
        content_type = response.headers.get("content-type", "")
        message = _parse_sse(response.text) if "text/event-stream" in content_type else None
        if message is None:
            try:
                message = response.json()
            except ValueError as exc:
                raise OpenConnectError("open_connect_invalid_response") from exc

        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if isinstance(message, dict) and message.get("error"):
            error = message["error"]
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise OpenConnectError(f"open_connect_error: {detail}")
        return message if isinstance(message, dict) else {}

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no id; the server does not reply)."""

        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        try:
            httpx.post(self._endpoint, headers=self._headers(), content=json.dumps(body), timeout=self._timeout)
        except httpx.HTTPError:
            pass

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self._notify("notifications/initialized", {})
        return result.get("result", {})

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list", {})
        tools = (result.get("result") or {}).get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return result.get("result")

    def draft_completion(self, model: str, messages: list[dict[str, str]]) -> str:
        """Use the verified Open-Connect model gateway, never a provider URL/tool.

        Contract: hillstreet-ph/open-connect docs/MODEL_GATEWAY.md and
        src/routes/v1/chat/completions.ts. The existing server key needs
        models:invoke and a connected upstream in Open-Connect.
        """
        if not self.configured or not model.strip():
            raise OpenConnectError("open_connect_ai_not_configured")
        try:
            response = httpx.post(
                "https://open-connect.site/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "temperature": 0.2,
                      "max_tokens": 800, "stream": False}, timeout=40,
                follow_redirects=False,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("error"):
                raise OpenConnectError("open_connect_model_error")
            draft = result["choices"][0]["message"]["content"]
            if not isinstance(draft, str) or not draft.strip():
                raise OpenConnectError("open_connect_empty_draft")
            return draft
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            # Never surface transport headers, gateway secrets, source text or
            # provider errors containing submitted conversation data.
            raise OpenConnectError("open_connect_model_unavailable") from exc

    def status(self) -> OpenConnectResult:
        """Return a safe, non-throwing summary for the admin endpoint."""

        if not self.configured:
            return OpenConnectResult(configured=False, ok=False, error="open_connect_not_configured")
        try:
            tools = self.list_tools()
        except OpenConnectError as exc:
            return OpenConnectResult(configured=True, ok=False, error=str(exc))
        return OpenConnectResult(
            configured=True,
            ok=True,
            tools=[
                {
                    "name": t.get("name"),
                    "description": (t.get("description") or "")[:200],
                }
                for t in tools
            ],
        )

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> OpenConnectResult:
        if not self.configured:
            return OpenConnectResult(configured=False, ok=False, error="open_connect_not_configured")
        try:
            data = self.call_tool(tool, arguments)
        except OpenConnectError as exc:
            return OpenConnectResult(configured=True, ok=False, error=str(exc))
        return OpenConnectResult(configured=True, ok=True, data=data)
