"""judge(state, request) -> JudgmentResult holding a Proposal.

Facts are computed in code, the five questions go to Jev (Claude if Jev is
unreachable), every answer is validated, and the result becomes a Proposal.
If neither model gives a valid answer, the Proposal is an escalation with zero
confidence everywhere, so rule 15 sends it to a person; judgment never blocks
the pipeline or invents certainty.
"""

import math
from dataclasses import dataclass, field

from judgment import fallback, jev
from judgment.facts import compute_facts
from judgment.questions import ACTION, BANK, DUPLICATE, LABELS, PROGRESS, QUESTIONS, RELEASE
from shared.schema import Action, PayRequest, Proposal


class InvalidAnswers(ValueError):
    pass


@dataclass(frozen=True)
class Answer:
    question: str
    type: str
    value: str | float  # choice name, or score normalized to 0-1
    confidence: float
    probabilities: dict[str, float]


@dataclass
class JudgmentResult:
    proposal: Proposal
    source: str  # "jev", "claude", or "unavailable"
    answers: dict[str, Answer] = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _finite_unit(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 <= x <= 1


def validate(raw: dict, questions: dict = QUESTIONS) -> dict[str, Answer]:
    """Check every answer against its question; normalize Score answers to 0-1."""
    missing = set(questions) - set(raw)
    if missing:
        raise InvalidAnswers(f"missing answers: {sorted(missing)}")
    out = {}
    for qid, q in questions.items():
        a = raw[qid]
        if not isinstance(a, dict) or a.get("type") != q["type"]:
            raise InvalidAnswers(f"{qid}: expected a {q['type']} answer, got {a!r}")
        conf = a.get("confidence")
        if not _finite_unit(conf):
            raise InvalidAnswers(f"{qid}: confidence {conf!r} is not in [0, 1]")
        probs = a.get("probabilities") or {}
        if q["type"] == "choice":
            if a.get("choice") not in q["criteria"]:
                raise InvalidAnswers(f"{qid}: {a.get('choice')!r} is not an option")
            value: str | float = a["choice"]
        else:
            legend = a.get("legend") or {}
            try:
                levels = sorted(float(k) for k in legend)
            except (TypeError, ValueError) as e:
                raise InvalidAnswers(f"{qid}: unreadable legend {legend!r}") from e
            score = a.get("score")
            if len(levels) != len(q["criteria"]) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise InvalidAnswers(f"{qid}: bad score {score!r} for legend {legend!r}")
            lo, hi = levels[0], levels[-1]
            if not lo <= score <= hi:
                raise InvalidAnswers(f"{qid}: score {score} outside {lo}..{hi}")
            value = (score - lo) / (hi - lo)
        out[qid] = Answer(qid, q["type"], value, float(conf), {str(k): float(v) for k, v in probs.items()})
    return out


_CHOICE_TEXT = {
    RELEASE: {
        "correct": "The lien release on file is the right form for this payment.",
        "wrong_type": "The lien release on file is the wrong kind for this payment.",
        "incomplete": "The lien release is missing, unsigned, or doesn't cover this period.",
    },
    PROGRESS: {
        "plausible": "The progress claimed looks consistent with the site video and last month.",
        "overstated": "The sub seems to be claiming more work than the site evidence shows.",
        "understated": "The sub is claiming less work than the site evidence shows.",
    },
    ACTION: {
        "release": "Suggested: pay it.",
        "hold": "Suggested: hold it until the sub fixes something.",
        "escalate": "Suggested: get the owner's sign-off before paying.",
    },
}


def _score_text(qid: str, value: float) -> str:
    levels = QUESTIONS[qid]["criteria"]
    nearest = levels[round(value * (len(levels) - 1))]
    return f"{LABELS[qid]}: {nearest[0].lower()}{nearest[1:]}"


def reasons_for(answers: dict[str, Answer], threshold: float | None, source: str) -> list[str]:
    reasons = []
    for qid in (RELEASE, BANK, PROGRESS, DUPLICATE, ACTION):
        a = answers[qid]
        text = _CHOICE_TEXT[qid][a.value] if a.type == "choice" else _score_text(qid, a.value)
        if threshold is not None and a.confidence < threshold:
            text += " (Not sure about this one; a person should check.)"
        reasons.append(text)
    if source == "claude":
        reasons.append("Reviewed by the backup model because Jev was unavailable.")
    return reasons


def _unavailable(facts: dict, errors: list[str]) -> JudgmentResult:
    proposal = Proposal(
        action=Action.escalate,
        confidences={qid: 0.0 for qid in QUESTIONS},
        reasons=["The automatic review couldn't run, so a person needs to look at this one."],
    )
    return JudgmentResult(proposal, "unavailable", {}, facts, errors)


def judge(state, request: PayRequest, *, ask_jev=jev.ask, ask_fallback=fallback.ask, use_fallback: bool = True) -> JudgmentResult:
    try:
        facts = compute_facts(state, request)
    except ValueError as e:
        return _unavailable({}, [f"facts: {e}"])
    threshold = state.contract.get("escalation_confidence_threshold")
    errors: list[str] = []
    sources = [("jev", ask_jev)] + ([("claude", ask_fallback)] if use_fallback else [])
    for source, ask in sources:
        try:
            answers = validate(ask(facts, QUESTIONS))
        except (jev.JevError, fallback.FallbackError, InvalidAnswers) as e:
            errors.append(f"{source}: {e}")
            continue
        proposal = Proposal(
            action=Action(answers[ACTION].value),
            confidences={qid: a.confidence for qid, a in answers.items()},
            reasons=reasons_for(answers, threshold, source),
        )
        return JudgmentResult(proposal, source, answers, facts, errors)
    return _unavailable(facts, errors)
