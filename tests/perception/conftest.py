from types import SimpleNamespace

import pytest


class FakeClaude:
    """Stands in for anthropic.Anthropic: returns canned structured output and records calls."""

    def __init__(self, parsed=None, stop_reason="end_turn"):
        self.calls = []
        self._parsed = parsed
        self._stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self._stop_reason, parsed_output=self._parsed, _request_id="req_test")


@pytest.fixture
def fake_claude():
    return FakeClaude
