# Open-TGate — agent notes

Scope: the `open-tgate/` application (FastAPI API + TDLib worker + Cloudflare
Worker operator console). The repository root also contains upstream TDLib
sources; changes for this product live under `open-tgate/`.

## Layout

- `open-tgate/app/` — FastAPI API and the TDLib worker (`app/telegram/`).
- `open-tgate/tests/` — pytest suite for the API, login state machine, and bus.
- `open-tgate/dashboard/` — Cloudflare Worker serving the landing page and the
  login-gated operator console at `/app` (`src/app.js`, `src/index.js`).
- `open-tgate/supabase/` — SQL migrations. Account status/type/action
  vocabularies are defined here; keep app constants a subset of the DB CHECK
  constraints (e.g. bot action is `start_bot_token`, account type is `user`/`bot`).

## Commands

Run from `open-tgate/`:

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q          # unit tests
ruff check app tests            # lint
```

Dashboard (from `open-tgate/dashboard/`):

```bash
npm install
npm run check   # wrangler deploy --dry-run
npm test        # node:test contract tests in test/
```

CI runs all of the above (`.github/workflows/open-tgate-ci.yml`), plus Docker
image builds and runtime smoke tests.

## Conventions

- Keep secrets out of the dashboard bundle; only `SUPABASE_URL` and the
  publishable key are injected. Optional integrations are passed as Worker vars
  (see `OPEN_CONNECT_URL` in `wrangler.toml`).
- Downstream integrations go through the Open-Connect MCP gateway
  (`app/integrations/openconnect.py`, `app/api_integrations.py`). The MCP key is
  an API-only secret; never expose it to the dashboard. See
  `docs/INTEGRATIONS.md`.
- The operator console keeps browser history in step with its panes so the
  Back button and deep links (`#account=<id>`) work on mobile and desktop. Only
  navigational panes (`NAV_PANES`) rewrite the URL; loading/login/recovery panes
  must not, or they clobber a deep link before it is read.
- Every login status the worker can write must map to a UI path (method picker
  or concrete form) so an operator is never stranded on a spinner. Login-command
  actions are a subset of the DB CHECK constraint (add `resend_code` there too).
- Never log or persist login secrets (bot tokens, codes, passwords); consume
  them once and clear them after dispatch.

## Releases and the backend deploy

- `app.__version__` must equal the release tag: `/readyz` and worker heartbeats
  report it, and the Zeabur deploy gate (`zeabur-deploy.yml`) refuses to pass
  until `/readyz` reports `version == image_tag`. release-please keeps it in
  sync via `extra-files` in `release-please-config.json`; a test
  (`tests/test_release_version.py`) pins the wiring.
- An automated release publishes images by *calling* `release-dockerhub.yml` as
  a reusable workflow from `release-please.yml`. A tag pushed with
  `GITHUB_TOKEN` does not fire `push: tags`, and `GITHUB_TOKEN` cannot create a
  `workflow_dispatch` event, so neither of those paths works for an automated
  release.
- Deploy the backend with `zeabur-deploy.yml` (`mode=deploy`,
  `image_tag=<version>`); the dashboard and Supabase migrations ship on push to
  master via `deploy.yml`. The migration leg is skipped until `SUPABASE_DB_URL`
  is configured as a repository secret — so a schema-widening migration (e.g.
  the `resend_code` action) must be applied before its UI is used.
- Open-Connect (Composio-backed integrations) is reached over MCP at
  `https://open-connect.site/mcp`; the API only needs `OPEN_CONNECT_MCP_KEY` to
  enable it.
