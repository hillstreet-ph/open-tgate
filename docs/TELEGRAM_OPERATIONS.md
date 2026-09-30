# Open-TGate account operations

Open-TGate is a third-party TDLib application, not an official Telegram app.
No implementation can promise that Telegram will never restrict an account.

## Component map

| Layer | Canonical location | Responsibility |
|---|---|---|
| Frontend | Cloudflare, https://open-tgate.site/app | Supabase operator sign-in, account login/status |
| API | Zeabur open-tgate-api | Protected administrative API, readiness |
| Worker | Zeabur open-tgate-worker | TDLib session lifecycle and read-only metadata sync |
| Database | Supabase open-operations | Account rows, commands, metadata entities, heartbeats |
| Session state | Worker /data/tdlib volume | Isolated TDLib database/files per account |
| Documentation | Notion Open-TGate knowledge base | Engineering configuration and source references |

The browser inserts commands under Supabase RLS. The worker consumes them and
updates account state. Telegram API hash and Supabase service credentials remain
server-side. Operator access is currently shared across accounts: isolated session
directories do not imply per-staff data authorization.

## Connect an account

1. Sign into the console as an active operator and check worker freshness.
2. Add a clearly labelled authorized business account.
3. Choose QR and scan in Telegram Settings → Devices → Link Desktop Device,
   or use the international phone number and Telegram-delivered code.
4. Enter 2FA only in the console when requested, never in documentation or logs.
5. Confirm authorized state, profile and synced entity counts.
6. Repeat separately for each account, then verify persistence after a restart.
7. Wait out rate-limit errors. Do not use repeated logins or account rotation to
   bypass restrictions. A failed sync is not a completed sync.

Bot login uses a token and has different Telegram capabilities. Do not assume
that bots can enumerate user-style contacts or history. Email-code login is not
implemented; use a supported method if Telegram requires email authorization.

## Release gates

Use the existing PR and a new immutable release. Version 1.3.4 adds rate-limit
handling, cancelled stale sync tasks, scoped worker verification and code-version
readiness. The deploy workflow requires the exact supported service names, a
probeable API domain, required secrets, matching API/worker version, and a fresh
heartbeat from open-tgate-worker-1 with a changed boot ID.

Test code, publish the release images, deploy, then check actual login and session
recovery. A successful build or heartbeat alone does not verify account sync.
Retain the prior immutable image and mounted volume for rollback. Do not erase
TDLib sessions to recover an unrelated deployment failure.

## Scope and downstream knowledge

Current sync mirrors profile, chats and contacts as metadata. Full message
history, attachment bytes, durable message checkpoints/outbox, Notion data export
and automated AI summaries are not implemented. Do not claim that all knowledge
or files have been retrieved. Notion holds raw engineering documentation, not a
blanket copy of private Telegram content. Review Telegram API terms and applicable
permissions before adding AI processing of Telegram data.

Sources: https://core.telegram.org/api/auth and https://core.telegram.org/api/terms.
