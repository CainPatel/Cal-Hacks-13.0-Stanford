"""Loads config/contract.yaml: the editable permissions contract. Pure I/O-free parsing
of an already-read string, plus one convenience loader for the default path.
"""
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "contract.yaml"


def load_contract(path: Path = DEFAULT_PATH) -> dict:
    return yaml.safe_load(path.read_text())
