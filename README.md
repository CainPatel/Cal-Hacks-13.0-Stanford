# Draw

## Introduction

An AI accounts-payable clerk for small general contractors. The model can only *propose* a
payment; a deterministic policy kernel, a calibrated judgment layer, and a hardware-held
signing key decide whether money moves. Built at Cal Hacks 13.0.

## The problem

Every month, each subcontractor on a commercial job sends the general contractor (GC) a pay
application: an AIA G702 summary and a G703 continuation sheet listing cumulative percent complete on
every contract line. Before paying, someone in the GC's office checks it by hand: the cumulative math,
retainage, lien releases, insurance, supplier notices, whether the owner's draw has cleared, whether the
bank details changed, whether the progress claimed matches the job site. A 10-30 person GC does this for
40-60 lines per sub per project, from Excel and email. The failures are expensive: a lookalike-domain
email that redirects a six-figure wire, a supplier lien that surfaces after the sub is paid, months of
quiet over-billing.

## What Draw does

Draw reads the documents that already exist (G702/G703 as PDF or spreadsheet, lien releases, insurance
certificates, the sub's emails, the superintendent's site video), checks each pay request against 15
rules, and tells the office one of three things in a plain sentence with the evidence:

- **Ready to pay**: every check passed and the amount is under the auto-release limit.
- **Needs your OK**: over the limit, a new vendor, or a judgment call. The owner approves on a physical
  key on their desk, which signs that exact payee and amount.
