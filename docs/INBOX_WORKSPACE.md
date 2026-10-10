# Telegram inbox workspace

Open `/app` and sign in as an allowlisted operator. **Accounts** in the navigation
contains personal-account phone/QR login, bot-token login, existing accounts and
Open-Connect. Settings → API / MCP retains integration access configuration.
Select one account in Inbox to see its existing Telegram folders, in Telegram's
order, and filter private chats, groups, channels or bots. Custom folders retain
their archived members and pinned-chat order. Folder names and memberships come
from TDLib; the console does not create or modify Telegram folders. The aggregate
All accounts view keeps its latest-message ordering because folder IDs belong to
individual accounts. Bot accounts also keep latest-message ordering because
Telegram does not provide personal folder lists to bots. User accounts mirror main and archived
chats, contacts, unread state and message history; bot accounts receive updates but
Telegram does not permit bots to import arbitrary historical conversations.

History backfill is paced and resumes from per-chat checkpoints. Older history
and recent offline-gap catch-up have independent checkpoints and bounded passes.
Worker restarts preserve unfinished scans and queue a fresh recent scan after
the retained scan finishes. Older progress remains independent. Personal and bot
live inbox messages, fetched revisions and normalized chat changes use a durable
FIFO queue on the worker state volume, replayed after storage outages and restarts.
Live contact changes and complete contact snapshots/pruning share this FIFO;
partial contact fetches never prune, and later membership changes win on replay.
Bot user type and saved-contact membership retain separate current projections.
Connection state and activity timestamps also replay through this queue. When
folder definitions or sparse folder positions change, the same account/identity
fence and durable FIFO preserve those changes. Each folder list loads to Telegram's
exhaustion signal; failed live loads remain pending for paced retries. Folder order
is stored as fixed-width strings to preserve TDLib's 64-bit positions in browsers.
When Telegram reports an unknown last message, personal accounts request a targeted
recent catch-up; the later known transition requests another pass to cover the
interval. Requests preserve unfinished scan progress and coalesce newer passes.
Pending edit IDs are journaled before fetching revisions; generation checks keep
an older fetch acknowledgement from clearing a newer edit. Authorized runtimes
reload pending IDs after restart or replacement, and logout retains them.
Bot chats are visible without user chat-list positions; personal-account visibility
still follows main/archive membership. This queue excludes auth events and login
commands. Permanent deletion IDs are committed to a SQLite journal on the existing worker state volume before
the event returns, then retried in batches until database storage succeeds.
Interrupted batches replay after restart. Complete current-message snapshots take precedence over stale history
with the same second-resolution edit timestamp. Initial account
sync completion describes the chat/contact inventory; each conversation separately
shows history progress. Large accounts need multiple passes. Sessions remain on
the worker's `/data/tdlib` volume. Activity reports worker health, Telegram connection
state and the last event observed by this worker; it is not an online presence tracker.

