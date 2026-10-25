"""A Z3 model of the money rules (01, 02, 03, 13), for proving what they guarantee together.

Each rule checks one piece of a pay request. The properties worth having -- "we never pay
past the contract", "we never pay past what the site video shows", "retainage stays held"
-- hold only if the pieces combine correctly, which no single rule states. Z3 checks them
over every possible request at once: we assert "every hard rule passes" and "the property
is broken", and ask for an example. `unsat` (no example exists) is a proof; `sat` comes
with a concrete counterexample.

Months are handled by induction. Assume what we paid so far equals the latest paid
request's earned-less-retainage (true before the first payment: both are 0). Show that
after paying one more request that passes every rule, it still does. That covers any number
of months. The properties then follow from the latest request alone.

What the model covers, and what it assumes:
- Money is whole cents; retainage is (total * rate) rounded half-up, as rule 03 does.
- Every line's item is on the subcontract (rule 02 fails a line that isn't, so those
  requests never pay). Contract scheduled values are positive.
- Only one paid request per application number, and the executor pays exactly
  g702.current_payment_due (kernel/executor.py refuses anything else).
- Progress payments only: no final release, so retainage is never due back.
- A request has at most N_LINES lines over N_ITEMS contract items. The proof is for that
  size; raise them to check bigger requests (it gets slower, not less true).
Rules 04-12 look facts up (waivers, insurance, CRS, bank account) and don't affect the
amount, so they're not modelled. Rules 14 and 15 can be overridden by an approval-key tap,
so the properties are proved without them: they hold even if the owner approves everything.

The model is written by hand, so it can drift from the code. tests/test_proofs.py guards
that by building real KernelState/PayRequest objects from Z3's solutions and checking the
real rules agree with the model, pass for pass and fail for fail.
"""
from dataclasses import dataclass, field
from decimal import Decimal

import z3

N_ITEMS = 3
N_LINES = 4  # more lines than items, so a repeated item is expressible


@dataclass
class Config:
    """Which version of the rules to model. The defaults are the kernel as written; the
    switches reproduce earlier versions, so the tests can show each proof catches the bug
    that version had."""
    distinct_items: bool = True        # rule 01 rejects an item listed twice
    certified_from_lines: bool = True  # rule 01 recomputes "previously certified" from the prior request's lines


def half_up(x, rate_per_mille: int):
    """(x cents * rate).quantize(0.01, ROUND_HALF_UP) for rate = rate_per_mille / 1000.
    Half-up rounds ties away from zero, so negatives mirror positives."""
    p = rate_per_mille
    return z3.If(x >= 0, (2 * x * p + 1000) / 2000, -((2 * -x * p + 1000) / 2000))


@dataclass
class Line:
    active: z3.BoolRef
    item: z3.ArithRef
    total: z3.ArithRef
    retainage: z3.ArithRef
    previous: z3.ArithRef | None = None
    pct_tenths: z3.ArithRef | None = None  # printed percent complete, in tenths of a point


def _lines(prefix: str, current: bool) -> list[Line]:
    out = []
    for i in range(N_LINES):
        ln = Line(z3.Bool(f"{prefix}{i}_active"), z3.Int(f"{prefix}{i}_item"),
                  z3.Int(f"{prefix}{i}_total"), z3.Int(f"{prefix}{i}_ret"))
        if current:
            ln.previous, ln.pct_tenths = z3.Int(f"{prefix}{i}_prev"), z3.Int(f"{prefix}{i}_pct10")
        out.append(ln)
    return out


def _earned_less_retainage(lines: list[Line]):
    return z3.Sum([z3.If(l.active, l.total - l.retainage, 0) for l in lines])


def _distinct(lines: list[Line]):
    return z3.And([z3.Not(z3.And(a.active, b.active, a.item == b.item))
                   for i, a in enumerate(lines) for b in lines[i + 1:]])


