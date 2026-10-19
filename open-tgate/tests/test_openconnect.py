"""Tests for the Open-Connect MCP client and its admin API surface.

No network: the transport is exercised through monkeypatched httpx responses so
the suite stays hermetic. The point is the contract the API depends on — a clear
``configured: false`` when no key is set, and SSE/JSON response parsing when it is.
"""

import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.integrations import openconnect
from app.main import app

client = TestClient(app)


class _Response:
    def __init__(self, *, text="", payload=None, content_type="application/json", headers=None, status=200):
        self.text = text if payload is None else json.dumps(payload)
        self._payload = payload
        self.headers = {"content-type": content_type}
        if headers:
            self.headers.update(headers)
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://open-connect.site/mcp")
            response = httpx.Response(
                self.status_code, text=self.text, headers={"content-type": "application/json"}, request=request
            )
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_unconfigured_client_reports_not_configured():
    result = openconnect.OpenConnectMCP("", "").status()
    assert result.configured is False
    assert result.ok is False
    assert result.error == "open_connect_not_configured"


def test_list_tools_parses_json_transport(monkeypatch):
    calls = []

    def fake_post(url, headers=None, content=None, timeout=None):
        calls.append(json.loads(content))
        method = calls[-1]["method"]
        if method == "notifications/initialized":
            return _Response(payload={})
        if method == "initialize":
            return _Response(payload={"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "open-connect"}}},
                             headers={"mcp-session-id": "s1"})
        return _Response(payload={"jsonrpc": "2.0", "id": 2,
                                  "result": {"tools": [{"name": "gmail_send", "description": "Send mail"}]}})

    monkeypatch.setattr(openconnect.httpx, "post", fake_post)
    mcp = openconnect.OpenConnectMCP("https://open-connect.site/mcp", "key")
    status = mcp.status()
    assert status.configured is True and status.ok is True
    assert status.tools[0]["name"] == "gmail_send"
    # The notification must not carry an id (JSON-RPC notification, not a request).
    assert "id" not in calls[-2]


def test_sse_transport_is_parsed(monkeypatch):
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"t"}]}}\n\n'

    def fake_post(url, headers=None, content=None, timeout=None):
        method = json.loads(content)["method"]
        if method == "notifications/initialized":
            return _Response(payload={})
        if method == "initialize":
            return _Response(payload={"jsonrpc": "2.0", "id": 1, "result": {}})
        return _Response(text=body, content_type="text/event-stream")

    monkeypatch.setattr(openconnect.httpx, "post", fake_post)
    mcp = openconnect.OpenConnectMCP("https://open-connect.site/mcp", "key")
    assert [t["name"] for t in mcp.list_tools()] == ["t"]


def test_jsonrpc_error_becomes_open_connect_error(monkeypatch):
    def fake_post(url, headers=None, content=None, timeout=None):
        method = json.loads(content)["method"]
        if method in ("initialize", "notifications/initialized"):
            return _Response(payload={"jsonrpc": "2.0", "id": 1, "result": {}})
        return _Response(payload={"jsonrpc": "2.0", "id": 2,
                                  "error": {"code": -32000, "message": "invalid key"}})

    monkeypatch.setattr(openconnect.httpx, "post", fake_post)
    mcp = openconnect.OpenConnectMCP("https://open-connect.site/mcp", "key")
    status = mcp.status()
    assert status.ok is False
    assert "invalid key" in status.error


def test_unreachable_endpoint_is_reported(monkeypatch):
    def fake_post(url, headers=None, content=None, timeout=None):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(openconnect.httpx, "post", fake_post)
    mcp = openconnect.OpenConnectMCP("https://open-connect.site/mcp", "key")
    status = mcp.status()
    assert status.configured is True and status.ok is False
    assert "open_connect_unreachable" in status.error


def test_http_error_body_is_surfaced(monkeypatch):
    def fake_post(url, headers=None, content=None, timeout=None):
        return _Response(
            payload={"error": {"message": "Missing or invalid Open-Connect key.", "code": "invalid_api_key"}},
            status=401,
        )

    monkeypatch.setattr(openconnect.httpx, "post", fake_post)
    status = openconnect.OpenConnectMCP("https://open-connect.site/mcp", "bad").status()
    assert status.configured is True and status.ok is False
    assert "open_connect_http_401" in status.error
    assert "Missing or invalid Open-Connect key." in status.error


def test_admin_endpoint_requires_token():
    assert client.get("/api/v1/integrations/open-connect").status_code == 401


def test_admin_endpoint_reports_unconfigured_without_key(monkeypatch):
    monkeypatch.setattr(openconnect.httpx, "post", lambda *a, **k: _Response(payload={}))
    settings = Settings(api_admin_token="test-admin", open_connect_mcp_key="")
    monkeypatch.setattr("app.api_integrations.get_settings", lambda: settings)
    monkeypatch.setattr("app.security.get_settings", lambda: settings)
    response = client.get("/api/v1/integrations/open-connect", headers={"Authorization": "Bearer test-admin"})
    assert response.status_code == 200
    assert response.json()["configured"] is False
