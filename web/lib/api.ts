// Client for the Draw API (api/main.py). Every figure on screen comes from here; nothing is invented.

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type Status = "ready" | "needs_ok" | "on_hold" | "not_checked" | "paid";

export type RequestRow = {
  id: string;
  sub_id: string;
  sub: string;
  job_id: string;
  job: string;
  amount: string;
  period_end: string;
  status: Status;
  sentence: string;
  reason: string | null;
  next_action: string | null;
  /** First pay app from a sub added on the Upload screen: their SOV came from that pay app. */
  new_sub: boolean;
  new_sub_note: string | null;
  /** Rule numbers that didn't pass ("10"), the vendor's CRS status, the auto-release limit, escalation reasons. */
  failed_rules: string[];
  crs_status: string;
  limit: string;
  why: ("over_limit" | "unsure" | "new_vendor" | "new_bank")[];
};

export type EmailRow = { id: string; from_addr: string; from_domain: string; subject: string; sent_at: string; sub_id: string | null; sub: string | null };

export type VendorList = { vendors: { id: string; legal_name: string; job_ids: string[] }[]; jobs: { id: string; name: string }[] };
export type NewVendor = { legal_name: string; email_domain?: string; ein?: string; bank_account?: string };

export type RuleResult = { rule: string; passed: boolean; evidence: Record<string, unknown>; message: string };

export type Line = {
  item_no: string;
  description: string;
  scheduled_value: string;
  previous_applications: string;
  this_period: string;
  stored_materials: string;
  total_to_date: string;
  pct_complete: string;
  balance_to_finish: string;
  retainage: string;
  video_pct: string | null;
};

export type RequestDetail = RequestRow & {
  checklist: { rule: string; passed: boolean; message: string }[];
  lines: Line[];
  /** `sample`: seeded estimates, not read from a real video. */
  site_evidence: { date: string; frames: string[]; sample: boolean } | null;
  bank_account: string;
  vendor: { legal_name: string; email_domain: string };
  source_doc: string;
  /** True when the lines came from the seed, not from reading an uploaded file. */
  sample_document: boolean;
  /** The uploaded original's stored name, for GET /files (behind the upload passcode). */
  original: string | null;
  trace: { action: string; results: RuleResult[]; next_action: string | null } | null;
};

/** data/calibration.json from `python -m fuzzer.calibration` (report()). */
export type Calibration = {
  answers: number;
  curve: { bins: { bin: string; n: number; mean_confidence: number; accuracy: number }[]; expected_calibration_error: number | null };
};

export type SweepPoint = {
  limit: number;
  button_presses_per_job_month: number;
  auto_released_share: number;
  attacks_auto_paid: number;
  dollars_auto_paid: string;
};

export type Scorecard = {
  kernel: string;
  cases: number;
  mutated: number;
  clean: number;
  wrongful_if_every_button_approved: number;
  rule_catch_rate: number | null;
  wrongful_releases: number;
  false_hold_rate: number | null;
  dollars_protected: string;
  dollars_delayed: string;
  button_presses_per_job_month: number;
  auto_released_share: number;
  by_anomaly: Record<string, { n: number; caught: number; wrongful: number; right_rule: number }>;
  limit_sweep: SweepPoint[];
  misses: { case: number; request_id: string; anomaly: string | null; action: string; amount: string }[];
};

export type AttackResult = {
  label: { request_id: string; anomaly: string; rules: string[] };
  decision: { action: string; results: RuleResult[] };
  amount: string;
  caught: boolean;
  caught_by_rules: boolean;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    const deployed = typeof window !== "undefined" && !/^(localhost|127\.0\.0\.1)$/.test(window.location.hostname);
    if (deployed && /localhost|127\.0\.0\.1/.test(API_URL))
      throw new ApiError(0, "This dashboard was built without NEXT_PUBLIC_API_URL, so it's looking for the API on your own computer. Set it to the API's address on Vercel and redeploy.");
    throw new ApiError(0, `Can't reach the Draw server at ${API_URL}. Is it running (make api)? If it is, check that its CORS_ORIGINS includes this page's address.`);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : `Request failed (${res.status}).`);
  return body as T;
}