@dataclass
class Model:
    """Every variable, plus each rule's constraints kept separately so a test can negate one."""
    cfg: Config
    rate: int  # retainage rate per mille (100 = 10%)
    sv: list = field(default_factory=list)       # contract scheduled value per item (cents)
    co: list = field(default_factory=list)       # approved change orders per item (cents)
    has_video: list = field(default_factory=list)
    video: list = field(default_factory=list)    # video estimate per item, whole percent
    tol: z3.ArithRef = None                      # progress_tolerance_points
    video_limit: list = field(default_factory=list)  # (video + tol)% of the contract SV, in cents
    prior: list = field(default_factory=list)    # last paid request's lines
    prior_line6: z3.ArithRef = None              # its G702 line 6, as the sub printed it
    lines: list = field(default_factory=list)    # this request's lines
    less_previous: z3.ArithRef = None            # G702 line 7, as printed
    due: z3.ArithRef = None                      # G702 line 8: what the executor pays
    g702_retainage: z3.ArithRef = None
    g702_completed: z3.ArithRef = None
    paid_before: z3.ArithRef = None              # total actually paid before this request
    world: list = field(default_factory=list)    # constraints that hold of any real state
    rules: dict = field(default_factory=dict)    # rule number -> its constraints

    def ceiling(self, item):
        return z3.Sum([z3.If(item == k, self.sv[k] + self.co[k], 0) for k in range(N_ITEMS)])

    def pick(self, values, item):
        return z3.Sum([z3.If(item == k, values[k], 0) for k in range(N_ITEMS)])

    def pick_bool(self, values, item):
        return z3.Or([z3.And(item == k, values[k]) for k in range(N_ITEMS)])

    @property
    def paid_after(self):
        return self.paid_before + self.due

    @property
    def billed_to_date(self):
        return z3.Sum([z3.If(l.active, l.total, 0) for l in self.lines])

    def all_rules(self):
        return [c for cs in self.rules.values() for c in cs]


def build(cfg: Config = Config(), rate: int = 100, exact_video: bool = False) -> Model:
    """The model for one request. Rule 13's limit, (video + tolerance)% of the scheduled
    value, multiplies two unknowns, which Z3 can't decide efficiently. Proofs leave each
    item's limit as an unknown of its own (`exact_video=False`): a property proved for every
    possible limit holds for the real one. Tests that build concrete requests fix the
    contract numbers first and pass `exact_video=True` to tie the limit to them."""
    m = Model(cfg=cfg, rate=rate)
    m.sv = [z3.Int(f"sv_{k}") for k in range(N_ITEMS)]
    m.co = [z3.Int(f"co_{k}") for k in range(N_ITEMS)]
    m.has_video = [z3.Bool(f"has_video_{k}") for k in range(N_ITEMS)]
    m.video = [z3.Int(f"video_{k}") for k in range(N_ITEMS)]
    m.tol = z3.Int("tol")
    m.video_limit = [z3.Real(f"video_limit_{k}") for k in range(N_ITEMS)]
    m.prior, m.lines = _lines("prior", False), _lines("line", True)
    m.prior_line6, m.less_previous, m.due = z3.Int("prior_line6"), z3.Int("less_previous"), z3.Int("due")
    m.g702_retainage, m.g702_completed, m.paid_before = z3.Int("g702_ret"), z3.Int("g702_completed"), z3.Int("paid_before")

    w = m.world
    for k in range(N_ITEMS):
        w += [m.sv[k] > 0, m.co[k] >= -m.sv[k], m.video[k] >= 0, m.video[k] <= 100]
    w += [m.tol >= 0, m.tol <= 100]
    w += [lim >= 0 for lim in m.video_limit]  # a percentage of a positive value
    if exact_video:
        w += [100 * m.video_limit[k] == (m.video[k] + m.tol) * m.sv[k] for k in range(N_ITEMS)]
    for l in m.prior + m.lines:
        w += [l.item >= 0, l.item < N_ITEMS]
    # the prior request was paid, so it passed rules 01 and 03 itself
    w += [_distinct(m.prior)]
    w += [l.retainage == half_up(l.total, rate) for l in m.prior]
    # induction hypothesis: what we've paid so far is the prior request's earned-less-retainage
    w += [m.paid_before == _earned_less_retainage(m.prior)]

    # rule 01: starting balances from our ledger, payment due recomputed, no repeats
    certified = _earned_less_retainage(m.prior) if cfg.certified_from_lines else m.prior_line6
    r01 = []
    for l in m.lines:
        on_prior = [z3.And(p.active, p.item == l.item) for p in m.prior]
        prior_total = z3.Sum([z3.If(c, p.total, 0) for c, p in zip(on_prior, m.prior)])
        r01.append(z3.Implies(l.active, l.previous == prior_total))
    r01 += [m.less_previous == certified, m.due == _earned_less_retainage(m.lines) - certified]
    if cfg.distinct_items:
        r01.append(_distinct(m.lines))
    r01.append(m.due >= 0)

    # rule 02: no line past the contract's scheduled value plus approved change orders
    r02 = [z3.Implies(l.active, l.total <= m.ceiling(l.item)) for l in m.lines]

    # rule 03: retainage is the contract rate on every line and on the G702, never zeroed early
    r03 = [z3.Implies(l.active, l.retainage == half_up(l.total, rate)) for l in m.lines]
    r03 += [m.g702_retainage == z3.Sum([z3.If(l.active, l.retainage, 0) for l in m.lines]),
            z3.Not(z3.And(m.g702_retainage <= 0, m.g702_completed > 0))]

    # rule 13: claimed percent = max(printed, total / contract SV); within tolerance of the video
    r13 = []
    for l in m.lines:
        filmed = z3.And(l.active, m.pick_bool(m.has_video, l.item))
        r13.append(z3.Implies(filmed, z3.And(l.pct_tenths <= 10 * (m.pick(m.video, l.item) + m.tol),
                                             z3.ToReal(l.total) <= m.pick(m.video_limit, l.item))))
    m.rules = {"01": r01, "02": r02, "03": r03, "13": r13}
    return m


