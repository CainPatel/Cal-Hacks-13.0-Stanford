# Deploying Draw on Vercel (Neon Postgres + private Blob)

Two Vercel projects from this repo:

| Project | Root Directory | What it runs |
|---|---|---|
| `draw-web` | `web` | the Next.js dashboard |
| `draw-api` | repo root | the FastAPI backend (`app.py` → `api.main:app`, `vercel.json`) |

## 1. Storage (on `draw-api`)

**Neon Postgres** (the event log)
1. `draw-api` → **Storage** → **Create Database** → **Neon** → create → **Connect** to `draw-api` (all environments).
2. Vercel adds `DATABASE_URL` (and related `POSTGRES_*`/`PG*` variables) to the project. Draw reads only `DATABASE_URL`.

**Blob** (archive of uploaded originals)
1. `draw-api` → **Storage** → **Create** → **Blob** → **Continue** → set access to **Private** → create → **Connect** to `draw-api`.
2. **It must be Private**: pay apps carry bank details. A public store would give every upload a public URL.
3. Vercel adds `BLOB_STORE_ID` (and the OIDC token Draw's SDK uses automatically). Locally, `vercel env pull` also gives a `BLOB_READ_WRITE_TOKEN`.

## 2. Environment variables

`draw-api` → Settings → Environment Variables (Production and Preview):

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from whoever holds it (GitHub secrets can't be read back) |
| `JEV_API_KEY` | same |
| `NESSIE_API_KEY` | same |
| `CORS_ORIGINS` | the `draw-web` URL, e.g. `https://draw-web.vercel.app` (comma-separate several) |
| `DEVICE_URL` | the Pi's Tailscale Funnel URL (step 4) |
| `SCORECARD_CASES` | optional, default `2000`: attacks scored on a cold start |

`draw-web` → Settings → Environment Variables:

| Name | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | the `draw-api` URL, e.g. `https://draw-api.vercel.app` |

Redeploy both after adding variables (`NEXT_PUBLIC_*` is baked in at build time).

## 3. Load the demo company into Neon (once)

From your laptop, in the repo:

```
npm i -g vercel
vercel link                 # pick the draw-api project
vercel env pull .env.local  # gitignored; contains DATABASE_URL
set -a; . ./.env.local; set +a
.venv/bin/python -m events load       # 419 facts; refuses if the log already has events
.venv/bin/python -m events replay 6   # decide May-October (90 decisions)
```

To start over, drop the table in the Neon console (`DROP TABLE events;`). The log refuses
UPDATE, DELETE and TRUNCATE by trigger, on purpose.

## 4. The approval key (Pi)

Vercel can't reach your LAN. On the Pi: `sudo tailscale funnel 8000` prints a public
`https://<name>.<tailnet>.ts.net` URL; set it as `DEVICE_URL` on `draw-api`. The URL is public,
but money moves only on a physical button press plus a signature the executor verifies.

## Limits to know

- **Function duration**: 300 s (Hobby and Pro default). Approve waits up to ~90 s for the button: fine.
  Live updates (`/events`) reconnect every 300 s on their own.
- **Request body**: 4.5 MB per request. Pay apps, releases, COIs and emails fit. **Site videos
  don't**: upload video from the laptop setup (`make demo` + `POST /site-video`), not the deployed API.
- **Cold starts** score a fresh 2,000-attack scorecard (~1-3 s) once per instance.
- **Fallback**: if venue Wi-Fi or Tailscale misbehaves, `make demo` + `make web` on the laptop is
  the same app with SQLite and needs no internet except for model calls.