- **On hold**: a rule failed. The screen shows why and what to do next ("Ask the sub for their supplier's
  release").

No AI output can move money. Claude reads documents and Jev (TypeSafe) answers judgment questions, but
both are treated as untrusted input to a pure-function rule kernel, and every payment above the limit
needs a hardware signature. Draw also ships with its own attacker: a fuzzer that tries to get wrong
payments through and a scorecard that reports how often it succeeds.

**Live:** [drawweb.vercel.app](https://drawweb.vercel.app) (dashboard) · [drawapi.vercel.app/docs](https://drawapi.vercel.app/docs) (API).
The data is one fictional GC with 3 jobs, 15 subs and six months of history.

## How it works

1. **Perception** (`perception/`): pdfplumber reads the G703's text layer; Claude handles scans, emails
   and site video frames. Output is a set of facts, never a decision.
2. **Event log** (`events/`): every fact, judgment, decision, signature and payment is appended to one
   log (SQLite locally, Neon Postgres in production, append-only enforced by trigger). Decisions are
   replayable from it.
3. **Judgment** (`judgment/`): Jev answers five questions per request (is the release the right form,
   does the bank change look like account takeover, is the progress plausible, is it a duplicate, what
   should happen), each with a confidence. Claude is the fallback.
4. **Kernel** (`kernel/`): 15 pure-function rules, no network or model calls. All pass and under the limit
   means release; rule 14 (over the limit) or 15 (low confidence) means escalate; anything else holds.
5. **Approval key** (`device/`, `firmware/`): a Raspberry Pi (or ESP32) with green and red buttons holds a
   P-256 private key and signs the request id, payee, amount and a one-time nonce.
6. **Executor** (`kernel/executor.py`): the only code that can call the bank (Capital One's Nessie
   sandbox). It re-checks the decision, verifies the signature against exactly what it is about to pay,
   refuses anything Nessie already holds, and pays once.

| # | Rule | # | Rule |
|---|---|---|---|
| 01 | Cumulative billing adds up | 09 | Vendor identity verified (CRS) |
| 02 | Within the scheduled value plus approved change orders | 10 | Bank details unchanged |
| 03 | Retainage withheld at the contract rate | 11 | Sender domain authentic (lookalike check) |
| 04 | Conditional lien release covers this payment | 12 | Not a duplicate |
| 05 | Last payment's unconditional release received | 13 | Progress matches the site video |
| 06 | Insurance current | 14 | Within the auto-release limit |
| 07 | Lower-tier suppliers released | 15 | Every judgment above the confidence floor |
| 08 | Owner's draw funded | | |

The full data flow is under [How a pay request flows](#how-a-pay-request-flows).

## Results

From [`docs/results.md`](docs/results.md), measured on `main`, Oct 25, 2026 (synthetic data):

| | |
|---|---|
| Generated attacks the rules stop on their own | **100%** of 8,029 (16 attack types and 35 two-fault combinations) |
| Held-out attacks written outside the codebase | **32 of 38** stopped; the 6 gaps are documented |
| Honest requests wrongly held | **0** of 1,994 |
| Owner's button presses in an honest month | **1.83** per job (63% of payments need no tap, at a $25K limit) |
| Pay applications read exactly by the code extractor | **940 / 940** (no model calls) |
| Jev's calibration over 6,000 answers | expected calibration error **0.029** |

## Built with

Python (FastAPI, Pydantic, pdfplumber) · Next.js, TypeScript, Tailwind, shadcn/ui · SQLite and Neon
Postgres · Vercel (dashboard, API, Blob storage) · Claude (document and video reading) · Jev by TypeSafe
(judgment) · Capital One Nessie (sandbox bank) · CRS (vendor identity) · Raspberry Pi / ESP32 (approval
key, ECDSA P-256).

## Repository layout

| Path | What's there |
|---|---|
| `shared/schema.py` | Pydantic models every stage uses |
| `perception/` | Document and video extraction |
| `judgment/` | Jev questions, Claude fallback |
| `kernel/` | The 15 rules, the decision runner, the payment executor |
| `events/`, `pipeline.py` | Append-only log, replay, the decision path |
| `fuzzer/` | Attack generators, held-out set, scorecard, attack console |
| `api/`, `web/` | FastAPI backend and Next.js dashboard |
| `device/`, `firmware/` | Approval key (Raspberry Pi; ESP32 fallback) |
| `seed/`, `synthetic/` | The fictional GC and its rendered documents |
| `crs/` | Vendor identity check |
| `docs/` | Spec, results, deploy guide, device and kernel contracts |

Spec: [`docs/spec.pdf`](docs/spec.pdf). Team rules: [`CLAUDE.md`](CLAUDE.md). Secrets: [`SECURITY.md`](SECURITY.md) (public repo; read before adding any key).

## Getting started

### Setup

```
uv venv .venv && uv pip install -p .venv -r requirements.txt
cp .env.example .env && chmod 600 .env   # fill in keys; never commit; share by DM (SECURITY.md)
make hooks                  # once per clone: blocks commits that contain secrets
make test
```

### Run the demo

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

Open `localhost:3000` for the views: the pay-request list (with **Pay** on requests that are ready),
the owner's **Approve** screen (mirrors whatever the device is showing), **Security** (scorecard,
calibration, attack console), **Upload**, and **Log** (every row of the event log, newest first, live).

**One log for the laptop and the deployment.** Put the Neon `DATABASE_URL` in `.env`
(docs/deploy.md, step 5) and every command above works on the same Postgres log as
drawapi.vercel.app: what you do locally shows up on the site and the other way round. `make test`
never touches it. `make demo-online` starts that shared log over with the demo company (the old log is
renamed, never deleted); `make snapshot` copies it to `data/events.db` and `make offline` serves the
copy with no network, as a fallback.

Or exercise the API directly:

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
first (Jev reviews again when a model is configured), pay a request once (also refusing the same
application under another id), and sign the bank account into the device's payload. Right before
paying, the executor asks Nessie whether it already holds this payment (or one for the same sub, job
and period): copies of Draw with separate logs can share one Nessie account, so the bank is the one
record of every payment. If it does, the payment is recorded in the log and refused; if Nessie can't be
asked, nothing is paid. `POST /sync` (or `make sync`) copies the bank's ledger into the log, and
`POST /replay` does it before deciding, so rule 08 sees the latest owner draws. If the bank's answer is unknown (a timeout, a 5xx), further attempts
are blocked until `POST /requests/{id}/resolve` (or `python -m events nessie-resolve <id>`) finds out
from Nessie. Payments run one at a time across every API instance (a Postgres advisory lock on Vercel, a
file lock locally).

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
| `make demo` | fresh local log, decide May-Oct, score 5,000 attacks, serve the API (never Neon) |
| `make demo-online` | start the shared Neon log over with the demo company, bank synced, Jev in the loop |
| `make reset` | start whichever log is configured over (old one renamed, never deleted): seed + demo company, bank synced, May-Oct decided |
| `make sync` | copy the bank's ledger (Nessie owner draws, sub payments) into the log |
| `make snapshot` / `make offline` | copy the shared Neon log to `data/events.db` / serve that copy offline |
| `make smoke` | check every stage of a running Draw over HTTP (`API=… WEB=…` for the deployment; read-only unless `FLAGS="--sync --pay --approve"`) |
| `make test` | all tests |

## Demo company (first pay application by email)

Larkspur Construction LLC (Harbor Light Hotel, `sub_16` / `job_4`) is on file with everything except its
first pay application, which arrives by email during the demo: vendor, subcontract, insurance, signed
conditional release, owner's draw and site video (sample numbers). `make events`, `make demo`,
`make demo-online` and `make reset` all load it (`seed/demo_company.py`).

Email the two files in `seed/demo_company_files/` (`g703.pdf`, `g702.pdf`) to the inbox from an
`@stanford.edu` address: attachments are filed under a sub only when the sender's domain is exactly the
sub's `email_domain` in `company.json`. Then press "Check October": it reads as $595,008.60 due, passes
rules 01-13, and needs the owner's OK (over the limit, first payment to this sub). The PDFs are a real
spreadsheet-exported pay application with every name and address made up, 10% retainage on every line
and the period moved to October 2026.

To run the demo again: `make reset`, then send the email. The reset also cancels, in the Nessie sandbox,
every payment made since the seed's history (Nessie keeps them, marked cancelled), so a request paid in an
earlier run comes in unchecked and can be approved and paid again. It works on the bank account in `.env`:
for the deployment's demo, `NESSIE_API_KEY` and `NESSIE_ACCOUNT_ID` there must be the deployment's (the
`bank_fingerprint` in `curl localhost:8000/` and `curl https://drawapi.vercel.app/` must match).

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
