.PHONY: seed test fuzz events replay api synthetic demo benchmark device device-test
PY = .venv/bin/python
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
