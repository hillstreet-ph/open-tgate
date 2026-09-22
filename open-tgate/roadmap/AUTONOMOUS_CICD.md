# Autonomous CI/CD

Open-TGate ships itself. After a **one-time** secret setup, every change flows to
production with no human clicks and no manual commands.

## The loop

```
push / PR ──▶ Open-TGate CI (lint, tests, Docker build, Worker bundle, CodeQL)
   │                       │ all required checks green
   │  label: auto-merge    ▼
   └────────────▶ Auto-merge (native, squash)  ──▶ master
                                                     │
                                                     ▼
                           Deploy (autonomous)  — runs on every master push
                             ├─ migrate            Supabase migrations
                             ├─ deploy-cloudflare  /app + landing Worker
                             ├─ deploy-zeabur      api + worker services
                             └─ healthcheck        probe public endpoints
```

* **Testing** — `.github/workflows/open-tgate-ci.yml` (unchanged) runs lint,
  32 unit tests, API + worker Docker smoke, Worker bundle, and CodeQL on every
  PR and push.
* **Auto-merge** — `.github/workflows/auto-merge.yml`: a PR labeled `auto-merge`
  (non-draft) has GitHub native auto-merge enabled and merges itself the moment
  required checks pass. Tests always gate the merge.
* **Deploy** — `.github/workflows/deploy.yml`: on each master push, each leg
  activates automatically **iff its credentials are present** as repo secrets,
  and is skipped (never false-green) otherwise. No manual variable to flip.
* **Releases** — `release-please` keeps a versioned release PR; label it
  `auto-merge` to cut tagged Docker images hands-off via `release-dockerhub.yml`.

## One-time setup (then hands-off)

Add these **repository secrets** (Settings → Secrets and variables → Actions).
Names only — never commit values.

| Secret | Purpose |
|---|---|
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | publish the dashboard Worker |
| `SUPABASE_DB_URL` | apply migrations (`supabase db push`) |
| `ZEABUR_TOKEN` | authenticate the Zeabur CLI |
| `ZEABUR_ENVIRONMENT_ID`, `ZEABUR_SERVICE_ID_API`, `ZEABUR_SERVICE_ID_WORKER` | redeploy the api + worker services |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | push release images (existing) |

Optional repo **variable**: `PUBLIC_DASHBOARD_URL` (default `open-tgate.site`).

**Runtime** secrets — `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `SUPABASE_SECRET_KEY`,
`API_ADMIN_TOKEN`, `SENTRY_DSN` — live on the **Zeabur** services (secret store),
not in GitHub. The Telegram session material lives only on the worker volume.

Repo settings for fully hands-off merges:
* Settings → General → **Allow auto-merge** = on.
* Settings → Actions → **Workflow permissions**: allow Actions to create/approve,
  and disable "require approval for all outside collaborators" so agent PRs run
  without a manual "approve workflows" click.
* Branch protection on `master`: require the CI checks; do not require a human
  reviewer (or wire an automated approver) if merges must complete with no human.

Until a provider's secrets are added, its deploy leg is simply skipped — the
pipeline stays green and the rest still deploys.
