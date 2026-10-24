# Draw dashboard

Next.js (App Router) + Tailwind + shadcn/ui over the FastAPI in `api/`.

```
make demo            # from the repo root: fresh data + API on :8000
make web             # dashboard on http://localhost:3000
```

`NEXT_PUBLIC_API_URL` points the dashboard at the API (default `http://localhost:8000`). The API must allow
this origin: set `CORS_ORIGINS` on the API (default `http://localhost:3000`).

Views: **Pay requests** (`/`, `?m=<month>&r=<request id>`), **Approve** (`/approve`), **Security** (`/security`).
Read `AGENTS.md` before changing Next.js code: this is Next 16.
