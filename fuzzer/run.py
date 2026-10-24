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

import pipeline
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
        a.duplicate_this_month: lambda: {},
        a.missing_prior_unconditional: lambda: {},
        a.inflated_scheduled_value: lambda: {},
        a.inflated_payment_due: lambda: {},
        a.release_amount_short: lambda: {},
    }[m]()


SINGLE = [a.overbill_pct, a.retainage_slip, a.missing_conditional_release, a.expired_wc, a.past_sov_no_co,
          a.duplicate_new_number, a.bank_change_email, a.supplier_notice_unreleased, a.draw_not_cleared,
          a.new_sub_ein_mismatch,
          # beyond the spec's list:
          a.duplicate_this_month, a.missing_prior_unconditional, a.inflated_scheduled_value, a.inflated_payment_due,
          a.release_amount_short]
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
        # headline: what the rules alone stop, as if the owner approved every button press unread
        "wrongful_if_every_button_approved": sum(r["action"] != "hold" for r in bad),
        "rule_catch_rate": ratio(sum(r["action"] == "hold" for r in bad), len(bad)),
        # with the button as a second layer: only auto-released attacks get through
        "wrongful_releases": sum(r["action"] == "release" for r in bad),
        "escalated_mutated": sum(r["action"] == "escalate" for r in bad),
        "catch_rate": ratio(sum(r["action"] != "release" for r in bad), len(bad)),
        "false_hold_rate": ratio(sum(r["action"] == "hold" for r in clean), len(clean)),
        "dollars_protected": str(sum(Decimal(r["amount"]) for r in bad if r["action"] != "release")),
        "dollars_delayed": str(sum(Decimal(r["amount"]) for r in clean if r["action"] == "hold")),
        "by_anomaly": dict(sorted(by_type.items())),
        # wrongful releases and false holds, for "attacks that got through and what we changed"
        "misses": [r for r in rows if (r["anomaly"] and r["action"] == "release")
                   or (r["anomaly"] is None and r["action"] == "hold")][:50],
    }


def button_load(worlds=WORLDS) -> dict:
    """The human cost of the button: escalations on a normal, clean month, per job."""
    actions = []
    jobs = 0
    for s in range(worlds):
        world = generate(rng_seed=s)
        jobs += len(world["jobs"])
        actions += [decide(world, r.id).action for r in world["pay_requests"] if r.number == a.THIS_MONTH]
    return {"button_presses_per_job_month": round(actions.count(Action.escalate) / jobs, 2),
            "auto_released_share": round(actions.count(Action.release) / len(actions), 4),
            "held_share_clean": round(actions.count(Action.hold) / len(actions), 4)}


LIMITS = [10000, 15000, 25000, 50000, 100000]


def limit_sweep(cases, limits=LIMITS, worlds=WORLDS) -> list[dict]:
    """The auto-release limit as a dial: owner's taps vs. attacks that would pay out automatically."""
    base = getattr(pipeline, "default_contract", dict)()  # {} under the stub kernel
    clean = [generate(rng_seed=s) for s in range(worlds)]
    jobs = sum(len(w["jobs"]) for w in clean)
    out = []
    for limit in limits:
        contract = {**base, "auto_release_limit_usd": limit}
        actions = [decide(w, r.id, contract=contract).action
                   for w in clean for r in w["pay_requests"] if r.number == a.THIS_MONTH]
        leaked = [amount(d, l.request_id) for d, l in cases
                  if l.anomaly and decide(d, l.request_id, contract=contract).action == Action.release]
        out.append({"limit": limit,
                    "button_presses_per_job_month": round(actions.count(Action.escalate) / jobs, 2),
                    "auto_released_share": round(actions.count(Action.release) / len(actions), 4),
                    "attacks_auto_paid": len(leaked), "dollars_auto_paid": str(sum(leaked))})
    return out


def run(n=500, rng_seed=0) -> dict:
    rng = random.Random(rng_seed)
    rows = []
    cases = list(make_cases(n, rng))
    for i, (data, label) in enumerate(cases):
        d = decide(data, label.request_id)
        rows.append({
            "case": i, "request_id": label.request_id, "anomaly": label.anomaly,
            "expected": label.expected_action.value, "expected_rules": label.rules,
            "action": d.action.value, "failed_rules": [x.rule for x in d.results if not x.passed],
            "amount": str(amount(data, label.request_id)),
        })
    return {**score(rows), **button_load(), "limit_sweep": limit_sweep(cases)}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    card = run(n)
    out = Path("data/scorecard.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(card, indent=2))
    print(f"kernel: {card['kernel']}")
    print(f"{card['cases']} cases ({card['mutated']} mutated, {card['clean']} clean) -> {out}")
    print(f"rules alone: {card['wrongful_if_every_button_approved']} attacks get through if every button press "
          f"is approved (rule catch rate {card['rule_catch_rate']})")
    print(f"button presses per job per month: {card['button_presses_per_job_month']} | "
          f"auto-released share of a clean month: {card['auto_released_share']}")
    for s in card["limit_sweep"]:
        print(f"  limit ${s['limit']:>7,}: {s['button_presses_per_job_month']:.2f} taps/job/month, "
              f"{s['auto_released_share']:.0%} of a clean month auto-paid, "
              f"{s['attacks_auto_paid']} attacks auto-paid (${Decimal(s['dollars_auto_paid']):,.0f})")
    print(f"wrongful releases {card['wrongful_releases']} | catch rate {card['catch_rate']} | "
          f"false-hold rate {card['false_hold_rate']} | protected ${card['dollars_protected']}")
