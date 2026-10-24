"""Judgment in the decision path: the real kernel on the seed, with a stand-in model."""
import pytest

import pipeline
from events import log as events
from events.replay import load_seed, replay
from judgment.judge import judge as real_judge
from judgment.questions import QUESTIONS
from seed.anomalies import THIS_MONTH, with_demo
from seed.generate import generate
from shared.schema import Action

pytestmark = pytest.mark.skipif(pipeline.kernel_decide is None, reason="needs kernel/")


def answers(conf, action="release"):
    legend = {str(i): t for i, t in enumerate(QUESTIONS["bank_change_suspicious"]["criteria"])}
    return {
        "release_form_correct": {"type": "choice", "choice": "correct", "probabilities": {}, "confidence": conf},
        "bank_change_suspicious": {"type": "score", "score": 0, "legend": legend, "probabilities": {}, "confidence": conf},
        "progress_plausible": {"type": "choice", "choice": "plausible", "probabilities": {}, "confidence": conf},
        "duplicate_likelihood": {"type": "score", "score": 0, "legend": legend, "probabilities": {}, "confidence": conf},
        "proposed_action": {"type": "choice", "choice": action, "probabilities": {}, "confidence": conf},
    }


def model(conf):
    """judgment.judge with Jev answering at a fixed confidence; no network."""
    def judge(ks, req):
        return real_judge(ks, req, ask_jev=lambda s, q: answers(conf), ask_fallback=None, use_fallback=False)
    return judge


def down(ks, req):
    return real_judge(ks, req, ask_jev=_raise, use_fallback=False)


def _raise(*a):
    from judgment.jev import JevError
    raise JevError("down")


@pytest.fixture(scope="module")
def world():
    return with_demo(generate())[0]


def october(world):
    return [r.id for r in world["pay_requests"] if r.number == THIS_MONTH]


def test_confident_model_changes_no_decision(world):
    for rid in october(world):
        plain = pipeline.decide(world, rid)
        judged, result = pipeline.judge_and_decide(world, rid, judge=model(0.95))
        assert judged.action == plain.action, rid
        assert result.proposal.answers["proposed_action"] == "release"


def test_unsure_model_escalates_what_would_have_been_released(world):
    released = [rid for rid in october(world) if pipeline.decide(world, rid).action == Action.release]
    assert released
    for rid in released:
        d = pipeline.decide(world, rid, judge=model(0.6))
        assert d.action == Action.escalate, rid
        assert any(r.rule == "15_confidence_floor" and not r.passed for r in d.results)


def test_unsure_model_never_turns_a_hold_into_an_escalation(world):
    held = [rid for rid in october(world) if pipeline.decide(world, rid).action == Action.hold]
    assert held
    for rid in held:
        assert pipeline.decide(world, rid, judge=model(0.6)).action == Action.hold, rid


def test_model_outage_escalates_instead_of_releasing(world):
    for rid in october(world):
        d, result = pipeline.judge_and_decide(world, rid, judge=down)
        assert result.source == "unavailable"
        assert d.action != Action.release, rid


def test_replay_logs_each_judgment_before_its_decision(tmp_path, world):
    db = tmp_path / "events.db"
    load_seed(world, path=db)
    decisions = replay(6, path=db, judge=model(0.95))
    judged = events.read(kind="judgment", path=db)
    traces = events.read(kind="kernel_trace", path=db)
    assert len(judged) == len(traces) == len(decisions)
    first = {e.request_id: e.seq for e in reversed(judged)}
    assert all(first[t.request_id] < t.seq for t in traces)
    e = judged[0].data
    assert e["source"] == "jev" and set(e["answers"]) == set(QUESTIONS) and e["facts"]["request"]["number"]
    assert e["proposal"]["answers"]["proposed_action"] == "release"
