.PHONY: seed test fuzz events replay api synthetic demo benchmark keys hooks device device-test
PY = .venv/bin/python

# Keys from .env (gitignored) reach every command below; missing .env is fine.
-include .env
export
N ?= 500
M ?= 6
W ?= 0
PORT ?= 8000

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

synthetic:
	$(PY) -m synthetic --worlds $(W)

benchmark:
	$(PY) -m synthetic.benchmark

# One-command demo reset: archive the old log (never delete it), load the seed,
# decide May-October, score 5,000 attacks, serve the API.
demo:
	@ts=$$(date +%Y%m%d-%H%M%S); for f in data/events.db*; do [ -e "$$f" ] && mv "$$f" "data/archive-$$ts-$${f#data/}"; done; true
	$(PY) -m events load
	$(PY) -m events replay 6
	$(PY) -m fuzzer.run 5000
	$(PY) -m uvicorn api.main:app --port $(PORT)

# Which secrets are set (never prints a value)
keys:
	@for k in ANTHROPIC_API_KEY JEV_API_KEY AI_GATEWAY_API_KEY NESSIE_API_KEY NESSIE_ACCOUNT_ID CRS_USERNAME CRS_PASSWORD CRS_API_KEY ESP32_DEVICE_URL APPROVAL_KEY_PUBKEY; do \
		if [ -n "$$(printenv $$k)" ]; then echo "  $$k: set"; else echo "  $$k: missing"; fi; done

# Turn on the repo's secret-blocking pre-commit hook for this clone (run once per laptop)
hooks:
	git config core.hooksPath .githooks
	@echo "pre-commit secret check enabled"
