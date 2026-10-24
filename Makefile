.PHONY: seed test fuzz events replay
PY = .venv/bin/python
N ?= 500
M ?= 6

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
