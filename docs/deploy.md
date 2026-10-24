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
3. Vercel adds `BLOB_STORE_ID`, `BLOB_READ_WRITE_TOKEN` and the OIDC token Draw's SDK uses automatically.
4. **Also connect the same store to `draw-web`** (store → Projects → Connect Project). The dashboard's
   Upload screen sends files straight from the browser to Blob (client uploads, so site video isn't
   capped at 4.5 MB); its token route needs `BLOB_READ_WRITE_TOKEN`, which the connection adds.

## 2. Environment variables

`draw-api` → Settings → Environment Variables (Production and Preview):

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from whoever holds it (GitHub secrets can't be read back) |
| `TYPESAFE_API_KEY` | the Jev key from TypeSafe (`apikey_...`). Goes with `JEV_BASE_URL` below |
| `JEV_BASE_URL` | `https://api.typesafe.ai` for a TypeSafe key. Leave unset only if the key is a Vercel AI Gateway key, which then goes in `JEV_API_KEY` / `AI_GATEWAY_API_KEY` |
| `NESSIE_API_KEY` | same |
| `NESSIE_ACCOUNT_ID` | the GC's account in the Nessie sandbox: run `python -m events nessie-setup` once from a laptop with `NESSIE_API_KEY` in `.env`; it prints the id. Without it, payments are refused |
| `CRS_USERNAME`, `CRS_PASSWORD` | the CRS sandbox login from CRS's email (rule 09's vendor check). Mistyped credentials are tried once and not retried: repeated failed logins lock the CRS account |
| `CORS_ORIGINS` | the `draw-web` URL, e.g. `https://draw-web.vercel.app` (comma-separate several) |
| `DEVICE_URL` | the Pi's Tailscale Funnel URL (step 4) |
| `SCORECARD_CASES` | optional, default `2000`: attacks scored on a cold start |

`draw-web` → Settings → Environment Variables:

| Name | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | the `draw-api` URL, e.g. `https://draw-api.vercel.app` |
| `UPLOAD_PASSCODE` | a passcode the team shares by DM. The Upload screen asks for it; without it set, deployed uploads stay off (the dashboard has no logins, so this keeps strangers out of the Blob store) |

Redeploy both after adding variables (`NEXT_PUBLIC_*` is baked in at build time).

## 3. Load the demo company into Neon (once)

After Neon is connected and `draw-api` redeployed:

```
curl -X POST https://drawapi.vercel.app/setup/demo
```

It loads 419 facts and decides May-October (90 decisions), on Vercel itself, so the database URL never
leaves Vercel. It only works on an empty log (409 otherwise), so it can't overwrite real data. To start
over, drop the table in the Neon console (`DROP TABLE events;`); the log refuses UPDATE, DELETE and
TRUNCATE by trigger, on purpose.

When connecting Neon, set the **Custom Prefix to `DATABASE`** so the variable is `DATABASE_URL` (Draw
reads that name), and untick Preview under "Create database branch for deployment".

## 4. The approval key (Pi)

Vercel can't reach your LAN. On the Pi: `sudo tailscale funnel 8000` prints a public
`https://<name>.<tailnet>.ts.net` URL; set it as `DEVICE_URL` on `draw-api`. The URL is public,
but money moves only on a physical button press plus a signature the executor verifies.

## Limits to know

- **Function duration**: 300 s (Hobby and Pro default). Approve waits up to ~90 s for the button: fine.
  Live updates (`/events`) reconnect every 300 s on their own.
- **Request body**: 4.5 MB per request to a function. The Upload screen avoids it: files go from the
  browser straight to Blob, then `POST /ingest` reads them from the store. Video up to 500 MB.
- **Cold starts** score a fresh 2,000-attack scorecard (~1-3 s) once per instance.
- **Fallback**: if venue Wi-Fi or Tailscale misbehaves, `make demo` + `make web` on the laptop is
  the same app with SQLite and needs no internet except for model calls.
