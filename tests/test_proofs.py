"""Z3 proofs of what the money rules guarantee together (proofs/kernel_model.py has the model).

Three kinds of test:
- proofs: no request passing rules 01, 02, 03 and 13 breaks a property, for any amounts;
- negative controls: each proof finds the bug in the version of rule 01 that had it, so a
  proof that passes isn't passing because the model is too strict to say anything;
- conformance: requests Z3 builds to pass (or to fail one rule) go through the real rule
  functions, which must agree. This is what keeps the hand-written model honest about the code.
"""
import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

z3 = pytest.importorskip("z3", reason="z3-solver not installed: pip install -r requirements-dev.txt")

from kernel.rules import (rule_01_cumulative_continuity, rule_02_scheduled_value_ceiling,  # noqa: E402
                          rule_03_retainage_math, rule_13_progress_plausible)
from kernel.state import KernelState  # noqa: E402
from proofs import kernel_model as km  # noqa: E402
from shared.schema import ChangeOrder, LineItem, PayRequest, SiteEvidence, SOVLine  # noqa: E402

RATES = [100, 50, 125]  # 10%, 5%, 12.5%
REAL_RULES = {"01": rule_01_cumulative_continuity.check, "02": rule_02_scheduled_value_ceiling.check,
              "03": rule_03_retainage_math.check, "13": rule_13_progress_plausible.check}
BASE = json.loads((Path(__file__).parent / "fixtures" / "rule_01_pass.json").read_text())


# --- proofs -------------------------------------------------------------------

@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("prop", sorted(km.PROPERTIES))
def test_property_holds(prop, rate):
    found = km.counterexample(prop, rate=rate)
    assert found is None, f"{prop} fails for this request, which passes every rule: {found}"


@pytest.mark.parametrize("rate", RATES)
def test_lemma_net_is_monotone(rate):
    assert km.net_is_monotone(rate)


# --- negative controls --------------------------------------------------------

@pytest.mark.parametrize("prop", ["contract_cap", "video_cap"])
def test_repeated_item_would_break_the_caps(prop):
    """Before rule 01 rejected repeats, listing an item twice passed every per-line check
    and paid both lines."""
    found = km.counterexample(prop, km.Config(distinct_items=False))
    assert found is not None
    items = [l["item"] for l in found["lines"]]
    assert len(items) != len(set(items)), found


@pytest.mark.parametrize("prop", ["contract_cap", "retainage_held"])
def test_trusting_line_6_would_break_the_caps(prop):
    """Before rule 01 recomputed it, "previously certified" was the prior G702's line 6,
    which nothing checked: understate it once, get paid that month's work again."""
    found = km.counterexample(prop, km.Config(certified_from_lines=False))
    assert found is not None
    assert found["less_previous"] < found["paid_before"], found


# --- conformance: the model against the real rules ------------------------------

def to_kernel(sol: dict) -> tuple[KernelState, PayRequest]:
    """A real KernelState and PayRequest with the numbers in a Z3 solution, built on the
    rule 01 pass fixture so every field the money rules don't read is realistic."""
    state, request = KernelState.model_validate(BASE["state"]), PayRequest.model_validate(BASE["request"])
    item = [f"I{k}" for k in range(km.N_ITEMS)]
    c = km.cents

    def line(row, sv):
        prev = row.get("previous", 0)
        return LineItem(item_no=item[row["item"]], description=f"item {row['item']}", scheduled_value=c(sv),
                        previous_applications=c(prev), this_period=c(row["total"] - prev),
                        stored_materials=Decimal("0.00"), total_to_date=c(row["total"]),
                        pct_complete=Decimal(row.get("pct_tenths", 0)) / 10,
                        balance_to_finish=c(sv - row["total"]), retainage=c(row["retainage"]))

    prior = request.model_copy(update={
        "id": "prior", "number": request.number - 1,
        "lines": [line(r, sol["sv"][r["item"]]) for r in sol["prior"]],
        "g702": request.g702.model_copy(update={"total_earned_less_retainage": c(sol["prior_line6"])}),
    })
    request = request.model_copy(update={
        "lines": [line(r, sol["sv"][r["item"]]) for r in sol["lines"]],
        "g702": request.g702.model_copy(update={
            "retainage": c(sol["g702_retainage"]), "total_completed_stored": c(sol["g702_completed"]),
            "less_previous_certificates": c(sol["less_previous"]), "current_payment_due": c(sol["due"])}),
    })
    state = state.model_copy(update={
        "subcontract": state.subcontract.model_copy(update={
            "sov": [SOVLine(item_no=item[k], description=f"item {k}", scheduled_value=c(v))
                    for k, v in enumerate(sol["sv"])],
            "retainage_rate": Decimal(sol["rate"]) / 1000}),
        "change_orders": [ChangeOrder(id=f"co_{k}", job_id=request.job_id, sub_id=request.sub_id, item_no=item[k],
                                      amount=c(v), approved=True, date=date(2026, 8, 1))
                          for k, v in enumerate(sol["co"]) if v],
        "prior_requests": [prior],
        "site_evidence": SiteEvidence(id="site", job_id=request.job_id, date=request.period_end, frames=[],
                                      estimates={item[k]: Decimal(v) for k, v in enumerate(sol["video"])
                                                 if v is not None}),
        "contract": {**state.contract, "progress_tolerance_points": sol["tol"]},
    })
    return state, request


