.PHONY: seed test fuzz events replay api synthetic
PY = .venv/bin/python
N ?= 500
M ?= 6
W ?= 0

seed:
	$(PY) -m seed

test:
	$(PY) -m pytest -q tests

fuzz:
	$(PY) -m fuzzer.run $(N)

events:
	$(PY) -m events load

replay:
	$(PY) -m events replay $(M)

api:
	$(PY) -m uvicorn api.main:app --reload --port 8000

synthetic:
	$(PY) -m synthetic --worlds $(W)
