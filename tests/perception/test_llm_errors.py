"""What a person sees when Claude's structured answer doesn't parse: which kind of failure, never the content."""
from types import SimpleNamespace

import pydantic
import pytest

from perception import llm


def _client_raising(raw: str):
    def parse(**kwargs):
        llm.G703Transcription.model_validate_json(raw)  # what the SDK does inside .parse()
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))


def test_cut_off_answer_says_so():
    with pytest.raises(llm.PerceptionLLMError, match="cut off"):
        llm.transcribe_g703(b"%PDF-1.4", client=_client_raising('{"is_g703": true, "rows": [{"item_no": "1"'))


def test_wrong_shape_names_the_field_not_the_value():
    with pytest.raises(llm.PerceptionLLMError) as e:
        llm.transcribe_g703(b"%PDF-1.4", client=_client_raising('{"is_g703": "secret-value"}'))
    assert "is_g703" in str(e.value) and "secret-value" not in str(e.value)
