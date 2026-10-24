// Words for things the API names in code.

import type { RequestRow } from "@/lib/api";

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

export const CATEGORY: Record<Category, { label: string; chip: string; fill: string; tone: string }> = {
  fraud: { label: "Possible fraud or wrong amount", chip: "bg-fail-soft text-fail", fill: "bg-fail text-white", tone: "border-t-fail" },
  paperwork: { label: "Waiting for paperwork", chip: "bg-pending-soft text-pending", fill: "bg-pending-fill hatch text-white", tone: "border-t-pending-fill" },
  ok: { label: "Needs your OK", chip: "bg-hold-soft text-hold", fill: "bg-hold-fill text-foreground", tone: "border-t-hold-fill" },
  unchecked: { label: "Not checked", chip: "border border-dashed border-pending text-pending", fill: "border border-dashed border-pending bg-sheet text-pending", tone: "border-t-pending" },
  ready: { label: "Ready to pay", chip: "bg-pass-soft text-pass", fill: "bg-pass text-white", tone: "border-t-pass" },
  paid: { label: "Paid", chip: "bg-pass-soft text-pass", fill: "bg-pass text-white", tone: "border-t-pass" },
};

/** Most need help first: the order of the top bar, the board's columns and each job's list. */
export const CATEGORY_ORDER: Category[] = ["fraud", "paperwork", "ok", "unchecked", "ready", "paid"];

export function category(r: Pick<RequestRow, "status" | "failed_rules" | "crs_status">): Category {
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
