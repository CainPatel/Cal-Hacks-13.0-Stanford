"use client";

import { Fragment, useState } from "react";
import { cn } from "@/lib/utils";

type ServiceId = "postmark" | "vercel" | "blob" | "neon" | "claude" | "jev" | "nessie" | "crs" | "pi";

type Service = { id: ServiceId; name: string; role: string };

/** Everything outside Draw's own code that a pay application touches on its way to the bank. */
const SERVICES: Service[] = [
  { id: "postmark", name: "Postmark", role: "Inbound email, delivered as a webhook" },
  { id: "vercel", name: "Vercel", role: "Hosts draw-api (FastAPI) and draw-web (Next.js)" },
  { id: "blob", name: "Vercel Blob", role: "Private store for the original files" },
  { id: "neon", name: "Neon Postgres", role: "The append-only event log" },
  { id: "claude", name: "Anthropic Claude", role: "Scans, site video, email flags, Jev’s fallback" },
  { id: "jev", name: "TypeSafe Jev", role: "Five typed questions, each with a confidence" },
  { id: "nessie", name: "Capital One Nessie", role: "Sandbox bank: owner draws and sub payments" },
  { id: "crs", name: "CRS", role: "Vendor identity (TransUnion, LexisNexis)" },
  { id: "pi", name: "Raspberry Pi key", role: "Signs approvals; reached over Tailscale" },
];

type Call = { method?: string; target: string; note: string };

type Stage = {
  id: string;
  n: string; // "1".."9", or "0" for what's on file before
  actor: string;
  ai: string;
  title: string;
  summary: string;
  calls: Call[];
  code: string[];
  libraries?: string[];
  writes?: string;
  uses: ServiceId[];
  outcomes?: { word: string; tone: string; next: string }[];
};

