# Draw — CLAUDE.md

AI accounts-payable clerk for small general contractors. The model can only *propose* a payment; a deterministic policy kernel, a calibrated decision layer, and a hardware-held signing key decide whether money moves. Built at Cal Hacks 13.0, Oct 23–25 2026. Full spec: see `docs/spec.pdf` (or the shared Claude doc). This file is the operating contract for every human and every Claude Code session in this repo.

## Hard rules (never violate; if a task requires it, stop and ask)

1. `kernel/` is pure functions. No network, no LLM, no I/O. Input: state + proposal. Output: `list[RuleResult]`.
2. Only `kernel/executor.py` may call Nessie. Nothing else touches payment APIs.
3. A payment executes only if every `RuleResult.passed` is true AND (amount ≤ `auto_release_limit` OR a valid ESP32 signature is present).
4. Never invent fields. All data flows through `shared/schema.py`. Add a field there first, with a fixture.
5. `events/` is append-only. No updates, no deletes.
6. No secrets in the repo. Keys in `.env`, which is gitignored. Public key of the ESP32 goes in `config/contract.yaml`.
7. Every rule in `kernel/rules/` ships with one passing fixture and one failing fixture in `tests/fixtures/`.
8. Do not add sponsor SDKs beyond Claude, Jev, Nessie, CRS. Do not add a chat interface. Do not build daily job reports.

## Repo layout and owners

```
shared/schema.py      Pydantic models everyone uses        (A, frozen Fri 8:30 PM)
seed/                 fake GC, jobs, subs, G703s, anomalies (B)
synthetic/            seed rendered as PDF/XLSX/EML + truth (B)
kernel/rules/         one file per rule, rule_01..rule_15   (A)
kernel/runner.py      runs all rules, decides, writes trace (A)
kernel/executor.py    Nessie call behind signature gate     (A)
events/               SQLite append-only log + replay       (A)
perception/           Claude extraction + video estimate    (C)
judgment/             Jev questions + fallback              (C)
fuzzer/               mutators, runner, scorecard           (B)
api/                  FastAPI, SSE from event log           (A/D)
web/                  Next.js + shadcn dashboard            (D)
firmware/             ESP32 Arduino sketch                  (D)
config/contract.yaml  permissions contract (editable)       (A)
tests/                pytest; fixtures per rule             (all)
```

Edit only your directory unless the owner says yes in chat. `shared/schema.py` changes go through A.

## Commands

```
make seed        # regenerate seed data into data/seed.db
make test        # pytest, all rules against fixtures
make synthetic   # render G703 PDF/XLSX, releases, COIs, emails + truth.json into data/synthetic
make fuzz N=500  # run N mutated cases, write scorecard to data/scorecard.json
make api         # uvicorn api.main:app --reload --port 8000
make web         # cd web && npm run dev
make replay M=3  # replay the event log through month 3
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
- `Proposal`: action ∈ {release, hold, escalate}, confidences {question: float}, reasons[]
- `RuleResult`: rule, passed: bool, evidence: dict, message: str (plain English, user-facing)
- `Decision`: request_id, action, results[RuleResult], next_action, signature?, executed_at?

Cumulative billing: this month's payment = (total_to_date − retainage) − previously certified. Never trust the sub's `this_period`; recompute from the ledger.

## Rules (kernel/rules/)

01 cumulative continuity · 02 scheduled-value ceiling (+ approved COs) · 03 retainage math · 04 conditional release present · 05 prior unconditional received · 06 insurance current · 07 lower-tier released · 08 owner draw funded (Nessie) · 09 vendor verified (CRS) · 10 bank details unchanged · 11 sender domain authentic (homoglyph check) · 12 not a duplicate · 13 progress plausible (video vs claim, within `progress_tolerance_points`) · 14 within spend authority · 15 confidence floor (every Jev answer ≥ `escalation_confidence_threshold`)

Decision: all pass + under limit → release. All pass + over limit / new vendor / bank change → escalate (ESP32). Any fail → hold with failing rules, evidence, and a next action.

## Jev questions (judgment/)

release_form_correct (Choice) · bank_change_suspicious (Score) · progress_plausible (Choice) · duplicate_likelihood (Score) · proposed_action (Choice). Jev does no arithmetic: compute numbers in code, pass results as state. If Jev is unreachable, `judgment/fallback.py` uses Claude structured output with the same schema. Vision estimate is Claude; Jev is text-only.

## ESP32 contract (firmware/ ↔ kernel/executor.py)

- Device generates P-256 keypair on first boot, stores in NVS, prints pubkey to serial once.
- `POST http://<device-ip>/approve` body `{request_id, payee, amount, job, nonce, expires_at}`.
- Device shows payee + amount, waits for button. Returns `{sig, nonce}` (approve) or `{rejected: true, sig}`.
- Executor verifies ECDSA over SHA-256 of the canonical JSON payload against the pubkey in `config/contract.yaml`, checks nonce unused and not expired, then pays.
- Pins: approve GPIO 4, reject GPIO 5 (INPUT_PULLUP); green LED GPIO 16, red LED GPIO 17.

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

- Fri 11 PM: 
- Sat 2 PM: 
- Sat 8 PM: 
- Sat 11 PM: 
- Sun 8 AM: 

## When unsure

Ask in the team chat before guessing. Prefer deleting scope to adding it. A small thing that works at 10:30 Sunday beats anything else.
