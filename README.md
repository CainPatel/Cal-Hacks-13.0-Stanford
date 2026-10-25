# Draw

An AI accounts-payable clerk for small general contractors. The model can only *propose* a
payment; a deterministic policy kernel, a calibrated judgment layer, and a hardware-held
signing key decide whether money moves. Built at Cal Hacks 13.0.

Spec: [`docs/spec.pdf`](docs/spec.pdf). Team rules: [`CLAUDE.md`](CLAUDE.md). Secrets: [`SECURITY.md`](SECURITY.md) (public repo; read before adding any key).

## Setup

```
uv venv .venv && uv pip install -p .venv -r requirements-dev.txt   # requirements.txt plus z3-solver for the proofs
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

Open `localhost:3000` for the views: the pay-request list (with **Pay** on requests that are ready),
the owner's **Approve** screen (mirrors whatever the device is showing), **Security** (scorecard,
calibration, attack console), **Upload**, and **Log** (every row of the event log, newest first, live).
Or exercise the API directly:

**One log for the laptop and the deployment.** Put the Neon `DATABASE_URL` in `.env`
(docs/deploy.md, step 5) and every command above works on the same Postgres log as
drawapi.vercel.app: what you do locally shows up on the site and the other way round. `make test`
never touches it. `make demo-online` starts that shared log over with the demo company (the old log is
renamed, never deleted); `make snapshot` copies it to `data/events.db` and `make offline` serves the
copy with no network, as a fallback.

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
| `make demo` | fresh local log, decide May-Oct, score 5,000 attacks, serve the API (never Neon) |
| `make demo-online` | start the shared Neon log over with the demo company, bank synced, Jev in the loop |
| `make reset` | start whichever log is configured over (old one renamed, never deleted): seed + demo company, bank synced, May-Oct decided |
| `make sync` | copy the bank's ledger (Nessie owner draws, sub payments) into the log |
| `make snapshot` / `make offline` | copy the shared Neon log to `data/events.db` / serve that copy offline |
| `make smoke` | check every stage of a running Draw over HTTP (`API=… WEB=…` for the deployment; read-only unless `FLAGS="--sync --pay --approve"`) |
| `python -m fuzzer.describe_eval [N]` | score the attack console's "Or describe it" answers on 39 plain-English ideas, N times each (live Claude calls) |
| `make test` | all tests |
| `make prove` | just the Z3 proofs of what the money rules guarantee together (see below) |

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

## Proofs (Z3)

The fuzzer tries attacks someone thought of. `proofs/kernel_model.py` asks the opposite question
with the [Z3](https://github.com/Z3Prover/z3) solver: is there *any* request that passes every hard
rule and still breaks a money property? `unsat` means no such request exists, for any amounts.

| Property | Rules it needs |
|---|---|
| `contract_cap`: paid to date never exceeds the contract plus approved change orders, less retainage | 01, 02, 03 |
| `video_cap`: paid to date never exceeds what the site video shows plus tolerance | 01, 03, 13 |
| `retainage_held`: billed minus paid is exactly the contract rate's retainage, to the cent | 01, 03 |

Months are covered by induction: if what we've paid equals the last paid request's earned-less-retainage,
it still does after paying one more request that passes the rules. Rules 14 and 15 aren't assumed, so
the properties hold even if the owner approves every escalation. The model's assumptions (one paid
request per application number, progress payments only, at most 4 lines over 3 items) are in its
docstring.

The model is written by hand, so `tests/test_proofs.py` also has Z3 build requests that pass the model's
rules, or break exactly one, and runs them through the real rule functions, which must agree. Changing a
money rule in `kernel/rules/` without changing the model fails those tests. Negative controls check that
each proof finds the bug in the old version of rule 01 (see below), so a passing proof isn't vacuous.

The first run found two ways to be paid twice, both now closed in rule 01:
- the same line item listed twice: each copy passed rules 02 and 13 on its own, and both were paid;
- last month's G702 line 6 was taken as "previously certified" but never checked, so understating it
  one month paid that month's work again the next. Rule 01 now recomputes it from that request's lines.

`z3-solver` is in `requirements-dev.txt`, not `requirements.txt`, to keep the 80 MB wheel out of the
Vercel function. Without it, `make test` skips the proofs and says so.

## Scorecard fields

- `wrongful_if_every_button_approved`, `rule_catch_rate`: what the rules stop on their own (headline)
- `wrongful_releases`, `catch_rate`: with the ESP32 button as a second layer
- `false_hold_rate`, `dollars_delayed`: cost to honest subs
- `button_presses_per_job_month`, `auto_released_share`: cost to the owner
- `limit_sweep`: the above at auto-release limits from $10K to $100K
- `by_anomaly`, `misses`: per attack type, and the attacks that got through