const STAGES: Stage[] = [
  {
    id: "before",
    n: "0",
    actor: "Earlier uploads",
    ai: "Claude reads video",
    title: "Already on file",
    summary: "The contract, insurance, lien releases and the super’s site video come in before the pay application does.",
    calls: [
      { method: "POST", target: "/site-video", note: "frames sampled one every 3 s (10 to 30), Claude estimates each line’s progress" },
      { method: "POST", target: "/releases", note: "lien releases" },
      { method: "POST", target: "/cois", note: "certificates of insurance" },
    ],
    code: ["perception/video.py", "perception/release.py", "perception/coi.py"],
    libraries: ["PyAV", "Pillow", "pdfplumber"],
    writes: "fact (site_evidence, releases, cois)",
    uses: ["vercel", "blob", "claude", "neon"],
  },
  {
    id: "email",
    n: "1",
    actor: "The sub, Postmark",
    ai: "No AI needed",
    title: "The pay application arrives",
    summary: "The sub emails its G702 and G703. Each PDF is named by its printed form title, and filed under a sub only when the sender’s domain matches exactly.",
    calls: [
      { method: "POST", target: "/inbound/postmark", note: "Postmark’s webhook, basic auth" },
      { method: "POST", target: "/emails", note: "the same handler as an uploaded .eml" },
      { method: "POST", target: "/pay-apps", note: "or from the Upload Portal (big files via Blob and /ingest)" },
    ],
    code: ["api/main.py", "perception/mail.py", "perception/attachments.py"],
    writes: "fact (emails)",
    uses: ["postmark", "vercel", "blob", "neon"],
  },
  {
    id: "read",
    n: "2",
    actor: "Perception",
    ai: "Claude only for scans",
    title: "Draw reads it",
    summary: "Text layer first: only glyphs that leave ink count, lines are read from the table or by column position, and the arithmetic is checked and reported, never corrected.",
    calls: [
      { target: "extract_pay_request()", note: "G703 lines, G702 totals, period, remit-to account" },
      { target: "check_consistency(), check_g702()", note: "D + E + F = G, C − G = H, G702 against the lines" },
      { target: "Anthropic Messages API", note: "only when the text layer is missing or can’t be trusted" },
    ],
    code: ["perception/g703.py", "perception/common.py", "perception/numbers.py"],
    libraries: ["pdfplumber", "numpy", "Pillow"],
    writes: "fact (pay_requests)",
    uses: ["vercel", "claude", "neon"],
  },
  {
    id: "log",
    n: "3",
    actor: "Event log",
    ai: "No AI",
    title: "It’s written to the log",
    summary: "Every fact is a new row. Database triggers refuse updates, deletes and truncation, and the state at any moment is a fold over the facts known by then.",
    calls: [
      { target: "events.append(kind, data)", note: "one row per fact, judgment, trace, signature or payment" },
      { target: "events.state(as_of)", note: "the world as it was known at a moment in business time" },
    ],
    code: ["events/log.py"],
    libraries: ["psycopg"],
    writes: "every kind",
    uses: ["neon"],
  },
  {
    id: "check",
    n: "4",
    actor: "You, the API",
    ai: "No AI",
    title: "You press “Check October”",
    summary: "The API copies the bank’s latest ledger into the log, then decides every pay request that hasn’t been decided yet.",
    calls: [
      { method: "POST", target: "/replay?through=6", note: "the dashboard’s Check button" },
      { method: "GET", target: "/accounts/{id}/deposits", note: "Nessie: which owner’s draws have cleared" },
      { method: "GET", target: "/accounts/{id}/bills", note: "Nessie: payments already made, by any copy of Draw" },
      { target: "pipeline.decide()", note: "one request’s slice of the world, then judgment, then the rules" },
    ],
    code: ["api/main.py", "events/replay.py", "pipeline.py"],
    writes: "fact (ledger)",
    uses: ["vercel", "nessie", "neon"],
  },
  {
    id: "review",
    n: "5",
    actor: "Judgment",
    ai: "AI: Jev, Claude as fallback",
    title: "Jev reviews it",
    summary: "All the arithmetic is done in code first, then Jev answers five typed questions with a confidence each. Jev only answers; it can’t decide or pay.",
    calls: [
      { target: "compute_facts()", note: "every number Jev sees, computed in code, under 24 KiB" },
      { method: "POST", target: "{JEV_BASE_URL}/v1/systemone", note: "release form, bank change, progress, duplicate, proposed action" },
      { target: "Anthropic Messages API", note: "structured output with the same questions, if Jev is unreachable" },
    ],
    code: ["judgment/facts.py", "judgment/jev.py", "judgment/fallback.py", "judgment/judge.py"],
    writes: "judgment",
    uses: ["jev", "claude", "neon"],
  },
  {
    id: "rules",
    n: "6",
    actor: "Kernel",
    ai: "No AI",
    title: "15 rules decide",
    summary: "Pure functions with no network and no model: the same inputs always give the same decision. Rule 15 checks Jev’s confidence; rule 9 reads the CRS identity check run earlier.",
    calls: [
      { target: "kernel.runner.decide(state, request)", note: "runs rules 01 to 15, returns release, escalate or hold" },
      { target: "config/contract.yaml", note: "auto-release limit $25,000, confidence floor, 10-point progress tolerance" },
      { method: "POST", target: "/vendors/{id}/verify", note: "CRS identity check, run on demand before this" },
    ],
    code: ["kernel/runner.py", "kernel/rules/rule_01 … rule_15", "kernel/state.py"],
    writes: "kernel_trace",
    uses: ["crs", "neon"],
    outcomes: [
      { word: "Ready to pay", tone: "text-pass", next: "step 8, from the dashboard" },
      { word: "Needs your OK", tone: "text-hold", next: "step 7, the approval key" },
      { word: "On hold", tone: "text-fail", next: "stops here, with the proof and next step" },
    ],
  },
  {
    id: "sign",
    n: "7",
    actor: "You, the Pi",
    ai: "No AI",
    title: "Your key signs it",
    summary: "Only for “Needs your OK”. The Pi shows the payee and amount and waits for a button. Its private key never leaves the device.",
    calls: [
      { method: "POST", target: "/requests/{id}/approve", note: "the Approve tab’s button" },
      { method: "POST", target: "{DEVICE_URL}/approve", note: "{request_id, payee, amount, job, nonce, expires_at} as canonical JSON" },
      { target: "GPIO 17 (green) or 27 (red)", note: "ECDSA P-256 over SHA-256 of the exact bytes, returned as {sig, nonce}" },
    ],
    code: ["device/main.py", "kernel/executor.py"],
    libraries: ["gpiozero", "cryptography"],
    writes: "signature",
    uses: ["pi", "vercel", "neon"],
  },
  {
    id: "pay",
    n: "8",
    actor: "Executor",
    ai: "No AI",
    title: "The bank pays",
    summary: "The only module that can call the bank. It checks the signature, the nonce and every rule result, asks the bank whether it was already paid, and only then pays, one payment at a time.",
    calls: [
      { method: "POST", target: "/requests/{id}/pay", note: "ready to pay, with {\"confirm\": true}; or after the key signs" },
      { target: "verify_signature()", note: "device public key from config/contract.yaml; payee, amount and account must match" },
      { method: "GET", target: "/accounts/{id}/bills", note: "Nessie: refuse if this sub, job and period is already paid" },
      { method: "POST", target: "/accounts/{id}/bills", note: "Nessie: the payment, under a Postgres advisory lock" },
    ],
    code: ["kernel/executor.py"],
    libraries: ["cryptography", "requests"],
    writes: "execution",
    uses: ["nessie", "neon", "vercel"],
  },
  {
    id: "live",
    n: "9",
    actor: "Dashboard",
    ai: "No AI",
    title: "Every screen updates",
    summary: "Open pages check the log every 10 seconds and refetch only when something new was written.",
    calls: [
      { method: "GET", target: "/events", note: "server-sent events; on Vercel the stream closes and retries in 10 s" },
      { method: "GET", target: "/requests?month=6, /requests/{id}, /log", note: "refetched in place, no reload" },
    ],
    code: ["web/hooks/use-live.ts", "web/lib/api.ts"],
    uses: ["vercel", "neon"],
  },
];

