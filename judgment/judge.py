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


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _finite_unit(x) -> bool:
    return _number(x) and 0 <= x <= 1


def _probabilities(qid: str, probs) -> dict[str, float]:
    if probs is None:
        return {}
    if not isinstance(probs, dict) or not all(isinstance(k, str) and _finite_unit(v) for k, v in probs.items()):
        raise InvalidAnswers(f"{qid}: probabilities must map names to numbers in [0, 1], got {probs!r}")
    if probs and abs(sum(probs.values()) - 1) > 0.02:
        raise InvalidAnswers(f"{qid}: probabilities sum to {sum(probs.values())}")
    return {k: float(v) for k, v in probs.items()}


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
        probs = _probabilities(qid, a.get("probabilities"))
        if q["type"] == "choice":
            choice = a.get("choice")
            if not isinstance(choice, str) or choice not in q["criteria"]:
                raise InvalidAnswers(f"{qid}: {choice!r} is not an option")
            if probs and (set(probs) - set(q["criteria"]) or probs.get(choice, 0) < max(probs.values())):
                raise InvalidAnswers(f"{qid}: choice {choice!r} disagrees with probabilities {probs}")
            value: str | float = choice
        else:
            legend = a.get("legend")
            if not isinstance(legend, dict):
                raise InvalidAnswers(f"{qid}: legend {legend!r} is not a mapping")
            try:
                keyed = sorted((float(k), text) for k, text in legend.items())
            except (TypeError, ValueError) as e:
                raise InvalidAnswers(f"{qid}: unreadable legend {legend!r}") from e
            levels = [k for k, _ in keyed]
            if len(set(levels)) != len(q["criteria"]) or not all(math.isfinite(k) for k in levels):
                raise InvalidAnswers(f"{qid}: legend {legend!r} doesn't have {len(q['criteria'])} distinct levels")
            # If the legend echoes our level texts, they must be in our order, or the scale would be inverted.
            texts = [text for _, text in keyed]
            if all(t in q["criteria"] for t in texts) and texts != list(q["criteria"]):
                raise InvalidAnswers(f"{qid}: legend levels are not in the order they were declared")
            score = a.get("score")
            if not _number(score):
                raise InvalidAnswers(f"{qid}: score {score!r} is not a number")
            lo, hi = levels[0], levels[-1]
            if not lo <= score <= hi:
                raise InvalidAnswers(f"{qid}: score {score} outside {lo}..{hi}")
            value = (score - lo) / (hi - lo)
        out[qid] = Answer(qid, q["type"], value, float(conf), probs)
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
    except Exception as e:  # noqa: BLE001 - judgment must not take the pipeline down; rule 15 escalates instead
        return _unavailable({}, [f"facts: {type(e).__name__}: {e}"])
    threshold = state.contract.get("escalation_confidence_threshold")
    errors: list[str] = []
    sources = [("jev", ask_jev)] + ([("claude", ask_fallback)] if use_fallback else [])
    for source, ask in sources:
        try:
            answers = validate(ask(facts, QUESTIONS))
        except (jev.JevError, fallback.FallbackError, InvalidAnswers) as e:
            errors.append(f"{source}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 - an unexpected failure is still a failed source, never a crash
            errors.append(f"{source}: unexpected {type(e).__name__}: {e}")
            continue
        proposal = Proposal(
            action=Action(answers[ACTION].value),
            confidences={qid: a.confidence for qid, a in answers.items()},
            reasons=reasons_for(answers, threshold, source),
            answers={qid: a.value for qid, a in answers.items()},
        )
        return JudgmentResult(proposal, source, answers, facts, errors)
    return _unavailable(facts, errors)
