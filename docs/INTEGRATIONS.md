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


## Open-TGate as a managed connector

The reverse integration exposes Open-TGate workspace operations to Open-Connect
and Composio. Use the existing connector pointing to
`https://open-tgate.site/mcp`; inspect its actual tools and grants before calling.
The repository's `open-tgate/plugin/open-tgate/toolkit.json` records the operation
map and `skills/open-tgate-workspace-operator/SKILL.md` supplies the operator
workflow. The manifest is a specification, not an independently installed executor.

From Composio, discover the existing Open-Connect connection-tool schemas, then
use `CUSTOM_OPEN_CONNECT_LIST_CONNECTION_TOOLS` and
`CUSTOM_OPEN_CONNECT_CALL_CONNECTION_TOOL` with the verified connection ID.
Open-Connect authorization and Open-TGate authorization are separate. A
project-scoped Open-Connect grant needs `connections:invoke` for mutations, and
the stored Open-TGate credential needs each requested workspace scope. A catalog
record or successful account sync alone does not prove execution permissions.
Keep credentials in the provider-managed store; never include them in this
manifest, plugin, skill or request URLs.

Preview Composio synchronization, match existing owned ACTIVE accounts, and apply
only missing intended mappings. Avoid duplicating the functioning MCP bridge
when a separate custom-app record fails discovery. Confirm project assignment
independently from personal-library presence. Verify success with a bounded read
such as `list_accounts`; do not mutate user data merely to test permissions.
Existing read-only bridges remain functional, but new management tools require
the migration, backend deployment and an explicit new grant described in
[the workspace contract](INBOX_WORKSPACE.md#opt-in-management-toolkit).
