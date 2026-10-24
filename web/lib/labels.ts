// Words for things the API names in code.

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

export const STATUS_LABEL = {
  ready: "Ready",
  needs_ok: "Needs your OK",
  on_hold: "On hold",
  not_checked: "Not checked",
} as const;

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