export type DocKind = "pay_app" | "release" | "coi" | "email" | "site_video";

export type IngestResult = {
  request_id?: string;
  release_id?: string;
  coi_id?: string;
  email_id?: string;
  site_evidence_id?: string;
  method?: string;
  fallback_reason?: string | null;
  notes?: { code: string; message: string; item_no: string | null }[];
  pay_request?: { number: number; period_end: string; g702: { current_payment_due: string } };
  /** Email only: what happened to each PDF attached to it. */
  attachments?: { filename: string; kind: string | null; status: "recorded" | "used" | "needs_person" | "duplicate" | "unreadable"; detail?: string; sub?: string }[];
};

const DIRECT: Record<DocKind, { path: string; fields: ("sub_id" | "job_id")[] }> = {
  pay_app: { path: "/pay-apps", fields: ["sub_id", "job_id"] },
  release: { path: "/releases", fields: ["sub_id", "job_id"] },
  coi: { path: "/cois", fields: ["sub_id"] },
  email: { path: "/emails", fields: [] },
  site_video: { path: "/site-video", fields: ["job_id"] },
};

export const api = {
  requests: (month: number) => call<RequestRow[]>(`/requests?month=${month}`),
  request: (id: string) => call<RequestDetail>(`/requests/${encodeURIComponent(id)}`),
  approve: (id: string) =>
    call<{ paid: boolean; rejected: boolean; reason?: string }>(`/requests/${encodeURIComponent(id)}/approve`, { method: "POST" }),
  /** After a client upload to Blob: the API pulls the file from the private store and reads it. */
  ingest: (kind: DocKind, pathname: string, ids: { sub_id?: string; job_id?: string }) =>
    call<IngestResult>("/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, pathname, ...ids }),
    }),
  /** No Blob store (local): the file goes to the API directly. */
  uploadDirect: (kind: DocKind, file: File, ids: { sub_id?: string; job_id?: string }) => {
    const form = new FormData();
    form.set("file", file);
    for (const f of DIRECT[kind].fields) if (ids[f]) form.set(f, ids[f] as string);
    return call<IngestResult>(DIRECT[kind].path, { method: "POST", body: form });
  },
  vendors: () => call<VendorList>("/vendors"),
  emails: (month: number) => call<EmailRow[]>(`/emails?month=${month}`),
  /** The original upload as a blob URL; the passcode goes in a header, never the URL. */
  openOriginal: async (name: string, passcode: string) => {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/files/${encodeURIComponent(name)}`, { headers: { "X-Upload-Passcode": passcode }, cache: "no-store" });
    } catch {
      throw new ApiError(0, `Can't reach the Draw server at ${API_URL}.`);
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : `Couldn't open the original (${res.status}).`);
    }
    return URL.createObjectURL(await res.blob());
  },
  createVendor: (body: NewVendor, passcode: string) =>
    call<{ id: string; legal_name: string }>("/vendors", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Upload-Passcode": passcode },
      body: JSON.stringify(body),
    }),
  verifyVendor: (id: string) => call<{ crs_status: string; reasons: string[] }>(`/vendors/${encodeURIComponent(id)}/verify`, { method: "POST" }),
  replay: (through: number) => call<{ new_decisions: number }>(`/replay?through=${through}`, { method: "POST" }),
  scorecard: () => call<Scorecard>("/scorecard"),
  calibration: () => call<Calibration>("/calibration"),
  attacks: () => call<{ name: string; about: string }[]>("/attacks"),
  attack: (name: string, target: string) =>
    call<AttackResult>("/attacks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, target }) }),
  describe: (text: string) =>
    call<{ plan: { attack: string; target: string; explanation: string }; result?: null } & Partial<AttackResult>>("/attacks/describe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
};

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const money = (v: string | number) => usd.format(Number(v));
export const moneyShort = (v: string | number) => usd0.format(Number(v));

export const MONTHS = ["May", "June", "July", "August", "September", "October"]; // month 1..6 of 2026
export const monthName = (m: number) => `${MONTHS[m - 1]} 2026`;

// Rule ids are "13_progress_plausible": a person reads the words.
export const ruleNumber = (rule: string) => rule.slice(0, 2);
