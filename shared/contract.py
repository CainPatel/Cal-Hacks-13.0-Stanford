"""Loads config/contract.yaml: the editable permissions contract. Lives outside kernel/
because kernel/ is pure functions with no I/O (CLAUDE.md hard rule #1) -- callers
(pipeline.py, api/main.py) read the file and pass the resulting dict into KernelState.
"""
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "contract.yaml"


def load_contract(path: Path = DEFAULT_PATH) -> dict:
    return yaml.safe_load(path.read_text())
