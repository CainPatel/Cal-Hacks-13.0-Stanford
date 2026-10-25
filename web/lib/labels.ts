// Words for things the API names in code.

import { moneyShort, type RequestRow } from "@/lib/api";

export const ATTACK_LABELS: Record<string, string> = {
  overbill_pct: "Bills more progress than the site video shows",
  retainage_slip: "Withholds less retainage than the contract",
  missing_conditional_release: "No lien release for this month",
  missing_prior_unconditional: "Last month's final waiver never came in",
  expired_wc: "Workers' comp has expired",
  past_sov_no_co: "Bills past the contract with no approved change order",
  duplicate_new_number: "Resubmits last month's paid request",
  duplicate_this_month: "Submits this month's request twice",
  bank_change_email: "Email asks to pay a new bank account",
  bank_change_homoglyph: "New bank account, from a lookalike domain",
  supplier_notice_unreleased: "Supplier filed a notice and hasn't released",
  draw_not_cleared: "Owner's draw hasn't cleared yet",
  new_sub_ein_mismatch: "New sub whose tax ID doesn't match",
  inflated_scheduled_value: "Inflates the contract value to hide over-billing",
  inflated_payment_due: "Pads the amount due on the summary page",
  release_amount_short: "Lien release covers less than the payment",
  // held-out test set types (fuzzer/heldout.py) that aren't one of the fuzzer's own
  cumulative_billing_jump: "Last month's total jumps without a reason",
  disguised_duplicate_billing: "Resets earlier billing to zero to bill it again",
  release_short_date: "Lien release stops short of the period end",
  unsigned_conditional_waiver: "Lien release isn't signed",
  wrong_waiver_kind_final: "Final waiver sent for a progress payment",
  unconditional_waiver_amount_mismatch: "Waiver for last month covers less than we paid",
  prior_unconditional_short_date: "Last month's waiver stops short of that period",
  wrong_prior_waiver_kind: "Last month's waiver is the wrong kind",
  expired_gl_insurance: "General liability insurance has expired",
  gl_expires_on_period_end: "Liability insurance ends on the last day of the period",
  missing_additional_insured: "Insurance doesn't name us as additional insured",
  change_order_scope_duplication: "Change order bills for work already in the contract",
  billing_unapproved_co: "Bills against a change order nobody approved",
  post_period_change_order: "Leans on a change order approved after the period",
  duplicate_scope_double_billing: "Bills the same work on two lines",
  unverified_stored_materials: "Bills for stored materials nobody has seen",
  split_billing_threshold_evasion: "Splits one bill in two to stay under the limit",
  bank_change_on_pay_app: "New bank account slipped onto the pay application",
  vendor_identity_mismatch: "Sub's identity check doesn't match",
};

export const attackLabel = (key: string) =>
  key
    .split("+")
    .map((k) => ATTACK_LABELS[k] ?? k.replaceAll("_", " "))
    .join(", and ");

/** "I" -> "a capital I": how you'd say a character out loud on the phone. */
export function sayChar(c: string): string {
  if (/[A-Z]/.test(c)) return `a capital ${c}`;
  if (/[a-z]/.test(c)) return `a lowercase ${c}`;
  if (/[0-9]/.test(c)) return `the number ${c}`;
  const code = c.codePointAt(0)?.toString(16).toUpperCase().padStart(4, "0");
  return `a lookalike “${c}” (U+${code})`;
}

/**
 * The one place that decides a request's colour category (web/DESIGN.md "Status categories").
 * A hold is "fraud" if any failed rule says money is going to the wrong place or for the wrong amount,
 * else it's waiting for paperwork. Rules 14 and 15 escalate, so they never decide a hold's colour.
 */
export type Category = "fraud" | "paperwork" | "ok" | "unchecked" | "ready" | "paid";

export const RULE_CATEGORY: Record<string, "fraud" | "paperwork"> = {
  "01": "fraud", "02": "fraud", "03": "fraud", "10": "fraud", "11": "fraud", "12": "fraud", "13": "fraud",
  "04": "paperwork", "05": "paperwork", "06": "paperwork", "07": "paperwork", "08": "paperwork", "09": "paperwork",
};

