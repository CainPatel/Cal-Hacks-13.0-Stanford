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

Four terminals, from the repo root after Setup:

```
make events && make replay        # 1. load the seed into data/events.db, decide May-Oct
make api                          # 2. dashboard API on :8000
make device                       # 3. approval key on :8001 (Mac mock: type a / r + Enter)
make web                          # 4. dashboard on :3000 (npm install on first run)
```

Open `localhost:3000` for the three views: the pay-request list, the owner's **Approve**
screen (mirrors whatever the device is showing), and **Security** (scorecard, calibration,
attack console). Or exercise the API directly:

| Stage | Try it |
|---|---|
| Decisions | `curl localhost:8000/requests?month=6`, `curl localhost:8000/requests/pr_sub_03_6` |
| Judgment in the loop | `python -m events replay 6 --judge` on a fresh log (`make keys` first: needs `JEV_API_KEY` and/or `ANTHROPIC_API_KEY`) |
| Pay application upload | `make synthetic`, then `curl -F file=@data/synthetic/oct/pr_sub_04_6/g703.pdf -F sub_id=sub_04 -F job_id=<job> localhost:8000/pay-apps` and `curl -X POST localhost:8000/replay` |
| Release / COI / email upload | `POST /releases` (file, sub_id, job_id), `POST /cois` (file, sub_id), `POST /emails` (file) |
| Site video | `POST /site-video` (file, job_id); needs `ANTHROPIC_API_KEY` |
| Attacks and scorecard | `make fuzz N=2000`, `curl localhost:8000/scorecard`, `POST /attacks` |
| Approve and pay | `curl -X POST localhost:8000/requests/<id>/approve` on an escalated request -- posts to the device, waits for a button, and on approve the executor verifies the signature and pays |
| Pay a released request | `curl -X POST -H 'content-type: application/json' -d '{"confirm":true}' localhost:8000/requests/<id>/pay` |
| Vendor identity (CRS) | `python -m events crs-verify`, or `POST /vendors/<id>/verify` (needs CRS credentials) |

Without keys, nothing breaks: Claude-backed uploads (scans, video) answer 422 with the reason, emails
are read with code-only claims, and a judged replay escalates every request rule 15 can't vouch for.
`POST /requests/{id}/approve` answers 503 until `DEVICE_URL` is set and the device's real public key
is pasted into `config/contract.yaml`'s `esp32_pubkey` (see `firmware/README.md` / `device/README.md`
for how to get it) -- until then, `kernel/executor.py` is exercised directly by
`tests/test_executor.py` and `tests/test_executor_gates.py`.

### The money stage (Nessie sandbox: pretend bank, no real money)

```
python -m events nessie-setup     # once: the GC's Nessie account + its history; prints NESSIE_ACCOUNT_ID for .env
python -m events nessie-sync      # copy the bank's ledger (owner draws, sub payments) into the log
```

Both pay endpoints (`/approve` for escalations, `/pay` for anything) decide again on current facts
first, pay a request once (also refusing the same application under another id), and sign the bank
account into the device's payload. If the bank's answer is unknown (a timeout, a 5xx), further attempts
are blocked until `POST /requests/{id}/resolve` (or `python -m events nessie-resolve <id>`) finds out
from Nessie. Payments run one at a time across every API instance (a Postgres advisory lock on Vercel, a
file lock locally).

## Results

Every measured number (kernel vs. 16 attack types, the limit dial, perception accuracy, Jev calibration), with commands to reproduce: [`docs/results.md`](docs/results.md).

## Deploy

Vercel (dashboard + API), Neon Postgres for the event log, a private Blob store for uploads: see [`docs/deploy.md`](docs/deploy.md).

## Commands

| Command | Does |
|---|---|
| `make seed` | demo company into `data/seed.db` (3 jobs, 15 subs, 6 months, 3 demo holds) |
| `make events` | load the seed into the append-only log `data/events.db` (refuses if it exists) |
| `make replay M=6` | decide every request through month M (1 = May, 6 = October) |
| `make fuzz N=5000` | attack the pipeline N times, write `data/scorecard.json` |
| `make api` | dashboard API on `:8000` (see `api/main.py` for endpoints) |
| `make web` | Next.js dashboard on `:3000` (`npm install` on first run) |
| `make device` | approval key on `:8001`; Mac mock (`DRAWKEY_MOCK=1`), type `a`/`r` + Enter |
| `make device-test` | tests for the Pi/mock approval device |
| `make synthetic W=10` | render pay apps, releases, COIs, emails + `truth.json` for perception |
| `make benchmark` | score perception's extractor on `data/synthetic` (no Claude calls) |
| `make keys` | which of the `.env` secrets are set (never prints a value) |
| `make hooks` | turn on the repo's secret-blocking pre-commit hook (once per clone) |
| `make demo` | fresh log, decide May-Oct, score 5,000 attacks, serve the API |
| `make test` | all tests |

## How a pay request flows

```
documents ──► perception/ (Claude extraction, untrusted)
                 │ facts
                 ▼
            events/ append-only log ──► pipeline.py ──► judgment/ (Jev) ──► kernel/ (pure rules)
                 ▲                                                            │ Decision
                 │  kernel_trace, signature, execution                        │
                 └────────────────────────────────────────────────────────────┤
                                                                   hold/release │ escalate
                                                                                ▼
                                                   api/ ──POST /approve──► firmware/ or device/
                                                    ▲                      (ESP32 or Pi button, signs)
                                                    │ serves the log            │ signed approval
                                                    │                           ▼
                                                   web/                  kernel/executor.py ──► Nessie
                                            (pay requests, approve, security)

Nessie (owner draws, sub payments) and CRS (vendor identity) are the simulated bank and vendor
registry; seed/ and fuzzer/ stand in for both until real sandbox accounts are wired up.
fuzzer/ drives attacks through pipeline.py and writes the scorecard that api/ and web/ read.
```

## Scorecard fields

- `wrongful_if_every_button_approved`, `rule_catch_rate`: what the rules stop on their own (headline)
- `wrongful_releases`, `catch_rate`: with the ESP32 button as a second layer
- `false_hold_rate`, `dollars_delayed`: cost to honest subs
- `button_presses_per_job_month`, `auto_released_share`: cost to the owner
- `limit_sweep`: the above at auto-release limits from $10K to $100K
- `by_anomaly`, `misses`: per attack type, and the attacks that got through
