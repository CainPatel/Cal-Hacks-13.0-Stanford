# Draw

An AI accounts-payable clerk for small general contractors. The model can only *propose* a
payment; a deterministic policy kernel, a calibrated judgment layer, and a hardware-held
signing key decide whether money moves. Built at Cal Hacks 13.0.

Spec: [`docs/spec.pdf`](docs/spec.pdf). Team rules: [`CLAUDE.md`](CLAUDE.md). Secrets: [`SECURITY.md`](SECURITY.md) (public repo; read before adding any key).

## Setup

```
uv venv .venv && uv pip install -p .venv -r requirements.txt
cp .env.example .env && chmod 600 .env   # fill in keys; never commit; share by DM (SECURITY.md)
make hooks                  # once per clone: blocks commits that contain secrets
make test
```

## Run the demo

```
make demo                   # fresh log, decide May-Oct, score 5,000 attacks, API on :8000
make demo PORT=8010         # if 8000 is taken
```

`make demo` archives any existing `data/events.db` to `data/archive-<time>-events.db`; it never deletes a log.

## Run the whole pipeline locally

Three terminals, from the repo root after Setup:

```
make events && make replay        # 1. load the seed into data/events.db, decide May-Oct
make api                          # 2. dashboard API on :8000
make device                       # 3. approval key on :8001 (Mac mock: type a / r + Enter)
```

Then exercise each stage:

| Stage | Try it |
|---|---|
| Decisions | `curl localhost:8000/requests?month=6`, `curl localhost:8000/requests/pr_sub_03_6` |
| Judgment in the loop | `python -m events replay 6 --judge` on a fresh log (`make keys` first: needs `JEV_API_KEY` and/or `ANTHROPIC_API_KEY`) |
| Pay application upload | `make synthetic`, then `curl -F file=@data/synthetic/oct/pr_sub_04_6/g703.pdf -F sub_id=sub_04 -F job_id=<job> localhost:8000/pay-apps` and `curl -X POST localhost:8000/replay` |
| Release / COI / email upload | `POST /releases` (file, sub_id, job_id), `POST /cois` (file, sub_id), `POST /emails` (file) |
| Site video | `POST /site-video` (file, job_id); needs `ANTHROPIC_API_KEY` |
| Attacks and scorecard | `make fuzz N=2000`, `curl localhost:8000/scorecard`, `POST /attacks` |

Without keys, nothing breaks: Claude-backed uploads (scans, video) answer 422 with the reason, emails
are read with code-only claims, and a judged replay escalates every request rule 15 can't vouch for.
Paying (the money stage, all in the Nessie sandbox, no real money):

```
python -m events nessie-setup     # once: creates the GC's Nessie account, prints NESSIE_ACCOUNT_ID for .env
curl localhost:8001/pubkey        # register the approval key: APPROVAL_KEY_PUBKEY in .env (or contract.yaml)
curl -X POST localhost:8000/requests/pr_sub_02_6/pay    # released: pays straight away
curl -X POST localhost:8000/requests/pr_sub_15_6/pay    # escalated: waits for `a` + Enter on the device
python -m events nessie-sync      # pull the bank's ledger back into the log
python -m events crs-verify       # check every vendor with CRS (rule 09); POST /vendors/{id}/verify for one
```

`POST /requests/{id}/pay` takes `{"confirm": true}`. It decides again on current facts first, pays a
request once (also refusing the same application under another id), and if the bank's answer is
unknown (a timeout, a 5xx) it blocks further attempts until `POST /requests/{id}/resolve` (or
`python -m events nessie-resolve <id>`) checks Nessie for the bill. Payments are serialized within one
API process; run a single API worker.

## Commands

| Command | Does |
|---|---|
| `make seed` | demo company into `data/seed.db` (3 jobs, 15 subs, 6 months, 3 demo holds) |
| `make events` | load the seed into the append-only log `data/events.db` (refuses if it exists) |
| `make replay M=6` | decide every request through month M (1 = May, 6 = October) |
| `make fuzz N=5000` | attack the pipeline N times, write `data/scorecard.json` |
| `make api` | dashboard API on `:8000` (see `api/main.py` for endpoints) |
| `make synthetic W=10` | render pay apps, releases, COIs, emails + `truth.json` for perception |
| `make benchmark` | score perception's extractor on `data/synthetic` (no Claude calls) |
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