export const CATEGORY: Record<Category, { label: string; short: string; chip: string; fill: string; tone: string }> = {
  fraud: { label: "Possible fraud or wrong amount", short: "Fraud or error", chip: "bg-fail-soft text-fail", fill: "bg-fail text-white", tone: "border-t-fail" },
  paperwork: { label: "Waiting for paperwork", short: "Paperwork", chip: "bg-pending-soft text-pending", fill: "bg-pending-fill hatch text-white", tone: "border-t-pending-fill" },
  ok: { label: "Needs your OK", short: "Needs OK", chip: "bg-hold-soft text-hold", fill: "bg-hold-fill text-foreground", tone: "border-t-hold-fill" },
  unchecked: { label: "Not checked", short: "Not checked", chip: "border border-dashed border-pending text-pending", fill: "border border-dashed border-pending bg-sheet text-pending", tone: "border-t-pending" },
  ready: { label: "Ready to pay", short: "Ready", chip: "bg-pass-soft text-pass", fill: "bg-pass text-white", tone: "border-t-pass" },
  paid: { label: "Paid", short: "Paid", chip: "bg-pass-soft text-pass", fill: "bg-pass text-white", tone: "border-t-pass" },
};

/** Most need help first: the order of the top bar, the board's columns and each job's list. */
export const CATEGORY_ORDER: Category[] = ["fraud", "paperwork", "ok", "unchecked", "ready", "paid"];

type Judged = Pick<RequestRow, "status" | "failed_rules" | "crs_status">;

export function category(r: Judged): Category {
  if (r.status === "paid") return "paid";
  if (r.status === "ready") return "ready";
  if (r.status === "needs_ok") return "ok";
  if (r.status === "not_checked") return "unchecked";
  const fraud = r.failed_rules.some((n) => RULE_CATEGORY[n] === "fraud" || (n === "09" && r.crs_status !== "unknown"));
  return fraud ? "fraud" : "paperwork";
}

/** Rule messages come from code: "1 line(s)" -> "1 line", "$86835.60" -> "$86,835.60". */
const SINGULAR_VERB: Record<string, string> = { claim: "claims", have: "has", "haven't": "hasn't", "don't": "doesn't", are: "is" };

export function humanize(msg: string): string {
  if (/\b1 \w+\(s\)/.test(msg)) // "1 supplier(s) ... haven't" -> "1 supplier ... hasn't"
    msg = msg.replace(/\b(claim|have|haven't|don't|are)\b/, (v) => SINGULAR_VERB[v]);
  return msg
    .replace(/\b1 (\w+)\(s\)/g, "1 $1")
    .replace(/\b(\d+) (\w+?)\(s\)/g, "$1 $2s")
    .replace(/\$(\d{4,})(\.\d+)?/g, (_, whole: string, cents = "") => `$${Number(whole).toLocaleString("en-US")}${cents}`);
}

/** The sentence beside a status chip, without repeating the chip. */
export function rowLine(status: string, sentence: string, reason: string | null): string {
  if (status === "on_hold") return humanize(sentence.replace(/^On hold:\s*/, ""));
  if (status === "needs_ok" && reason) return `${reason[0].toUpperCase()}${reason.slice(1)}.`;
  if (status === "ready") return "Every check passed.";
  return humanize(sentence);
}

/** Short plain names for the checklist ("Bank details."), keyed by rule number. */
export const RULE_NAMES: Record<string, string> = {
  "01": "Billing continuity", "02": "Contract ceiling", "03": "Retainage", "04": "This month's lien release",
  "05": "Last month's final waiver", "06": "Insurance", "07": "Supplier notices", "08": "Owner's draw",
  "09": "Vendor identity", "10": "Bank details", "11": "Email sender", "12": "Duplicate check",
  "13": "Progress against site video", "14": "Spend limit", "15": "Confidence",
};

/** "Summit Drywall LLC" -> "Summit Drywall", for sentences. */
export const shortName = (name: string) => name.replace(/,?\s+(Inc\.|LLC)$/, "");

const at = (s: string) => new Date(`${s.slice(0, 10)}T12:00:00`);
/** "Oct 22, 2026" */
export const day = (s: string) => at(s).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
/** "Oct 22" */
export const dayShort = (s: string) => at(s).toLocaleDateString("en-US", { month: "short", day: "numeric" });

/** A short phrase per failed rule, for lists (DESIGN.md "Copy"). The detail pane says the full sentence. */
export const RULE_PHRASE: Record<string, string> = {
  "01": "Billing doesn't add up", "02": "Over the contract amount", "03": "Retainage is short",
  "04": "Lien release missing", "05": "Last month's final waiver missing", "06": "Insurance missing",
  "07": "Supplier release missing", "08": "Owner's draw not funded", "09": "Identity not checked",
  "10": "Bank account changed", "11": "Lookalike email domain", "12": "Looks like a duplicate",
  "13": "Billed past the site video",
};

