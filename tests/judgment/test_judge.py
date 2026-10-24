import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from judgment import fallback, jev
from judgment.judge import InvalidAnswers, judge, validate
from judgment.questions import ACTION, BANK, DUPLICATE, PROGRESS, QUESTIONS, RELEASE
from seed.anomalies import THIS_MONTH
from shared.schema import Action
from tests.judgment.conftest import state_for


def jev_answers(action="release", conf=0.95, legend_base=1, **override):
    legend = {str(legend_base + i): text for i, text in enumerate(QUESTIONS[BANK]["criteria"])}
    answers = {
        RELEASE: {"type": "choice", "choice": "correct", "confidence": conf,
                  "probabilities": {"correct": conf, "wrong_type": round(1 - conf, 6), "incomplete": 0.0}},
        BANK: {"type": "score", "score": legend_base + 0.2, "legend": legend, "probabilities": {}, "confidence": conf},
        PROGRESS: {"type": "choice", "choice": "plausible", "probabilities": {}, "confidence": conf},
        DUPLICATE: {"type": "score", "score": legend_base, "legend": legend, "probabilities": {}, "confidence": conf},
        ACTION: {"type": "choice", "choice": action, "probabilities": {}, "confidence": conf},
    }
    answers.update(override)
    return answers


@pytest.fixture
def clean(seed):
    req = next(r for r in seed["pay_requests"] if r.number == THIS_MONTH)
    return state_for(seed, req.id)


def boom(*a, **k):
    raise jev.JevError("down")


def test_jev_answers_become_a_proposal(clean):
    seen = {}

    def ask(state, questions):
        seen.update(state=state, questions=questions)
        return jev_answers()

    r = judge(*clean, ask_jev=ask, ask_fallback=boom)
    assert r.source == "jev" and r.errors == []
    assert r.proposal.action == Action.release
    assert r.proposal.confidences == {q: 0.95 for q in QUESTIONS}
    assert len(r.proposal.reasons) == 5 and not any("Not sure" in x for x in r.proposal.reasons)
    assert len(seen["questions"]) <= 8  # Jev's per-request limit
    assert len(json.dumps({"state": seen["state"], "questions": seen["questions"]}).encode()) <= 32 * 1024


@pytest.mark.parametrize("base", [0, 1])
def test_score_normalized_whatever_the_legend_numbering(base):
    answers = validate(jev_answers(legend_base=base))
    assert answers[BANK].value == pytest.approx(0.05)
    assert answers[DUPLICATE].value == 0


def test_low_confidence_is_called_out(clean):
    r = judge(*clean, ask_jev=lambda s, q: jev_answers(conf=0.6), ask_fallback=boom)
    assert all("Not sure" in x for x in r.proposal.reasons)


def test_fallback_used_when_jev_is_down(clean):
    r = judge(*clean, ask_jev=boom, ask_fallback=lambda s, q: jev_answers(action="hold"))
    assert r.source == "claude" and r.proposal.action == Action.hold
    assert "backup model" in r.proposal.reasons[-1]
    assert r.errors == ["jev: down"]


@pytest.mark.parametrize(
    "bad",
    [
        {ACTION: {"type": "choice", "choice": "pay_double", "probabilities": {}, "confidence": 0.9}},
        {RELEASE: {"type": "choice", "choice": "correct", "probabilities": {}, "confidence": 1.5}},
        {RELEASE: {"type": "choice", "choice": "correct", "probabilities": {}, "confidence": float("nan")}},
        {RELEASE: {"type": "score", "score": 1, "legend": {}, "probabilities": {}, "confidence": 0.9}},
        {BANK: {"type": "score", "score": 9, "legend": {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"},
                "probabilities": {}, "confidence": 0.9}},
    ],
)
def test_invalid_jev_answer_falls_back(clean, bad):
    r = judge(*clean, ask_jev=lambda s, q: jev_answers(**bad), ask_fallback=lambda s, q: jev_answers())
    assert r.source == "claude" and "jev" in r.errors[0]


def test_missing_answer_is_invalid():
    answers = jev_answers()
    del answers[PROGRESS]
    with pytest.raises(InvalidAnswers, match="missing"):
        validate(answers)


def test_both_down_escalates_with_zero_confidence(clean):
    def also_boom(*a, **k):
        raise fallback.FallbackError("no")

    r = judge(*clean, ask_jev=boom, ask_fallback=also_boom)
    assert r.source == "unavailable" and r.proposal.action == Action.escalate
    assert set(r.proposal.confidences.values()) == {0.0}
    assert len(r.errors) == 2


# --- Jev HTTP client ---------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(*bodies):
    calls = []

    def opener(request, timeout):
        calls.append(request)
        body = bodies[len(calls) - 1]
        if isinstance(body, Exception):
            raise body
        return FakeResponse(json.dumps(body).encode())

    opener.calls = calls
    return opener


def http_error(code):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b'{"message": "x"}'))


