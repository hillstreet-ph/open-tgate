# ChatGPT and Open-Connect integration

Use the existing hosted server `https://open-tgate.site/mcp`. The server supplies
OAuth protected-resource and authorization-server metadata, public-client Dynamic
Client Registration, operator consent, S256 PKCE, atomic single-use authorization
codes (five minutes), one-hour access tokens, rotating refresh tokens with a
fixed thirty-day expiry, replay-triggered grant revocation, and explicit revocation. No client secret needs to be copied into
ChatGPT. Registration accepts HTTPS callbacks on the configured
`OAUTH_REDIRECT_HOSTS` allowlist; defaults cover ChatGPT, Open-Connect and Composio.
Only `read` and `knowledge:read` scopes are granted. Operator deactivation and key
revocation apply to every request and refresh. OAuth tokens are stored as hashes. Shared database admission control limits public
registration to ten clients per minute across all API replicas, returning HTTP 429
with a retry delay when exhausted. Abandoned registrations expire after 30 minutes
and cannot fill the 1,000-client capacity through a rapid registration burst.

In ChatGPT Plugins, add the MCP server URL, choose OAuth, and choose Dynamic Client
Registration. Sign in to the existing Open-TGate operator console and explicitly
approve the requested scopes. The sign-in/consent page works in mobile browsers.
The request survives a Supabase identity-provider redirect in session storage.
An unsigned request alone never grants access. A private plugin package lives in
`open-tgate/plugin/`; it connects to this same deployment.

Open-Connect currently supports bearer, API-key and unauthenticated Custom MCP
connections; its Custom MCP provider does not yet support OAuth. Create a scoped
bearer key under Open-TGate Settings → API / MCP and save it in Open-Connect
Credentials, then create a Custom MCP connection to this URL. Never put
a key in a plugin archive, repository, chat, URL or dashboard JavaScript. A saved
connection alone is not proof of authentication: verify tool discovery and a
read-only account-list request.

## History and account monitoring

Activity → History & audit log filters all accounts or a selected account and
pages through observed account-state changes, login command actions/statuses,
message edits/deletions, removed chats, and removed account/message records.
Only allowlisted fields are captured; passwords, codes, bot tokens, QR links,
login command payloads, raw errors and raw TDLib events are excluded.

Deletion tombstones retain the last captured message snapshot. A delayed durable
snapshot can fill an empty tombstone without restoring inbox visibility. Audit
snapshots survive account deletion and its FK cascades. The audit table permits
service-role SELECT/INSERT only; triggers append snapshots. Existing deletion
markers are imported as `existing_deletion_marker` with unknown deletion time.
No deletion time is invented, and messages deleted before capture cannot be
recovered. Logging observes activity available to connected accounts and this
worker; it cannot observe arbitrary other Telegram clients' login events.

New read-only MCP tools: `list_audit_events` and `get_deleted_messages`.
REST: `GET /api/v1/workspace/audit` accepts `account_id`, `chat_id`, `before` (an
opaque string event ID) and `limit` (1–200). `GET /deleted` accepts account/chat
filters, `offset`, and `limit`. Returned Telegram message and event IDs are strings.
Existing `get_history` can read retained history for a known removed chat ID.

## Deployment and rollback

Apply the OAuth and audit migrations after review and green CI, before deploying
the new API. Both extend the existing public prefixed tables, preserving current
account/session identities and the known legacy schema boundary. They do not move
production data to another schema. Reconcile that separate boundary explicitly.
Run rollback-only `tests/sql/audit_history_integrity.sql`, test code replay,
refresh replay, client/redirect/resource mismatch, and revocation. Verify public
metadata JSON, the unauthenticated MCP challenge, tool discovery, worker heartbeat,
and all existing inbox tests. Publish the existing API/worker image process and
Cloudflare edge. Roll back runtime to immutable `1.5.0` images if health fails;
retain the additive tables and snapshots, rather than dropping captured history.
