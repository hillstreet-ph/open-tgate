"""Admin API for downstream integrations (Open-Connect MCP).

These endpoints let an operator (or automation holding the admin token) discover
which managed integrations Open-Connect exposes, and optionally invoke a tool.
They are read/utility only: nothing here touches Telegram session material, and
the MCP key stays on the server.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .config import get_settings
from .integrations.openconnect import OpenConnectMCP
from .security import require_admin

router = APIRouter(prefix="/api/v1/integrations", dependencies=[Depends(require_admin)])


class ToolCall(BaseModel):
    tool: str
    arguments: dict | None = None


def _client() -> OpenConnectMCP:
    settings = get_settings()
    return OpenConnectMCP(settings.open_connect_mcp_url, settings.open_connect_mcp_key)


@router.get("/open-connect")
def open_connect_status() -> dict[str, object]:
    """List the MCP tools Open-Connect advertises (or report it is unconfigured)."""

    return _client().status().as_dict()


@router.post("/open-connect/call")
def open_connect_call(body: ToolCall) -> dict[str, object]:
    tool = body.tool.strip()
    if not tool:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tool_required")
    result = _client().call(tool, body.arguments)
    if result.configured and not result.ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result.error)
    return result.as_dict()