@pytest.fixture
def gateway_key(monkeypatch):
    monkeypatch.delenv("JEV_BASE_URL", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")


def test_gateway_request_and_envelope(gateway_key):
    opener = opener_returning({"model": "typesafe-ai/jev", "answers": {"q": {"type": "noul", "noul": 0.9}}})
    assert jev.ask({"s": 1}, {"q": {}}, opener=opener) == {"q": {"type": "noul", "noul": 0.9}}
    req = opener.calls[0]
    assert req.full_url == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    assert req.get_header("Authorization") == "Bearer test-key"
    assert json.loads(req.data)["model"] == "typesafe-ai/jev"


def test_direct_api_envelope(monkeypatch):
    monkeypatch.setenv("JEV_BASE_URL", "https://thejevai.com")
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    body = {"code": 0, "message": "ok", "data": {"result": {"answers": {"q": {"type": "noul", "noul": 0.1}}}}}
    opener = opener_returning(body)
    assert jev.ask({}, {}, opener=opener)["q"]["noul"] == 0.1
    assert opener.calls[0].full_url == "https://thejevai.com/v1/systemone"


def test_retries_overload_then_succeeds(gateway_key):
    sleeps = []
    opener = opener_returning(http_error(529), http_error(429), {"answers": {}})
    assert jev.ask({}, {}, opener=opener, sleep=sleeps.append) == {}
    assert sleeps == [1, 2]


def test_auth_error_not_retried(gateway_key):
    opener = opener_returning(http_error(401))
    with pytest.raises(jev.JevError, match="401"):
        jev.ask({}, {}, opener=opener, sleep=lambda s: None)
    assert len(opener.calls) == 1


def test_no_key_is_a_jev_error(monkeypatch):
    monkeypatch.delenv("JEV_BASE_URL", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(jev.JevError, match="no Jev API key"):
        jev.ask({}, {})


def test_unexpected_envelope_is_a_jev_error(gateway_key):
    with pytest.raises(jev.JevError, match="unexpected"):
        jev.ask({}, {}, opener=opener_returning({"code": 7, "message": "nope"}))


# --- Claude fallback ---------------------------------------------------------


class FakeClaude:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))
        self._parsed, self._stop = parsed, stop_reason

    def _parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self._stop, parsed_output=self._parsed, _request_id="req_test")


def claude_answers(**probs):
    default = {
        RELEASE: {"correct": 0.9, "wrong_type": 0.05, "incomplete": 0.05},
        BANK: {"0": 0.7, "1": 0.2, "2": 0.1, "3": 0.0, "4": 0.0},
        PROGRESS: {"plausible": 0.8, "overstated": 0.15, "understated": 0.05},
        DUPLICATE: {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0},
        ACTION: {"release": 0.6, "hold": 0.3, "escalate": 0.1},
    }
    default.update(probs)
    return fallback.FallbackAnswers(answers=[
        fallback.QuestionAnswer(question_id=q, probabilities=[fallback.OptionProbability(option=k, probability=v)
                                                              for k, v in p.items()])
        for q, p in default.items()
    ])


def test_fallback_answers_match_jev_shape():
    out = fallback.ask({"s": 1}, QUESTIONS, client=FakeClaude(claude_answers()))
    answers = validate(out)
    assert answers[RELEASE].value == "correct" and answers[RELEASE].confidence == pytest.approx(0.9)
    # expectation over levels computed in code: 0*0.7 + 1*0.2 + 2*0.1 = 0.4 of 4
    assert answers[BANK].value == pytest.approx(0.1)
    assert answers[ACTION].value == "release" and answers[ACTION].confidence == pytest.approx(0.6)


def test_fallback_prompt_carries_state_and_options():
    client = FakeClaude(claude_answers())
    fallback.ask({"bank": {"account_changed": True}}, QUESTIONS, client=client)
    body = json.loads(client.calls[0]["messages"][0]["content"])
    assert body["state"] == {"bank": {"account_changed": True}}
    assert set(body["questions"][BANK]["options"]) == {"0", "1", "2", "3", "4"}


def test_fallback_bad_probabilities_rejected():
    bad = {RELEASE: {"correct": 0.2, "wrong_type": 0.2, "incomplete": 0.2}}
    with pytest.raises(fallback.FallbackError, match="sum"):
        fallback.ask({}, QUESTIONS, client=FakeClaude(claude_answers(**bad)))


def test_fallback_unknown_level_rejected():
    with pytest.raises(fallback.FallbackError, match="every option"):
        fallback.ask({}, QUESTIONS, client=FakeClaude(claude_answers(**{BANK: {"7": 1.0}})))


def test_fallback_refusal_is_an_error():
    with pytest.raises(fallback.FallbackError, match="refusal"):
        fallback.ask({}, QUESTIONS, client=FakeClaude(None, stop_reason="refusal"))


def test_oversized_state_escalates_instead_of_crashing(clean, monkeypatch):
    import judgment.facts

    monkeypatch.setattr(judgment.facts, "MAX_STATE_BYTES", 10)
    r = judge(*clean, ask_jev=lambda s, q: jev_answers(), ask_fallback=boom)
    assert r.source == "unavailable" and "size limit" in r.errors[0]


def test_fallback_without_credentials_is_a_fallback_error(monkeypatch):
    import anthropic

    class NoKey:
        @property
        def beta(self):
            raise anthropic.AnthropicError("api_key must be set")

    with pytest.raises(fallback.FallbackError, match="api_key"):
        fallback.ask({}, QUESTIONS, client=NoKey())
