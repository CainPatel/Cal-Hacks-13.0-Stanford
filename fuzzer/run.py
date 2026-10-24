"""Red team: drive thousands of mutated and clean pay requests through the kernel and score it. Owner: B.

    python -m fuzzer.run 500        # writes data/scorecard.json

Decisions go through pipeline.decide, the same path replay uses (see pipeline.py for
the kernel interface and the stub that stands in until A's kernel exists).
"""
import json
import random
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from pipeline import KERNEL, decide
from seed import anomalies as a
from seed.generate import generate
from shared.schema import Action, Label

SUBS = [f"sub_{i:02d}" for i in range(1, 16)]
WORLDS = 8           # distinct fake companies to mutate, so cases aren't all the same 15 requests
CLEAN_SHARE = 0.2    # fraction of cases left clean, for the false-hold rate


def random_kwargs(m, rng):
    """Random settings for a mutator, so each case is a different variant."""
    return {
        a.overbill_pct: lambda: {},
        a.retainage_slip: lambda: {"rate": rng.choice(["0", "0.05", "0.075", "0.09"])},
        a.missing_conditional_release: lambda: {"stale": rng.random() < 0.5},
        a.expired_wc: lambda: {"expiry": date(2026, 8, 1) + timedelta(days=rng.randint(0, 75))},
        a.past_sov_no_co: lambda: {"pct": rng.randint(101, 125), "unapproved_co": rng.random() < 0.3},
        a.duplicate_new_number: lambda: {"new_period": rng.random() < 0.5},
        a.bank_change_email: lambda: {"homoglyph": rng.random() < 0.5},
        a.supplier_notice_unreleased: lambda: {},
        a.draw_not_cleared: lambda: {},
    }[m]()


SINGLE = [a.overbill_pct, a.retainage_slip, a.missing_conditional_release, a.expired_wc, a.past_sov_no_co,
          a.duplicate_new_number, a.bank_change_email, a.supplier_notice_unreleased, a.draw_not_cleared,
          a.new_sub_ein_mismatch]
# Mutators that change the same October request, in an order where none undoes another
# (rebill resets retainage, so retainage_slip goes after the ones that rebill).
COMBINABLE = [a.overbill_pct, a.past_sov_no_co, a.retainage_slip, a.missing_conditional_release,
              a.expired_wc, a.bank_change_email, a.supplier_notice_unreleased, a.draw_not_cleared]


def mutate_once(world, rng):
    """One adversarial case, or None if the drawn mutation doesn't apply to the drawn target."""
    if rng.random() < 0.15:
        pair = sorted(rng.sample(COMBINABLE, 2), key=COMBINABLE.index)
        sub = rng.choice(SUBS)
        data, labels = world, []
        for m in pair:
            out = m(data, sub, rng, **random_kwargs(m, rng))
            if out is None:
                return None
            data, lab = out
            labels.append(lab)
        return data, Label(request_id=labels[-1].request_id, anomaly="+".join(l.anomaly for l in labels),
                           expected_action=Action.hold, rules=sorted({r for l in labels for r in l.rules}))
    m = rng.choice(SINGLE)
    if m is a.new_sub_ein_mismatch:
        return m(world, rng.choice(["job_1", "job_2", "job_3"]), rng)
    return m(world, rng.choice(SUBS), rng, **random_kwargs(m, rng))


def make_cases(n, rng):
    worlds = [generate(rng_seed=s) for s in range(WORLDS)]
    while n > 0:
        world = rng.choice(worlds)
        if rng.random() < CLEAN_SHARE:
            req = world["pay_requests"][a.find(world["pay_requests"], f"pr_{rng.choice(SUBS)}_{a.THIS_MONTH}")]
            yield world, Label(request_id=req.id, anomaly=None, expected_action=Action.release, rules=[])
        else:
            case = mutate_once(world, rng)
            if case is None:
                continue
            yield case
        n -= 1


ATTACKS = {m.__name__: m for m in SINGLE}  # what the judge's attack console offers


def attack(name: str, target: str, rng_seed: int | None = None, **kwargs) -> dict:
    """Run one chosen attack on one sub (or job, for new_sub_ein_mismatch) through the pipeline.

    For the attack console: returns the label (what should happen) and the decision (what did).
    kwargs override the mutator's settings, e.g. attack("bank_change_email", "sub_04", homoglyph=True).
    """
    out = ATTACKS[name](generate(), target, random.Random(rng_seed), **kwargs)
    if out is None:
        raise ValueError(f"{name} doesn't apply to {target}")
    data, label = out
    return {"label": label, "decision": decide(data, label.request_id),
            "amount": amount(data, label.request_id)}


def amount(data, request_id) -> Decimal:
    return data["pay_requests"][a.find(data["pay_requests"], request_id)].g702.current_payment_due


def rule_numbers(rules) -> set[str]:
    """'rule_07' and '07_lower_tier_released' both mean rule 07."""
    return {re.search(r"\d{2}", r).group() for r in rules}


def score(rows) -> dict:
    clean = [r for r in rows if r["anomaly"] is None]
    bad = [r for r in rows if r["anomaly"] is not None]
    by_type = defaultdict(lambda: {"n": 0, "caught": 0, "wrongful": 0, "right_rule": 0})
    for r in bad:
        t = by_type[r["anomaly"]]
        t["n"] += 1
        t["caught"] += r["action"] != "release"
        t["wrongful"] += r["action"] == "release"
        t["right_rule"] += rule_numbers(r["expected_rules"]) <= rule_numbers(r["failed_rules"])
    ratio = lambda num, den: round(num / den, 4) if den else None
    return {
        "kernel": KERNEL,
        "cases": len(rows), "mutated": len(bad), "clean": len(clean),
        "wrongful_releases": sum(r["action"] == "release" for r in bad),
        "escalated_mutated": sum(r["action"] == "escalate" for r in bad),  # waited on the button
        "catch_rate": ratio(sum(r["action"] != "release" for r in bad), len(bad)),
        "false_hold_rate": ratio(sum(r["action"] == "hold" for r in clean), len(clean)),
        "dollars_protected": str(sum(Decimal(r["amount"]) for r in bad if r["action"] != "release")),
        "dollars_delayed": str(sum(Decimal(r["amount"]) for r in clean if r["action"] == "hold")),
        "by_anomaly": dict(sorted(by_type.items())),
        # wrongful releases and false holds, for "attacks that got through and what we changed"
        "misses": [r for r in rows if (r["anomaly"] and r["action"] == "release")
                   or (r["anomaly"] is None and r["action"] == "hold")][:50],
    }


def run(n=500, rng_seed=0) -> dict:
    rng = random.Random(rng_seed)
    rows = []
    for i, (data, label) in enumerate(make_cases(n, rng)):
        d = decide(data, label.request_id)
        rows.append({
            "case": i, "request_id": label.request_id, "anomaly": label.anomaly,
            "expected": label.expected_action.value, "expected_rules": label.rules,
            "action": d.action.value, "failed_rules": [x.rule for x in d.results if not x.passed],
            "amount": str(amount(data, label.request_id)),
        })
    return score(rows)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    card = run(n)
    out = Path("data/scorecard.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(card, indent=2))
    print(f"kernel: {card['kernel']}")
    print(f"{card['cases']} cases ({card['mutated']} mutated, {card['clean']} clean) -> {out}")
    print(f"wrongful releases {card['wrongful_releases']} | catch rate {card['catch_rate']} | "
          f"false-hold rate {card['false_hold_rate']} | protected ${card['dollars_protected']}")
