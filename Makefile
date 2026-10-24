.PHONY: seed test fuzz events replay api web synthetic demo demo-online snapshot offline sync smoke benchmark keys hooks device device-test
PY = .venv/bin/python

# Keys from .env (gitignored) reach every command below; missing .env is fine. With DATABASE_URL in
# .env, events/replay/api/sync work on the shared Neon log (the deployment's); without it, data/events.db.
-include .env
export
N ?= 500
M ?= 6
W ?= 0
PORT ?= 8000
API ?= http://localhost:8000
WEB ?= http://localhost:3000

seed:
	$(PY) -m seed

test:
	$(PY) -m pytest -q tests

fuzz:
	$(PY) -m fuzzer.run $(N)

device:
	DRAWKEY_MOCK=1 $(PY) -m uvicorn device.main:app --port 8001

device-test:
	$(PY) -m pytest -q device/test_device.py

events:
	$(PY) -m events load

replay:
	$(PY) -m events replay $(M)

api:
	$(PY) -m uvicorn api.main:app --reload --port $(PORT)

# Dashboard on :3000; NEXT_PUBLIC_API_URL points it at the API (default http://localhost:8000)
web:
	cd web && npm install && npm run dev

synthetic:
	$(PY) -m synthetic --worlds $(W)

benchmark:
	$(PY) -m synthetic.benchmark

# One-command local demo reset: archive the old local log (never delete it), load the seed,
# decide May-October, score 5,000 attacks, serve the API. Always the local SQLite log, never Neon.
demo: export DATABASE_URL =
demo:
	@ts=$$(date +%Y%m%d-%H%M%S); for f in data/events.db*; do [ -e "$$f" ] && mv "$$f" "data/archive-$$ts-$${f#data/}"; done; true
	$(PY) -m events load
	$(PY) -m events replay 6
	$(PY) -m fuzzer.run 5000
	$(PY) -m uvicorn api.main:app --port $(PORT)

# Start the shared Neon log over with the demo company: set the current log aside (renamed, never
# deleted), load the seed, copy the bank's ledger in, and decide May-October with Jev in the loop.
demo-online:
	@[ -n "$$DATABASE_URL" ] || { echo "Put the Neon DATABASE_URL in .env first (docs/deploy.md, step 5)."; exit 1; }
	$(PY) -m events archive --yes || true
	$(PY) -m events load
	$(PY) -m events nessie-sync
	$(PY) -m events replay 6 --judge

# Copy the bank's ledger (owner draws, payments made anywhere) into the log
sync:
	$(PY) -m events nessie-sync

# Offline fallback: copy the shared Neon log into data/events.db (the old local log is archived),
# then `make offline` serves that copy with no database connection.
snapshot:
	@[ -n "$$DATABASE_URL" ] || { echo "DATABASE_URL isn't set in .env; the local log is already data/events.db."; exit 1; }
	@ts=$$(date +%Y%m%d-%H%M%S); for f in data/events.db*; do [ -e "$$f" ] && mv "$$f" "data/archive-$$ts-$${f#data/}"; done; true
	$(PY) -m events snapshot

offline: export DATABASE_URL =
offline:
	$(PY) -m uvicorn api.main:app --reload --port $(PORT)

# Check every stage of a running Draw over HTTP, local or deployed (API=, WEB=). Read-only unless
# FLAGS adds --sync (bank sync + replay), --pay (one sandbox payment), --approve (needs a button press).
smoke:
	$(PY) scripts/smoke.py --api $(API) --web $(WEB) $(FLAGS)

# Which secrets are set (never prints a value)
keys:
	@for k in DATABASE_URL ANTHROPIC_API_KEY TYPESAFE_API_KEY JEV_API_KEY AI_GATEWAY_API_KEY NESSIE_API_KEY NESSIE_ACCOUNT_ID CRS_USERNAME CRS_PASSWORD CRS_API_KEY DEVICE_URL APPROVAL_KEY_PUBKEY; do \
		if [ -n "$$(printenv $$k)" ]; then echo "  $$k: set"; else echo "  $$k: missing"; fi; done

# Turn on the repo's secret-blocking pre-commit hook for this clone (run once per laptop)
hooks:
	git config core.hooksPath .githooks
	@echo "pre-commit secret check enabled"