# --- the properties ---------------------------------------------------------

def net(x, rate: int):
    """What's paid on a billed amount: the amount less its retainage."""
    return x - half_up(x, rate)


def net_is_monotone(rate: int) -> bool:
    """Lemma: billing more never means being paid less (x <= y implies net(x) <= net(y)).
    Proved on its own here; contract_cap uses instances of it as hints, because the rounding
    divisions make Z3 slow to find it unaided."""
    x, y = z3.Ints("x y")
    s = z3.Solver()
    s.add(x <= y, net(x, rate) > net(y, rate))
    return s.check() == z3.unsat


def contract_cap(m: Model):
    """Paid to date never exceeds the contract (plus approved change orders) less retainage."""
    return m.paid_after <= z3.Sum([net(m.sv[k] + m.co[k], m.rate) for k in range(N_ITEMS)])


def contract_cap_hints(m: Model) -> list:
    """Instances of net_is_monotone: each line against its ceiling, and each ceiling against 0."""
    hints = [z3.Implies(z3.And(l.total <= m.ceiling(l.item), l.retainage == half_up(l.total, m.rate)),
                        l.total - l.retainage <= net(m.ceiling(l.item), m.rate)) for l in m.lines]
    return hints + [z3.Implies(m.sv[k] + m.co[k] >= 0, net(m.sv[k] + m.co[k], m.rate) >= 0)
                    for k in range(N_ITEMS)]


def video_cap(m: Model):
    """Paid to date never exceeds what the site video supports, plus tolerance, on each filmed
    item (an unfilmed item is capped by its contract ceiling instead)."""
    cap = z3.Sum([z3.If(m.has_video[k], m.video_limit[k], z3.ToReal(m.sv[k] + m.co[k]))
                  for k in range(N_ITEMS)])
    return z3.ToReal(m.paid_after) <= cap


def retainage_held(m: Model):
    """Billed to date minus paid to date is exactly the contract rate's retainage on what's
    billed: the sub can't get retainage paid out early through the payment due."""
    held = z3.Sum([z3.If(l.active, half_up(l.total, m.rate), 0) for l in m.lines])
    return m.billed_to_date - m.paid_after == held


PROPERTIES = {
    "contract_cap": contract_cap,
    "video_cap": video_cap,
    "retainage_held": retainage_held,
}
HINTS = {"contract_cap": contract_cap_hints}


def counterexample(name: str, cfg: Config = Config(), rate: int = 100) -> dict | None:
    """None if the property holds for every request passing rules 01, 02, 03 and 13 (a
    proof); otherwise one request that passes them all and breaks it."""
    m = build(cfg, rate)
    s = z3.Solver()
    s.add(*m.world, *m.all_rules(), *HINTS.get(name, lambda _: [])(m), z3.Not(PROPERTIES[name](m)))
    result = s.check()
    if result == z3.unsat:
        return None
    if result != z3.sat:
        raise RuntimeError(f"z3 returned {result}")
    return describe(m, s.model())


# --- turning a solution into numbers ----------------------------------------

def cents(x) -> Decimal:
    return Decimal(x).scaleb(-2)


def describe(m: Model, sol: z3.ModelRef) -> dict:
    """Read a Z3 solution back as plain values: everything tests need to build real objects."""
    def i(x):
        return sol.eval(x, model_completion=True).as_long()

    def b(x):
        return z3.is_true(sol.eval(x, model_completion=True))

    def lines(ls, current):
        out = []
        for l in ls:
            if not b(l.active):
                continue
            row = {"item": i(l.item), "total": i(l.total), "retainage": i(l.retainage)}
            if current:
                row |= {"previous": i(l.previous), "pct_tenths": i(l.pct_tenths)}
            out.append(row)
        return out

    return {
        "rate": m.rate,
        "sv": [i(v) for v in m.sv], "co": [i(v) for v in m.co],
        "video": [i(v) if b(h) else None for v, h in zip(m.video, m.has_video)],
        "tol": i(m.tol),
        "prior": lines(m.prior, False), "prior_line6": i(m.prior_line6),
        "lines": lines(m.lines, True),
        "less_previous": i(m.less_previous), "due": i(m.due),
        "g702_retainage": i(m.g702_retainage), "g702_completed": i(m.g702_completed),
        "paid_before": i(m.paid_before),
    }
