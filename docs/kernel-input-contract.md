# Kernel input/output contract (rules 01-07)

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
| `LedgerEntry.kind`, `.request_id`, `.sub_id` | | mixed | Nessie (not extraction) | 05 |
| `KernelState.contract` | `auto_release_limit_usd` etc. | `dict` | `config/contract.yaml` | runner (not a rule) |

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

Decision logic: any `RuleResult.passed == False` → `hold`. All pass + over `auto_release_limit_usd` or new vendor or bank change → `escalate`. All pass otherwise → `release`.
