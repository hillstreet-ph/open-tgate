# Open-TGate — downstream integrations

Open-TGate keeps third-party integrations off the Telegram sync path. The worker
only writes durable records (accounts, entities, outbox); integrations run on the
API service so a provider outage can never stall a login or an entity sync.

## Open-Connect MCP gateway

[Open-Connect](https://open-connect.site) is the organization's MCP gateway. It
exposes managed, Composio-backed connections (Gmail, Calendar, Notion, Slack, …)
through a single authenticated MCP endpoint:

```
POST https://open-connect.site/mcp
```

Open-TGate talks to it as an MCP client over the Streamable HTTP transport
(`initialize` → `tools/list` / `tools/call`). There is no per-provider SDK in
this repo: adding a provider means connecting it in Open-Connect, not adding
code here.

### Configuration (API service only)

| Variable | Purpose | Secret |
|---|---|---|
| `OPEN_CONNECT_MCP_URL` | MCP endpoint (default `https://open-connect.site/mcp`) | no |
| `OPEN_CONNECT_MCP_KEY` | Bearer key for the gateway | **yes** |
| `OPEN_CONNECT_URL` | Public link shown in the dashboard sidebar | no |

`OPEN_CONNECT_MCP_KEY` is a server secret. It is read only by the API service
(`app/integrations/openconnect.py`) and never injected into the dashboard
bundle. The dashboard receives only `OPEN_CONNECT_URL`, a plain link.

### Admin API

Both endpoints require the `API_ADMIN_TOKEN` bearer token.

```bash
# List the tools Open-Connect advertises (or report it is unconfigured)
curl -H "Authorization: Bearer $API_ADMIN_TOKEN" \
  https://<api-domain>/api/v1/integrations/open-connect

# Invoke a tool
curl -X POST -H "Authorization: Bearer $API_ADMIN_TOKEN" -H "content-type: application/json" \
  -d '{"tool":"gmail_send","arguments":{"to":"ops@example.com","subject":"hi","body":"..."}}' \
  https://<api-domain>/api/v1/integrations/open-connect/call
```

`GET /api/v1/integrations/open-connect` returns a safe, non-throwing summary:

```json
{ "configured": true, "ok": true, "data": null, "error": null, "tools": [{"name": "gmail_send", "description": "..."}] }
```

When no key is set it returns `{"configured": false, "ok": false, "error":
"open_connect_not_configured"}` and the dashboard simply hides the link.

### Failure behavior

The client never raises into the request path for expected conditions. Transport
and auth failures are normalized:

| Situation | `error` |
|---|---|
| No key or endpoint configured | `open_connect_not_configured` |
| DNS/connection failure | `open_connect_unreachable: …` |
| HTTP 4xx/5xx | `open_connect_http_<status>: <gateway message>` |
| JSON-RPC error in a 200 body | `open_connect_error: <message>` |

An unconfigured or unreachable gateway never blocks login, sync, or health.

## Adding a provider

1. Connect the provider in Open-Connect and confirm its tool appears in
   `tools/list`.
2. Call it through `POST /api/v1/integrations/open-connect/call` from an
   automation, or add a thin, tested wrapper under `app/integrations/` if the
   call needs normalization.
3. Do not add provider credentials to this repo — they live in Open-Connect.
