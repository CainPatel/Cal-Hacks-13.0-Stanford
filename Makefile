.PHONY: seed test fuzz synthetic
PY = .venv/bin/python
N ?= 500
W ?= 0

seed:
	$(PY) -m seed

test:
	$(PY) -m pytest -q tests

fuzz:
	$(PY) -m fuzzer.run $(N)

synthetic:
	$(PY) -m synthetic --worlds $(W)