const identity = (crs: string) => (crs === "unknown" ? "Identity not checked" : crs === "flagged" ? "Identity flagged" : "Identity doesn't match");
const FRAUD_FIRST = ["11", "10", "12", "13", "02", "01", "03"];
const PAPERWORK_FIRST = ["07", "04", "05", "06", "08"];

/** The failed rules that decide a hold, worst first (rule 09 counts as fraud unless CRS simply hasn't checked). */
export function holdRules(r: Judged): string[] {
  const has = (n: string) => r.failed_rules.includes(n);
  const id09 = has("09") ? ["09"] : [];
  const fraud = [...FRAUD_FIRST.filter(has), ...(r.crs_status !== "unknown" ? id09 : [])];
  return [...fraud, ...PAPERWORK_FIRST.filter(has), ...(r.crs_status === "unknown" ? id09 : [])];
}

export const rulePhrase = (n: string, r: Judged) => (n === "09" ? identity(r.crs_status) : RULE_PHRASE[n]);

const WHY_PHRASE: Record<RequestRow["why"][number], (r: RequestRow) => string> = {
  over_limit: (r) => `Over your ${moneyShort(r.limit)} limit`,
  unsure: () => "Review wasn't sure",
  new_vendor: () => "New subcontractor",
  new_bank: () => "New bank account",
};

/** The list's phrase: why it's held or needs the owner. Ready, paid and not checked need none; the chip says it. */
export function phrase(r: RequestRow): string {
  if (r.status === "needs_ok") return r.why.map((w) => WHY_PHRASE[w](r)).join(", ");
  if (r.status !== "on_hold") return "";
  const rules = holdRules(r);
  if (!rules.length) return "";
  const first = rulePhrase(rules[0], r);
  return rules.length > 1 ? `${first} and ${rules.length - 1} more` : first;
}

/** What to do about each missing piece of paperwork. `kind` links to the Upload Portal with the sub picked. */
export const PAPERWORK_ACTION: Record<string, { say: string; act?: string; kind?: "release" | "coi"; does?: "verify" | "mail" }> = {
  "04": { say: "There's no signed conditional lien release for this period.", act: "Upload their lien release", kind: "release" },
  "05": { say: "Last month's unconditional waiver hasn't come in.", act: "Upload their lien release", kind: "release" },
  "06": { say: "There's no current insurance certificate on file.", act: "Upload their insurance certificate", kind: "coi" },
  "07": { say: "A supplier filed a notice to owner and hasn't released.", act: "Ask the sub for their supplier's release", does: "mail" },
  "08": { say: "Waiting for the owner's draw to fund." },
  "09": { say: "Their identity hasn't been checked yet.", act: "Check their identity", does: "verify" },
};

/** The paperwork a hold is waiting on, in the order to chase it (fraud rules are not paperwork). */
export const missingPaperwork = (r: Judged) => holdRules(r).filter((n) => n in PAPERWORK_ACTION && !(n === "09" && r.crs_status !== "unknown"));

/** Jev's questions (judgment/questions.py), in words. */
export const JEV_QUESTION: Record<string, string> = {
  release_form_correct: "Is the lien release the right form?",
  bank_change_suspicious: "Does the bank change look suspicious?",
  progress_plausible: "Does the billed progress look right?",
  duplicate_likelihood: "Is this a duplicate?",
  proposed_action: "What should happen?",
};

/**
 * Jev's answers in words. Choice answers map by name; score answers (0-1, normalized from the
 * question's five levels in judgment/questions.py) map to the nearest level, so no raw number shows.
 */
const CHOICE_ANSWER: Record<string, string> = {
  correct: "Right form", wrong_type: "Wrong kind of release", incomplete: "Missing or incomplete",
  plausible: "Looks right", overstated: "Billed more than the video shows", understated: "Billed less than the video shows",
  release: "Pay it", hold: "Hold it", escalate: "Ask the owner",
};
const SCORE_LEVELS: Record<string, string[]> = {
  bank_change_suspicious: ["No bank change", "Ordinary change", "Unusual: call to check", "Pushed or wrong sender", "Payment diversion"],
  duplicate_likelihood: ["Clearly new", "Normal overlap", "Same period, different amounts", "Nearly identical", "Exact resubmission"],
};
export function jevAnswer(question: string, value: string | number | null): string {
  if (value == null) return "No answer";
  if (typeof value === "number") {
    const levels = SCORE_LEVELS[question];
    return levels ? levels[Math.min(levels.length - 1, Math.max(0, Math.round(value * (levels.length - 1))))] : "Answered";
  }
  return CHOICE_ANSWER[value] ?? value.replaceAll("_", " ");
}
