# Open-TGate Application Layer

This directory contains the HillStreet application layer built around TDLib. Keep product-specific code here instead of modifying TDLib internals.

## Planned services

- `worker/` — persistent TDLib/tdjson synchronization worker
- `api/` — authenticated API for the dashboard and controlled integrations
- `dashboard/` — private operations UI
- `supabase/` — SQL migrations, RLS policies, database functions and storage policy
- `shared/` — schemas/contracts shared between services
- `tests/` — unit, integration, restart/recovery and authorization tests

## First production vertical slice

1. Load native `libtdjson`.
2. Complete Telegram authorization interactively through an authorized operator flow.
3. Persist TDLib state on a mounted volume.
4. Discover the connected account and chats.
5. Persist normalized account/chat records to Supabase.
6. Backfill one selected chat with a durable checkpoint.
7. Restart the worker and prove it resumes rather than re-importing everything.
8. Receive a live message and UPSERT it idempotently.
9. Emit an outbox event after the database transaction succeeds.
10. Keep outbound Telegram sending disabled.

Do not add AI/Notion dependencies to the Telegram synchronization process. Downstream intelligence consumes durable database/outbox records.

See `docs/OPEN_TGATE_ARCHITECTURE.md` for the complete target architecture.
