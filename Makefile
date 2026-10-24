.PHONY: seed test replay
PY = .venv/bin/python

seed:
	$(PY) -m seed.generate

test:
	$(PY) -m pytest -q tests

replay:
	$(PY) -m events.replay $(if $(M),--month $(M),)
