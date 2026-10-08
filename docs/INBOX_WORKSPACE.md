# Telegram inbox workspace

Open `/app` and sign in as an allowlisted operator. Settings retains the existing
multi-account phone, QR and bot login flows. User accounts mirror main and archived
chats, contacts, unread state and message history; bot accounts receive updates but
Telegram does not permit bots to import arbitrary historical conversations.

History backfill is paced and resumes from per-chat checkpoints. Initial account
sync completion describes the chat/contact inventory; each conversation separately
shows history progress. Large accounts need multiple passes. Sessions remain on
the worker's `/data/tdlib` volume. Activity reports worker health, Telegram connection
state and the last event observed by this worker; it is not an online presence tracker.

AI Knowledge accepts pasted text and UTF-8 text/Markdown files. Sources must be
enabled and approved to participate in retrieval. Drafting uses bounded literal
retrieval and sends matching excerpts plus at most 20 selected messages to the
configured provider. Operators review drafts; generating a draft never sends it.
On the API service, configure `AI_API_KEY`, `AI_MODEL`, and optionally `AI_BASE_URL`
for an OpenAI-compatible chat-completions provider. When absent, the dashboard
reports the missing configuration. Webpage crawling and binary document parsing
are not implemented.

## Integration access

Settings → API / MCP creates a scoped key, displayed once. Only a SHA-256 hash is
stored. `read` permits accounts, chats, contacts and history; `knowledge:read`
permits searching approved sources. Revocation and disabling the key owner's
operator entry take effect on the next request. Keys cannot send messages, manage
knowledge, create keys or submit login commands. Existing operators share one
workspace under the existing operator allowlist; this release does not add tenant
isolation.

Use `https://open-tgate.site/mcp` as the MCP Streamable HTTP server URL with
`Authorization: Bearer YOUR_API_KEY`. The stateless server supports JSON POST
requests, initialization, tool discovery, ping and read-only tool calls; no SSE
subscription is provided. Tools are `list_accounts`, `list_chats`, `list_contacts`,
`get_history`, and `search_knowledge`. Returned message IDs are strings to preserve
Telegram's 64-bit IDs in JavaScript clients.

REST reads at `https://open-tgate.site/api/v1/workspace` use the same header:

| GET path | Parameters |
| --- | --- |
| `/accounts`, `/activity` | None |
| `/chats`, `/contacts` | Optional `account_id`, `limit` (1–200), `offset` |
| `/messages` | Required `account_id`, `chat_id`; optional `before`, `limit` |

Message pages are newest first. Pass the oldest returned `message_id` as `before`
for the next page. Operator login tokens additionally authorize knowledge CRUD,
`POST /ai/draft`, and their own key management. Requests through the edge forward
the caller's bearer token only; administrative and Telegram-command routes remain
blocked there. No upstream credentials are injected into browser requests.

## Deployment and verification

Apply `20261009000100_open_tgate_inbox_knowledge.sql` before deploying the new API
and worker. It adds chats, messages, source and key tables, operator read policies,
explicit grants, and a trigger preserving newer edits/deletion tombstones during
backfill. API key hashes are service-role only. Existing account/session rows are
preserved. Publish immutable images through the existing release workflow and
verify `/readyz`, the current worker heartbeat, database grants/RLS and increasing
chat/message counts. Production `/docs` remains disabled; this file documents the
workspace integration contract.
