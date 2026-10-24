.PHONY: seed test fuzz device device-test
PY = .venv/bin/python
N ?= 500

seed:
	$(PY) -m seed

test:
	$(PY) -m pytest -q tests

fuzz:
	$(PY) -m fuzzer.run $(N)

device:
	DRAWKEY_MOCK=1 $(PY) -m uvicorn device.main:app --port 8000

device-test:
	$(PY) -m pytest -q device/test_device.py
