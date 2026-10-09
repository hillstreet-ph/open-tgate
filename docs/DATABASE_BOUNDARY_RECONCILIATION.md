# Database boundary reconciliation

Date: 2026-10-09 UTC. Scope: Telegram inbox PR #68 and its release.

## Decision and current contract

The canonical domain schema remains `open_tgate`. This release retains the
existing `public.open_tgate_*` PostgREST contract as a temporary compatibility
exception. The deployed 1.4.4 API/worker and existing dashboard already use public
account, operator, login-command and entity tables; the inbox feature extends
that contract. This exception does not change the canonical destination or
approve unrelated public-schema objects.

The four feature tables have RLS enabled. Anonymous SELECT is denied.
Authenticated SELECT on chats, messages and knowledge requires an active
allowlisted operator; message reads also exclude deletion tombstones. API key
hashes and checkpoint RPCs remain service-role only. These grants and operator /
non-operator policies were verified against production with rollback-only
fixtures. Namespace location is architecture drift, not a substitute for access
control. Do not broaden grants or disable RLS to perform the later cutover.

The audit at [PR #68](https://github.com/hillstreet-ph/open-tgate/pull/68#issuecomment-6071810349)
correctly identified that feature migrations were applied before the complete
PR's CI/review gates finished. SQL validation and production application were
separate from releasing the application. Application merge/deploy still requires
successful checks and independent review of the exact latest commit. No
application release or destructive rollback is justified by migration presence.

## Applied migration provenance

The eight feature migration records were read from
`supabase_migrations.schema_migrations` and compared with source at
`1f29620d618366a46004e4a81bfc90fdb90ea678`. Every stored SQL statement string
matches its committed file's byte count and content fingerprint below. MD5 here
is a content comparison, not credential or authorization material.

| Version | Name | UTF-8 bytes | Stored/source MD5 |
| --- | --- | ---: | --- |
| `20261009000100` | `open_tgate_inbox_knowledge` | 6067 | `8b2257914f6fdc003d6c90413fe29b27` |
| `20261009000200` | `open_tgate_chat_visibility` | 603 | `6702757bd8e35c30cc1f35c509712eb3` |
| `20261009000300` | `open_tgate_knowledge_search` | 2964 | `d0fc724066a6e3d289c4b71a8d11f641` |
| `20261009000400` | `open_tgate_history_revisions` | 2315 | `f186bd5163321c68d4f56cd3329b26c8` |
| `20261009000500` | `open_tgate_recent_history_resume` | 2648 | `9049246cdcbbd3f5a64629a7e4043322` |
| `20261009000600` | `open_tgate_knowledge_previews` | 1295 | `affaef72e7fd8eede6ad9ce504116622` |
| `20261009000700` | `open_tgate_inbox_order_indexes` | 532 | `280ad3c594d9fb10c5f2eb0b07c12805` |
| `20261009000800` | `open_tgate_targeted_recent_history` | 1271 | `901ae6dd0f95f9b57642b55e4b9309ac` |

Keep these already applied files and records immutable. Earlier migration
history has pre-existing timestamp differences from repository history; this
release does not rewrite or repair those records. Subsequent corrections must
use new reviewed forward migrations. This reconciliation does not claim that
the complete historical migration chain can be blindly replayed into production.

## Forward-only canonical cutover

1. Open and lock a separate database-boundary issue/PR after discovery of current
   assignments. Inventory every API, worker, dashboard and integration consumer,
   plus tables, functions, FK/trigger dependencies, grants, policies, publication
   membership and gateway schema exposure. Existing `open_tgate.telegram_sessions`,
   `message_routes` and `gateway_logs` use a different owner-based contract;
   inventory and preserve them rather than overwrite or map them blindly.
   Never copy Telegram session strings, API hashes or mounted worker credentials
   into a new SQL model as part of namespace work.
2. Implement explicit server PostgREST profile headers and browser schema
   selection, with all affected RPC bodies/search paths. Validate both current
   and proposed layouts in a staging database clone, including authorized reads,
   service writes, 64-bit IDs and denied anonymous/non-operator access. Confirm
   reviewed `open_tgate` gateway exposure before any client switches. Public
   compatibility views alone cannot preserve the old worker's `ON CONFLICT`
   UPSERT behavior; retire old writers or implement and test a complete compatible
   write path before moving relations.
3. Take a restorable backup and validate recovery, checkpoint/drain old workers
   and API/login writes, then run a reviewed transaction moving existing
   relations with `ALTER TABLE ... SET SCHEMA` and rebinding schema-qualified
   SQL functions. This preserves relation identities, rows and FK dependencies.
   Activate the tested clients through an immutable release. Keep the worker's
   mounted session volume attached throughout.
4. Require exact API version readiness, a new active worker boot, positive
   authenticated REST/MCP checks, denied unauthorized reads/writes, correct RLS /
   grants and stable/increasing mirrored counts. Retire compatibility only after
   old writers are gone and the new layout has completed its agreed soak period.
   If recovery is needed, use the tested compatible client/image and a new
   forward repair migration; do not drop mirrored data, rewrite migration
   history, reset Telegram sessions or depend on untested public views.

The cutover is a separate reviewed delivery because it changes every existing
writer and the shared project's gateway contract. The inbox release's deployment
must keep the existing schema contract and credentials while satisfying its
exact-head review, CI, immutable-image and live verification gates.
