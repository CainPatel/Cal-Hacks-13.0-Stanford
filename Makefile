.PHONY: seed test
PY = .venv/bin/python

seed:
	$(PY) -m seed.generate

test:
	$(PY) -m pytest -q tests