def solutions(rate: int, rng: random.Random, extra, n: int):
    """Up to n distinct solutions, each with randomly fixed contract numbers so they differ."""
    for _ in range(n):
        m = km.build(rate=rate, exact_video=True)
        s = z3.Solver()
        s.set("random_seed", rng.randrange(1 << 16))
        s.add(*m.world, *extra(m))
        for k in range(km.N_ITEMS):
            s.add(m.sv[k] == rng.choice([1, 99, 10_000, 4_567_89, 100_000_00]))
            if rng.random() < 0.5:
                s.add(m.co[k] == 0)
            s.add(m.has_video[k] == (rng.random() < 0.7), m.video[k] == rng.choice([0, 33, 50, 97, 100]))
        s.add(m.tol == rng.choice([0, 5, 10]))
        s.add(z3.Sum([z3.If(l.active, 1, 0) for l in m.lines]) >= rng.randint(0, km.N_ITEMS))
        if s.check() == z3.sat:
            yield km.describe(m, s.model())


def real_results(sol):
    state, request = to_kernel(sol)
    return {n: check(state, request) for n, check in REAL_RULES.items()}


@pytest.mark.parametrize("rate", RATES)
def test_model_passes_only_what_the_rules_pass(rate):
    rng = random.Random(rate)
    seen = 0
    for sol in solutions(rate, rng, lambda m: m.all_rules(), 25):
        seen += 1
        failed = {n: r.message for n, r in real_results(sol).items() if not r.passed}
        assert not failed, f"the model passes this request but the real rules fail it: {failed}\n{sol}"
    assert seen >= 20


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("rule", sorted(REAL_RULES))
def test_model_fails_only_what_the_rules_fail(rule, rate):
    """Z3 builds requests that break exactly one modelled rule; the real rule must fail them
    and the others must pass. A rule loosened in code but not in the model is caught here."""
    def near_miss(m):
        others = [c for n, cs in m.rules.items() if n != rule for c in cs]
        return [*others, z3.Not(z3.And(m.rules[rule]))]

    rng = random.Random(f"{rule}-{rate}")
    seen = 0
    for sol in solutions(rate, rng, near_miss, 15):
        seen += 1
        results = real_results(sol)
        assert not results[rule].passed, f"the model fails rule {rule} here but the real rule passes it\n{sol}"
        failed_others = {n: r.message for n, r in results.items() if n != rule and not r.passed}
        assert not failed_others, f"the model passes these rules but the real ones fail: {failed_others}\n{sol}"
    assert seen >= 10


@pytest.mark.parametrize("rate", RATES)
def test_half_up_matches_decimal(rate):
    """The model's integer retainage rounding is Decimal's quantize(ROUND_HALF_UP)."""
    from decimal import ROUND_HALF_UP
    for x in [*range(-2000, 2001), 12_345_678_95, -99_999_99]:
        want = (km.cents(x) * Decimal(rate) / 1000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        got = z3.simplify(km.half_up(z3.IntVal(x), rate))
        assert km.cents(got.as_long()) == want, (x, rate)
