# Kernel input/output contract (rules 01-15, all shipped)

Source of truth is [`shared/schema.py`](../shared/schema.py) and [`kernel/state.py`](../kernel/state.py); this table is a snapshot of it.

## Inputs — what the rest of the codebase must produce

| Object | Field | Type | Produced by | Used by rule(s) |
|---|---|---|---|---|
| `PayRequest` | `id` | `str` | extraction (G703/PDF) | 04, 05 |
| `PayRequest` | `number` | `int` | extraction | 05 |
| `PayRequest` | `sub_id` | `str` | extraction | 04, 05, 06, 07 |
| `PayRequest` | `job_id` | `str` | extraction | 04, 05, 07 |
| `PayRequest` | `period_end` | `date` | extraction | 03, 04, 05, 06 |
| `PayRequest.lines[].item_no` | | `str` | extraction | 01, 02, 03 |
| `PayRequest.lines[].previous_applications` | | `Decimal` | extraction (not trusted as-is) | 01 |
| `PayRequest.lines[].total_to_date` | | `Decimal` | extraction | 01, 02, 03 |
| `PayRequest.lines[].scheduled_value` | | `Decimal` | extraction (not trusted as ceiling) | — (recorded only; rule 02 ignores it) |
| `PayRequest.lines[].retainage` | | `Decimal` | extraction | 03 |
| `PayRequest.g702.retainage` | | `Decimal` | extraction | 03 |
| `PayRequest.g702.total_completed_stored` | | `Decimal` | extraction | 03 |
| `Subcontract.sov[].item_no` | | `str` | contract (one-time, not per request) | 02 |
| `Subcontract.sov[].scheduled_value` | | `Decimal` | contract | 02 (authoritative ceiling) |
| `Subcontract.retainage_rate` | | `Decimal` | contract | 03 |
| `ChangeOrder.item_no`, `.amount`, `.approved` | | `str`/`Decimal`/`bool` | contract admin | 02 |
| `Release.sub_id`, `.job_id`, `.kind`, `.through_date`, `.signed` | | mixed | extraction (lien waiver doc) | 04, 05 |
| `COI.gl_expiry`, `.wc_expiry`, `.additional_insured` | | `date`/`date`/`bool` | extraction (insurance cert) | 06 |
| `LowerTierNotice.sub_id`, `.job_id`, `.released` | | `str`/`str`/`bool` | extraction (notice-to-owner doc) | 07 |
| `LedgerEntry.kind`, `.request_id`, `.sub_id` | | mixed | Nessie (not extraction) | 05, 08 |
| `LedgerEntry.job_id`, `.period_end` (kind=`owner_draw`) | | `str`/`date` | Nessie | 08 |
| `Job.pay_when_paid` | | `bool` | contract admin | 08 |
| `Vendor.crs_status` | | `str` | CRS sandbox | 09 |
| `Vendor.bank_account` | | `str` | vendor onboarding; updated by a human after an out-of-band verified change | 10 |
| `PayRequest.bank_account` | | `str` | extraction | 10 |
| `Email.from_domain` | | `str` | extraction (mail) | 11 |
| `Vendor.email_domain` | | `str` | vendor onboarding | 11 |
| `PayRequest` (other submissions, `KernelState.all_requests`) | `.period_end`, `.lines[].total_to_date`, `.number` | mixed | extraction | 12 |
| `SiteEvidence.estimates` | `{item_no: pct}` | `dict[str, Decimal]` | perception (video) | 13 |
| `LineItem.pct_complete` | | `Decimal` | extraction | 13 |
| `KernelState.contract` | `auto_release_limit_usd`, `escalation_confidence_threshold`, `progress_tolerance_points` | `dict` | `config/contract.yaml` | 06, 13, 14, 15 |
| `Proposal.confidences` | `{question_id: float}` | `dict[str, float]` | judgment/judge.py | 15 |

`KernelState.known_bank_account` (bank account of the last *successful payment*, not the vendor's account of record) isn't a rule input — it only drives `runner.py`'s separate escalation check: a verified bank change (rule 10 passes) still forces a physical tap if it differs from the account last actually paid.

All objects are frozen, `extra="forbid"` pydantic models — unknown fields raise instead of being dropped; money is always `Decimal`; dates are `date`/`datetime`, not strings.

## Outputs — what the kernel returns

### Per rule: `RuleResult`

| Field | Type | Meaning |
|---|---|---|
| `rule` | `str` | e.g. `"02_scheduled_value_ceiling"` |
| `passed` | `bool` | |
| `evidence` | `dict` | rule-specific; see each rule file for exact keys |
| `message` | `str` | plain-English, user-facing |

### Per request: `Decision` (from `kernel/runner.py::decide`)

| Field | Type | Meaning |
|---|---|---|
| `request_id` | `str` | |
| `action` | `Action` | `release` \| `hold` \| `escalate` |
| `results` | `list[RuleResult]` | every rule's result, in order |
| `next_action` | `str \| None` | set only when `action == hold` |
| `signature` | `str \| None` | filled in later by `kernel/executor.py`, not by `decide()` |
| `executed_at` | `datetime \| None` | filled in later by `kernel/executor.py`, not by `decide()` |

Decision logic: a failure in any rule *except* 14 (`within_spend_authority`) or 15 (`confidence_floor`) → `hold`. Otherwise, a failure in 14 or 15, or a new vendor (`Vendor.first_paid is None`), or a bank change vs. the last *paid* account (`known_bank_account`) → `escalate`. Everything else → `release`. Rules 14/15 failing never sets `next_action` — that field is for holds only.
