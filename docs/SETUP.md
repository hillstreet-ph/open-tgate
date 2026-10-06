# Open-TGate — end-to-end setup

This is the single path to run every Open-TGate app: the **API**, the **TDLib
worker**, the **dashboard**, and the **Supabase** schema. Follow it top to
bottom for a fresh environment, or jump to [Verify](#6-verify) to confirm an
existing one.

```
Supabase (schema + auth)
        ▲                 ▲
        │ service key     │ publishable key + RLS
   ┌────┴────┐       ┌────┴─────────┐
   │ worker  │       │ dashboard    │  (Cloudflare Worker, /app)
   │ TDLib   │       │ operator UI  │
   └────┬────┘       └────┬─────────┘
        │                 │
   ┌────┴─────────────────┴────┐
   │ API (FastAPI)             │──► Open-Connect MCP (integrations)
   └───────────────────────────┘
```

## 1. Prerequisites

- Python 3.13, Node 20+, Docker (for the container path).
- A Supabase project. You need its URL, the **publishable** key (browser), and
  the **secret** key (server only).
- Telegram `api_id` / `api_hash` from <https://my.telegram.org> (worker only).
- Optional: an `OPEN_CONNECT_MCP_KEY` from <https://open-connect.site> for
  integrations.

## 2. Supabase

1. Apply migrations in `open-tgate/supabase/migrations/` in filename order.
   The forward-only migrations create the account/command/entity tables, RLS
   policies, the worker heartbeat table, and the login-command action
   vocabulary (including `resend_code`).
2. Confirm RLS is enabled: an authenticated operator may read accounts and
   insert login commands; anonymous access is denied.
3. Note the project URL and both keys.

## 3. Configure environment

Copy `open-tgate/.env.example` to `open-tgate/.env` and fill it in. The
important groups:

```bash
# Telegram / TDLib — worker only
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...

# Supabase
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...   # browser-safe
SUPABASE_SECRET_KEY=...                        # server only, never in the dashboard

# API
API_ADMIN_TOKEN=...            # bearer token for /api/v1/*
DASHBOARD_ORIGIN=http://localhost:3000

# Integrations (optional)
OPEN_CONNECT_MCP_URL=https://open-connect.site/mcp
OPEN_CONNECT_MCP_KEY=...
OPEN_CONNECT_URL=https://open-connect.site/connections
```

## 4. Run the apps

### Docker (recommended)

```bash
cd open-tgate
docker compose up --build
```

This starts `api` (port 8000) and `worker` with a persistent `tdlib-state`
volume at `/data/tdlib`. The worker logs `TDLib loaded` once the native library
is available.

### Local processes

```bash
# API
cd open-tgate
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# Worker (separate shell, same env)
cd open-tgate && PYTHONPATH=. python -m app.worker
```

### Dashboard

```bash
cd open-tgate/dashboard
npm install
# Point the Worker at your API and Supabase project:
#   wrangler.toml -> API_BASE_URL, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, OPEN_CONNECT_URL
npm run dev      # local preview of /app and the landing page
```

`npm run deploy` publishes the dashboard to Cloudflare. `OPEN_CONNECT_URL`
controls whether the sidebar shows the Open-Connect link; leave it empty to hide.

## 5. First login (multi-account)

Open `/app`, sign in with a Supabase Auth operator account, then:

1. **Add account** (sidebar) → name it → the console selects it.
2. Pick a method:
   - **QR code** — scan in Telegram (Settings → Devices → Link Desktop Device),
     or paste the `tg://login?...` link.
   - **Phone number** — enter it in international format, submit the login
     **code**, then the **2FA** password if the account has one.
   - **Bot token** — for bot accounts, paste the BotFather token.
3. Stuck? **Resend code**, **Start over** (resets the account to `pending`
   without creating a duplicate), or the top-left **Back** button.

Every status the worker can write has a UI path; if no worker heartbeat is seen
in the last two minutes the console explains that logins are queued.

## 6. Verify

```bash
# API
curl -s http://localhost:8000/healthz   # {"status":"ok", ...}
curl -s http://localhost:8000/readyz    # ready:true once secrets are set

# Integrations (when a key is set)
curl -s -H "Authorization: Bearer $API_ADMIN_TOKEN" \
  http://localhost:8000/api/v1/integrations/open-connect
```

- Worker liveness: a recent row for `WORKER_ID` in
  `public.open_tgate_worker_heartbeats`.
- Dashboard: `/app` loads, accounts appear in the sidebar, and a login method
  advances the status stepper.

## 7. Tests

```bash
cd open-tgate && PYTHONPATH=. pytest -q && ruff check app tests
cd open-tgate/dashboard && npm test && npm run check
```

CI (`.github/workflows/open-tgate-ci.yml`) runs all of the above plus Docker
image builds and runtime smoke tests. See `deploy/zeabur/README.md` for the
production runtime and `docs/INTEGRATIONS.md` for Open-Connect.
