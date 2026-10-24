# Draw — CLAUDE.md

AI accounts-payable clerk for small general contractors. The model can only *propose* a payment; a deterministic policy kernel, a calibrated decision layer, and a hardware-held signing key decide whether money moves. Built at Cal Hacks 13.0, Oct 23–25 2026. Full spec: see `docs/spec.pdf` (or the shared Claude doc). This file is the operating contract for every human and every Claude Code session in this repo.

## Hard rules (never violate; if a task requires it, stop and ask)

1. `kernel/` is pure functions. No network, no LLM, no I/O. Input: state + proposal. Output: `list[RuleResult]`.
2. Only `kernel/executor.py` may call Nessie. Nothing else touches payment APIs.
3. A payment executes only if every `RuleResult.passed` is true (rules 14/15 may fail only with a signature) AND (the decision is `release` and amount ≤ `auto_release_limit`, OR a valid approval-key signature is present). Every escalation needs a signature, and the signature must cover this request's exact payee and amount; each one pays once (`kernel/executor.py`).
4. Never invent fields. All data flows through `shared/schema.py`. Add a field there first, with a fixture.
5. `events/` is append-only. No updates, no deletes.
6. No secrets in the repo (it is public; see `SECURITY.md`). Keys live in `.env` (gitignored; `make keys` checks them) and in GitHub Actions secrets for deploys. Run `make hooks` once per clone. Only the approval device's *public* key goes in `config/contract.yaml`.
7. Every rule in `kernel/rules/` ships with one passing fixture and one failing fixture in `tests/fixtures/`.
8. Do not add sponsor SDKs beyond Claude, Jev, Nessie, CRS. Do not add a chat interface. Do not build daily job reports.

## Repo layout and owners

```
shared/schema.py      Pydantic models everyone uses        (A, frozen Fri 8:30 PM)
seed/                 fake GC, jobs, subs, G703s, anomalies (B)
synthetic/            seed rendered as PDF/XLSX/EML + truth (B)
kernel/rules/         one file per rule, rule_01..rule_15   (A)
kernel/runner.py      runs all rules, decides, writes trace (A)
kernel/state.py       KernelState: one request's slice      (A)
kernel/executor.py    Nessie call behind signature gate     (A)
events/               SQLite append-only log + replay       (B)
pipeline.py           state -> KernelState -> judge -> kernel (B)
perception/           Claude extraction: G702/G703, releases, COIs, emails, video (C)
judgment/             Jev questions + Claude fallback       (C)
crs/                  CRS vendor identity check (rule 09) (C)
fuzzer/               mutators, scorecard, calibration, attack console (B)
api/                  FastAPI: dashboard reads, uploads, SSE (B started; A/D)
web/                  Next.js + shadcn dashboard            (B/D)
device/               approval key on a Raspberry Pi (in use) (D)
firmware/             ESP32 approval key (fallback)         (D)
config/contract.yaml  permissions contract (editable)       (A)
docs/                 spec, device wire contract, kernel input contract
tests/                pytest; fixtures per rule             (all)
```

Edit only your directory unless the owner says yes in chat. `shared/schema.py` changes go through A.

## Commands

```
make demo        # fresh log, decide May-Oct, score 5,000 attacks, API on :8000 (PORT= to change)
make seed        # regenerate seed data into data/seed.db
make events      # load the seed into data/events.db as facts (refuses if the log exists)
make replay M=3  # decide every request through month 3
make test        # pytest, all rules against fixtures
make fuzz N=500  # run N attacks, write scorecard to data/scorecard.json
make synthetic   # render G703 PDF/XLSX, releases, COIs, emails + truth.json into data/synthetic
make benchmark   # score perception against data/synthetic (no Claude calls)
make api         # uvicorn api.main:app --reload --port 8000
make web         # cd web && npm run dev
make device      # approval key in mock mode on :8001 (real Pi: device/README.md)
make demo-online # shared Neon log (DATABASE_URL in .env): set the old log aside, load the seed, sync Nessie, decide with Jev
make sync        # copy the Nessie ledger (owner draws, sub payments) into the log
make snapshot    # copy the shared Neon log into data/events.db; `make offline` serves it with no network
make smoke       # check every stage of a running Draw over HTTP (API=, WEB= for the deployment)
make keys        # which secrets are set in .env (never prints values)
make hooks       # once per clone: pre-commit hook that blocks secrets
```

Run `make test` before every commit. If it fails, fix it or revert; don't commit red.

## Core schema (summary; `shared/schema.py` is the truth)

- `LineItem`: item_no, description, scheduled_value, previous_applications, this_period, stored_materials, total_to_date, pct_complete, balance_to_finish, retainage
- `PayRequest`: id, sub_id, job_id, period_end, lines[LineItem], g702 totals, bank_account, submitted_at, source_doc
- `Release`: id, sub_id, job_id, kind ∈ {conditional_progress, unconditional_progress, conditional_final, unconditional_final}, through_date, amount, signed
- `LowerTierNotice`: supplier, sub_id, job_id, notice_date, released
- `COI`: sub_id, gl_expiry, wc_expiry, additional_insured
- `Vendor`: id, legal_name, ein, email_domain, bank_account, first_paid, crs_status
- `Email`: id, from_addr, from_domain, claims (bank_change?, new_account?, urgency?)
- `SiteEvidence`: job_id, date, frames[], estimates {item_no: pct}
- `Proposal`: action ∈ {release, hold, escalate}, confidences {question: float}, answers {question: choice | 0-1 score}, reasons[]
- `RuleResult`: rule, passed: bool, evidence: dict, message: str (plain English, user-facing)
- `Decision`: request_id, action, results[RuleResult], next_action, signature?, executed_at?
- `Event`: seq, ts (wall clock), at (business time), kind ∈ {fact, judgment, kernel_trace, signature, execution}, request_id?, table? (facts only), data
- `Label`: request_id, anomaly?, expected_action, rules[] (ground truth for seed/fuzz cases; never shown to the kernel)

