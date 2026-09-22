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
              ┌──────────────────────────────────────┴───────────────┐
              ▼                                                       ▼
  Deploy (push: frontend + data)                     release-please ─▶ vX.Y.Z tag
    ├─ migrate            Supabase migrations           │
    ├─ deploy-cloudflare  /app + landing Worker         ▼
    └─ healthcheck        probe deployed pages     release-dockerhub
                                                     └─ versioned api+worker images
                                                        (also moves the major tag `1`)
                                                        ─▶ Zeabur rolls out the `1` tag
```

Production branch is **`master`** (the repo's default branch today). The
`docs/BRANCHING_AND_SECRETS.md` model migrates production to `main` later; when
that happens, update `deploy.yml`, `auto-merge.yml`, and `release-please.yml`
together. Until then everything is wired to `master`.

* **Testing** — `.github/workflows/open-tgate-ci.yml` (unchanged) runs lint,
  32 unit tests, API + worker Docker smoke, Worker bundle, and CodeQL on every
  PR and push.
* **Auto-merge** — `.github/workflows/auto-merge.yml`: on PRs into `master`, a PR
  labeled `auto-merge` (non-draft) **or** a release-please PR (labeled
  `autorelease: pending`) has GitHub native auto-merge enabled and merges itself
  once required checks pass; removing the label disables it. It refuses any base
  other than `master`, so it can't merge into an unprotected branch. Tests always
  gate the merge, and release PRs need no manual labeling.
* **Deploy (push)** — `.github/workflows/deploy.yml` on push to `master`
  ships the parts that build directly from source: Supabase migrations and the
  Cloudflare Worker (frontend). Each leg activates **iff its credentials are
  present** and is skipped (never false-green) otherwise; incomplete Cloudflare
  config (one of the two secrets) fails loudly rather than skipping. The frontend
  deploys only after migrations succeed, and the health check strictly gates on
  the pages it just published.
* **Deploy (backend)** — the api + worker run as versioned Docker images built
  by `release-dockerhub.yml` on a semver tag. That workflow also moves the major
  convenience tag (`hillstreet/open-tgate-api:1`, `…-worker:1`). For hands-off
  rollout, point the Zeabur services at the **major tag `1`** and enable
  redeploy-on-new-image: each new `1.x.y` release moves `1`, and Zeabur rolls it
  out. (A pinned immutable tag like `1.0.0` does **not** auto-roll — a new tag
  leaves the pin untouched — so strict-immutability setups must bump the Zeabur
  image reference per release, in the dashboard or via a Zeabur deploy step with
  `ZEABUR_TOKEN`.)

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
