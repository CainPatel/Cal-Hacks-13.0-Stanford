# Draw

An AI accounts-payable clerk for small general contractors. The model can only *propose* a
payment; a deterministic policy kernel, a calibrated judgment layer, and a hardware-held
signing key decide whether money moves. Built at Cal Hacks 13.0.

Spec: [`docs/spec.pdf`](docs/spec.pdf). Team rules: [`CLAUDE.md`](CLAUDE.md).

## Setup

```
uv venv .venv && uv pip install -p .venv -r requirements.txt
cp .env.example .env        # fill in keys; .env is gitignored, share values by DM
make test
```

## Run the demo

```
make demo                   # fresh log, decide May-Oct, score 5,000 attacks, API on :8000
make demo PORT=8010         # if 8000 is taken
```

`make demo` archives any existing `data/events.db` to `data/archive-<time>-events.db`; it never deletes a log.

## Commands

| Command | Does |
|---|---|
| `make seed` | demo company into `data/seed.db` (3 jobs, 15 subs, 6 months, 3 demo holds) |
| `make events` | load the seed into the append-only log `data/events.db` (refuses if it exists) |
| `make replay M=6` | decide every request through month M (1 = May, 6 = October) |
| `make fuzz N=5000` | attack the pipeline N times, write `data/scorecard.json` |
| `make api` | dashboard API on `:8000` (see `api/main.py` for endpoints) |
| `make synthetic W=10` | render pay apps, releases, COIs, emails + `truth.json` for perception |
| `make test` | all tests |

## How a pay request flows

```
documents ──► perception/  (Claude extraction, untrusted)
                │ facts
                ▼
           events/  append-only log ──► pipeline.py ──► judgment/ (Jev, bounded) ──► kernel/ (pure rules)
                ▲                                                                       │ Decision
                └──────────── kernel_trace, signature, execution ◄── kernel/executor.py ◄┘
                                                                     (ESP32 signature, Nessie)
api/ serves the log to web/;  fuzzer/ drives attacks through pipeline.py and writes the scorecard.
```

## Scorecard fields

- `wrongful_if_every_button_approved`, `rule_catch_rate`: what the rules stop on their own (headline)
- `wrongful_releases`, `catch_rate`: with the ESP32 button as a second layer
- `false_hold_rate`, `dollars_delayed`: cost to honest subs
- `button_presses_per_job_month`, `auto_released_share`: cost to the owner
- `limit_sweep`: the above at auto-release limits from $10K to $100K
- `by_anomaly`, `misses`: per attack type, and the attacks that got through
