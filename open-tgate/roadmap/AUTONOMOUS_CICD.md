# Autonomous CI/CD

Open-TGate ships itself. After a **one-time** secret setup, every change flows to
production with no human clicks and no manual commands.

## The loop

```
push / PR ──▶ Open-TGate CI (lint, tests, Docker build, Worker bundle, CodeQL)
   │                       │ all required checks green
   │  label: auto-merge    ▼
   └────────────▶ Auto-merge (native, squash)  ──▶ master/main
                                                     │
              ┌──────────────────────────────────────┴───────────────┐
              ▼                                                       ▼
  Deploy (push: frontend + data)                     release-please ─▶ vX.Y.Z tag
    ├─ migrate            Supabase migrations           │
    ├─ deploy-cloudflare  /app + landing Worker         ▼
    └─ healthcheck        probe deployed pages     release-dockerhub
                                                     └─ versioned api+worker images
                                                        ─▶ Zeabur (registry auto-deploy)
```

* **Testing** — `.github/workflows/open-tgate-ci.yml` (unchanged) runs lint,
  32 unit tests, API + worker Docker smoke, Worker bundle, and CodeQL on every
  PR and push.
* **Auto-merge** — `.github/workflows/auto-merge.yml`: a PR labeled `auto-merge`
  (non-draft) has GitHub native auto-merge enabled and merges itself once
  required checks pass; removing the label disables it. Tests always gate merge.
* **Deploy (push)** — `.github/workflows/deploy.yml` on push to `master`/`main`
  ships the parts that build directly from source: Supabase migrations and the
  Cloudflare Worker (frontend). Each leg activates **iff its credentials are
  present** and is skipped (never false-green) otherwise; the frontend deploys
  only after migrations succeed, and the health check strictly gates on the
  pages it just published.
* **Deploy (backend)** — the api + worker run as **immutable, versioned Docker
  images**. `release-please` cuts a version PR (label it `auto-merge`); the tag
  triggers `release-dockerhub.yml`, which publishes the versioned images; Zeabur
  redeploys them via its Docker Hub registry auto-deploy. This keeps a push-time
  job from redeploying a stale pinned image and calling it green.

## One-time setup (then hands-off)

Add these **repository secrets** (Settings → Secrets and variables → Actions).
Names only — never commit values.

| Secret | Purpose |
|---|---|
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | publish the dashboard Worker (push deploy) |
| `SUPABASE_DB_URL` | apply migrations (`supabase db push`, push deploy) |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | publish versioned backend images (release path) |

Optional repo **variable**: `PUBLIC_DASHBOARD_URL` (default `open-tgate.site`).

**Zeabur** backend deploy is configured once in the Zeabur dashboard: point the
api + worker services at the Docker Hub image tag and enable redeploy-on-new-image
so a freshly published version rolls out with no human step.

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