Decision path: `pipeline.decide(state, request_id, judge=None)` cuts `state` (`events.log.state(as_of=...)`, same shape as `seed.generate.generate()`) down to a `KernelState` for one request (prior requests = ones we paid; first-paid and known bank account from the ledger), optionally runs `judgment.judge` to set `KernelState.proposal`, then calls the pure `kernel.runner.decide(KernelState, PayRequest) -> Decision`. Replay, the fuzzer and the API all go through it. Ground-truth `Label`s are never in state.

Cumulative billing: this month's payment = (total_to_date − retainage) − previously certified. Never trust the sub's `this_period`; recompute from the ledger.

## Rules (kernel/rules/)

01 cumulative continuity · 02 scheduled-value ceiling (+ approved COs) · 03 retainage math · 04 conditional release present · 05 prior unconditional received · 06 insurance current · 07 lower-tier released · 08 owner draw funded (Nessie) · 09 vendor verified (CRS) · 10 bank details unchanged · 11 sender domain authentic (homoglyph check) · 12 not a duplicate · 13 progress plausible (video vs claim, within `progress_tolerance_points`) · 14 within spend authority · 15 confidence floor (every Jev answer ≥ `escalation_confidence_threshold`)

Decision: all pass + under limit → release. Over limit (rule 14), unsure judgment (rule 15), new vendor or bank change → escalate to the approval key. Any other rule failing → hold with failing rules, evidence, and a next action.

## Jev questions (judgment/)

release_form_correct (Choice) · bank_change_suspicious (Score) · progress_plausible (Choice) · duplicate_likelihood (Score) · proposed_action (Choice). Jev does no arithmetic: compute numbers in code, pass results as state. If Jev is unreachable, `judgment/fallback.py` uses Claude structured output with the same schema. Vision estimate is Claude; Jev is text-only.

## Approval key contract (device/ Raspberry Pi, firmware/ ESP32 fallback ↔ kernel/executor.py)

Full wire contract: `docs/device-api.md`. Both devices speak it, so the executor doesn't care which is on the desk.
- Device makes a P-256 keypair on first run and keeps the private key on the device (Pi: `~/.drawkey/private.pem`, mode 600; never commit or copy it into the repo). `GET /pubkey` gives the public key; it goes in `config/contract.yaml` (`esp32_pubkey`, name kept).
- `POST /approve` body `{request_id, payee, amount, job, nonce, expires_at}` as canonical JSON. Device shows payee + amount, waits for a button, returns `{sig, nonce}` (approve) or `{rejected: true, sig}`.
- Executor verifies ECDSA over SHA-256 of the exact bytes sent, checks request id, payee and amount match what's paid, the nonce is unused (from the log's `signature` events) and not expired, then pays.
- Pi pins (BCM, `device/config.toml`): approve 17, reject 27, green LED 22, red LED 23. ESP32: approve 4, reject 5, green 16, red 17.
- Pi is reached over Tailscale; `device/README.md` has setup.

## Vocabulary

- **Pay app / pay request**: a sub's monthly invoice, as cumulative percent-complete per line (AIA G702 summary + G703 continuation sheet).
- **SOV**: schedule of values, the contract line items and amounts.
- **Retainage**: % withheld from every payment until final (usually 10%).
- **Conditional / unconditional release**: lien waiver given with the request / after payment clears.
- **Notice to owner**: a lower-tier supplier's filed claim right; they must release too.
- **Pay-when-paid**: GC pays subs only after the owner's draw funds.
- **Draw**: the GC's monthly payment from the owner/lender.
- **COI**: certificate of insurance (GL + workers' comp).

## UI copy rule (web/)

Every status is a plain sentence a 55-year-old office manager would say. Not `HOLD: RULE_07_FAIL`. "On hold: Coastal Concrete's supplier filed a notice to owner and hasn't released." Every hold has a next-action button. Raw trace lives behind "Show details" in monospace.

## Status (update at each checkpoint)

- Fri ~10 PM: kernel all 15 rules; scorecard 100% caught by rules on 10k attacks (15 types), 0 false holds; executor gates fixed; perception reads G702/G703, releases, COIs, emails, video (940/940 synthetic exact); judgment + rule 15 wired, no live model call yet (keys); Pi approval device built, untested on hardware; web/ started; no approve endpoint yet; Nessie/CRS not live (seed stands in).
- Sat 2 PM: 
- Sat 8 PM: 
- Sat 11 PM: 
- Sun 8 AM: 

## When unsure

Ask in the team chat before guessing. Prefer deleting scope to adding it. A small thing that works at 10:30 Sunday beats anything else.
