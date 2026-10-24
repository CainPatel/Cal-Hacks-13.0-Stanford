.PHONY: seed test
PY = .venv/bin/python

seed:
	$(PY) -m seed

test:
	$(PY) -m pytest -q tests