const NO_AI_FROM = "rules"; // the line where AI stops is drawn above this stage

type Active = { kind: "stage" | "service"; id: string } | null;

const FOCUS = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
const SELECTED = "bg-selected shadow-[inset_4px_0_0_var(--primary)]";
const FADE = "transition-opacity duration-150 motion-reduce:transition-none";

/**
 * The path of one pay application as a vertical timeline, with what each step calls. Hover or focus
 * a step to light up the services it uses; hover a service to light up every step that uses it.
 * A tap (or Enter) pins the selection, so it works on a phone too.
 */
export function Pipeline() {
  const [hover, setHover] = useState<Active>(null);
  const [pinned, setPinned] = useState<Active>(null);
  const active = hover ?? pinned;

  const stageLit = (s: Stage) =>
    active?.kind === "stage" ? active.id === s.id : active?.kind === "service" ? s.uses.includes(active.id as ServiceId) : false;
  const serviceLit = (v: Service) =>
    active?.kind === "service"
      ? active.id === v.id
      : active?.kind === "stage"
        ? STAGES.find((s) => s.id === active.id)!.uses.includes(v.id)
        : false;
  const toggle = (a: NonNullable<Active>) => setPinned((p) => (p?.kind === a.kind && p.id === a.id ? null : a));

  return (
    <div
      className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]"
      onKeyDown={(e) => e.key === "Escape" && setPinned(null)}
    >
      <ol className="rounded-lg border bg-sheet py-2" aria-label="Steps">
        {STAGES.map((s, i) => {
          const lit = stageLit(s);
          const last = i === STAGES.length - 1;
          return (
            <Fragment key={s.id}>
              {s.id === NO_AI_FROM && (
                <li className="mx-4 my-2 border-t-[3px] border-double border-foreground pt-3 pb-1 text-body font-semibold sm:mx-6 md:pl-[12.5rem]">
                  From here on, no AI: rules, your key and the bank.
                </li>
              )}
              <li
                onMouseEnter={() => setHover({ kind: "stage", id: s.id })}
                onMouseLeave={() => setHover(null)}
                className={cn(
                  "relative grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-4 px-4 py-5 sm:px-6 md:grid-cols-[10rem_2.5rem_minmax(0,1fr)]",
                  FADE,
                  lit && SELECTED,
                  active && !lit && "opacity-50",
                )}
              >
                <div className="hidden pt-2 md:block">
                  <p className="text-meta font-semibold">{s.actor}</p>
                  <p className="text-meta text-muted-foreground">{s.ai}</p>
                </div>
                <div className="relative flex justify-center">
                  {!last && <span aria-hidden className="absolute top-11 -bottom-5 w-px bg-border-strong" />}
                  <span
                    aria-hidden
                    className={cn(
                      "relative flex size-10 items-center justify-center rounded-full border-2 border-foreground font-mono font-semibold tnum",
                      s.id === NO_AI_FROM ? "bg-foreground text-background" : "bg-sheet",
                    )}
                  >
                    {s.n}
                  </span>
                </div>
                <div className="min-w-0 pt-1">
                  <p className="text-meta text-muted-foreground md:hidden">
                    {s.actor}. {s.ai}
                  </p>
                  <h3>
                    <button
                      type="button"
                      onFocus={() => setHover({ kind: "stage", id: s.id })}
                      onBlur={() => setHover(null)}
                      onClick={() => toggle({ kind: "stage", id: s.id })}
                      aria-pressed={pinned?.kind === "stage" && pinned.id === s.id}
                      className={cn("rounded-sm text-left text-body font-semibold", FOCUS)}
                    >
                      {s.title}
                    </button>
                  </h3>
                  <p className="mt-1 max-w-[64ch] text-body">{s.summary}</p>
                  <ul className="mt-3 space-y-1.5" aria-label="Calls">
                    {s.calls.map((c) => (
                      <li key={`${c.method ?? ""} ${c.target}`} className="text-meta">
                        <code className="font-mono">
                          {c.method && <span className="font-semibold">{c.method} </span>}
                          {c.target}
                        </code>
                        <span className="text-muted-foreground">: {c.note}</span>
                      </li>
                    ))}
                  </ul>
                  {s.outcomes && (
                    <ul className="mt-3 grid gap-2 sm:grid-cols-3" aria-label="Outcomes">
                      {s.outcomes.map((o) => (
                        <li key={o.word} className="rounded-lg border bg-sheet px-3 py-2 text-meta">
                          <span className={cn("font-semibold", o.tone)}>{o.word}</span>
                          <span className="block text-muted-foreground">Then {o.next}.</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <dl className="mt-3 grid gap-x-3 gap-y-0.5 text-meta sm:grid-cols-[auto_minmax(0,1fr)]">
                    <dt className="text-muted-foreground">Code</dt>
                    <dd className="font-mono">{s.code.join(", ")}</dd>
                    {s.libraries && (
                      <>
                        <dt className="text-muted-foreground">Libraries</dt>
                        <dd>{s.libraries.join(", ")}</dd>
                      </>
                    )}
                    {s.writes && (
                      <>
                        <dt className="text-muted-foreground">Writes</dt>
                        <dd className="font-mono">{s.writes}</dd>
                      </>
                    )}
                    <dt className="text-muted-foreground">Uses</dt>
                    <dd>{s.uses.map((u) => SERVICES.find((v) => v.id === u)!.name).join(", ")}</dd>
                  </dl>
                </div>
              </li>
            </Fragment>
          );
        })}
      </ol>

      <aside aria-labelledby="services" className="lg:sticky lg:top-24">
        <h2 id="services" className="text-[22px] font-semibold tracking-[-0.01em]">
          Services
        </h2>
        <p className="mt-1 text-meta text-muted-foreground">
          Hover or tap one to see every step that uses it. Hover a step to see what it calls.
        </p>
        <ul className="mt-4 space-y-2">
          {SERVICES.map((v) => {
            const lit = serviceLit(v);
            return (
              <li key={v.id}>
                <button
                  type="button"
                  onMouseEnter={() => setHover({ kind: "service", id: v.id })}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover({ kind: "service", id: v.id })}
                  onBlur={() => setHover(null)}
                  onClick={() => toggle({ kind: "service", id: v.id })}
                  aria-pressed={pinned?.kind === "service" && pinned.id === v.id}
                  className={cn(
                    "flex min-h-12 w-full flex-col rounded-lg border bg-sheet px-3 py-2 text-left",
                    FOCUS,
                    FADE,
                    lit && SELECTED,
                    active && !lit && "opacity-50",
                  )}
                >
                  <span className="font-semibold">{v.name}</span>
                  <span className="text-meta text-muted-foreground">{v.role}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </aside>
    </div>
  );
}
