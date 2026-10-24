"""Vercel's entrypoint for the Draw API (it looks for `app` in app.py). Locally, `make api` runs
api.main:app directly; this file only re-exports it."""
from api.main import app  # noqa: F401