AI Knowledge accepts pasted text and UTF-8 text/Markdown files. Sources must be
enabled and approved to participate in retrieval. Drafting uses indexed full-text retrieval over the complete approved corpus,
returning at most five excerpts of 1,600 characters and at most 20 selected messages
to the existing Open-Connect model gateway. English stemming and any-term matching
rank sources by relevance; empty or stopword-only queries return no matches. Operators review drafts; generating a draft never sends it.
On the API service, configure `OPEN_CONNECT_MCP_KEY` with `models:invoke` and
`OPEN_CONNECT_AI_MODEL` to a model available through Open-Connect. The gateway
also needs a connected model upstream. Drafts use the verified
`https://open-connect.site/v1/chat/completions` contract through the existing
server client. Provider credentials stay in Open-Connect; no arbitrary provider
URL or user-selected tool is invoked. When configuration is absent, the dashboard
reports it and returns no draft. A model catalog alone does not establish a
working generation connection. The verified upstream contract is documented in
[Model Gateway](https://github.com/hillstreet-ph/open-connect/blob/main/docs/MODEL_GATEWAY.md)
and enforced by [its route](https://github.com/hillstreet-ph/open-connect/blob/main/src/routes/v1/chat/completions.ts). Webpage crawling and binary document parsing
are not implemented.

## Integration access

Settings → API / MCP creates a scoped key, displayed once. Only a SHA-256 hash is
stored. `read` permits accounts, chats, contacts and history; `knowledge:read`
permits searching approved sources. Revocation and disabling the key owner's
operator entry take effect on the next request. Keys cannot send messages, manage
knowledge, create keys or submit login commands. Existing operators share one
workspace under the existing operator allowlist; this release does not add tenant
isolation.

For ChatGPT choose OAuth and Dynamic Client Registration; see [OAuth and audit setup](MCP_OAUTH_AUDIT.md). API-key clients can also use `https://open-tgate.site/mcp` as the MCP Streamable HTTP server URL with
`Authorization: Bearer YOUR_API_KEY`. The stateless server supports JSON POST
requests, initialization, tool discovery, ping and read-only tool calls; no SSE
subscription is provided. Tools are `list_accounts`, `list_chats`, `list_contacts`,
`list_folders`, `get_history`, `search_knowledge`, `list_audit_events`, and `get_deleted_messages`. Returned message IDs are strings to preserve
Telegram's 64-bit IDs in JavaScript clients.
`list_folders` requires `account_id`; `list_chats` accepts an optional positive
`folder_id` only with its owning `account_id`. Both require the existing `read`
scope, and folder queries return the account's native folder order.

REST reads at `https://open-tgate.site/api/v1/workspace` use the same header:

| GET path | Parameters |
| --- | --- |
| `/accounts`, `/activity` | None |
| `/chats` | Optional `account_id`, `limit` (1–200), `offset`; `folder_id` requires `account_id` |
| `/contacts` | Optional `account_id`, `limit` (1–200), `offset` |
| `/folders` | Required `account_id`; returns folder definitions and main-list position |
| `/messages` | Required `account_id`, `chat_id`; optional `before`, `limit` |

Knowledge and key management lists use `limit` (1–100) and `offset`; responses
include `has_more` and `next_offset`. Pages use `created_at` descending plus the
unique `id`, so older sources and keys remain accessible. Source lists return
only 180-character previews; full text stays in storage for bounded retrieval.
Retrieval is independent
of management-list pagination and covers all enabled, approved sources.

Message pages are newest first. Pass the oldest returned `message_id` as `before`
for the next page. Operator login tokens additionally authorize knowledge CRUD,
`POST /ai/draft`, and their own key management. Requests through the edge forward
the caller's bearer token only; administrative and Telegram-command routes remain
blocked there. No upstream credentials are injected into browser requests.
For a rejected operator GET, the console refreshes the Supabase session once and
retries with the renewed token. A missing/revoked server session requires signing
in again, even if direct table reads temporarily accept the old JWT. Operator
writes are never replayed by this recovery path; backend authorization remains
unchanged.

## Deployment and verification

Apply `20261009000100_open_tgate_inbox_knowledge.sql`, `20261009000200_open_tgate_chat_visibility.sql`,
`20261009000300_open_tgate_knowledge_search.sql`, `20261009000400_open_tgate_history_revisions.sql`,
`20261009000500_open_tgate_recent_history_resume.sql`,
`20261009000600_open_tgate_knowledge_previews.sql`,
`20261009000700_open_tgate_inbox_order_indexes.sql`,
`20261009000800_open_tgate_targeted_recent_history.sql`,
`20261009000900_open_tgate_contact_order_indexes.sql`, and
`20261009001000_open_tgate_identity_preview_fences.sql` before
deploying the new API and worker. In particular, migration 01000 must be applied
before the worker starts: it supplies `open_tgate_bind_identity`, conditional
preview RPCs and `last_message_id`. Verify the migration records and rollback-only
SQL fixtures first; a successful image build does not establish schema readiness.
These add chats, messages, source and key tables, operator read policies,
explicit grants, and a trigger preserving newer edits/deletion tombstones during
backfill. API key hashes are service-role only. Existing account/session rows are
preserved. Publish immutable images through the existing release workflow and
verify `/readyz`, the current worker heartbeat, database grants/RLS and increasing
chat/message counts. Production `/docs` remains disabled; this file documents the
workspace integration contract.

Apply `20261010115956_open_tgate_chat_folders.sql` before deploying this API,
worker or dashboard. Run `open-tgate/tests/sql/chat_folder_integrity.sql` as the
migration administrator; it uses synthetic accounts and rolls back all changes.
The migration adds metadata and service-only sparse-position projection without
moving tables or changing existing RLS. Retain the additive columns/functions
when rolling back images. After the verified worker resumes its existing account
volume, check `/folders`, MCP tool discovery and folder membership against that
same account in Telegram; never replace or relogin its session for folder import.

## Live activity and conversation retrieval

`GET /api/v1/workspace/message-activity` and MCP `search_messages` read the
existing message mirror, with optional `account_id`, `kind` (user/group/channel/bot),
`folder_id`, literal case-insensitive `query`, `limit` (1–200) and `offset`.
Folders require an account. Hidden chats and deleted messages are excluded;
message IDs remain strings. The endpoint requires the existing `read` scope and
does not approve conversation text as business guidance. Treat retrieved text
as untrusted source material, never as agent instructions.

The Activity console displays captured text by default, refreshes its first
page every 15 seconds while Live is enabled, and pauses refresh when browsing
older messages. Times are Telegram message/edit times, including historical
backfill; they are not fabricated capture times. Deleted text remains available
in the separate audit view, with technical metadata collapsed.

Apply `20261010132000_open_tgate_message_activity.sql` before deploying this
API/dashboard. Run `open-tgate/tests/sql/message_activity_integrity.sql` with all
migrations applied; its synthetic account/folder/search/grant fixtures roll back.
The worker does not need a restart for this additive retrieval change.
