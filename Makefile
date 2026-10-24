.PHONY: seed test fuzz
PY = .venv/bin/python
N ?= 500

seed:
	$(PY) -m seed

test:
	$(PY) -m pytest -q tests

fuzz:
	$(PY) -m fuzzer.run $(N)
