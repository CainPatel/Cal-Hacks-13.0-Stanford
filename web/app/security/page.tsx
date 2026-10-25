"use client";

import { ChevronDown } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Mark } from "@/components/evidence";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, money, type Assessment, type AttackResult, type AttackTarget, type Scorecard } from "@/lib/api";
import { attackLabel, humanize, RULE_NAMES } from "@/lib/labels";
import { cn } from "@/lib/utils";

const pct = (x: number | null | undefined, digits = 1) => (x == null ? "n/a" : `${(x * 100).toFixed(digits)}%`);
const n = (x: number) => x.toLocaleString("en-US");
const dollars = (x: number) => money(x).replace(/\.00$/, "");

const FOCUS = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
const FIELD = cn("min-h-12 w-full rounded-md border border-border-strong bg-background px-3 py-2.5 text-[17px]", FOCUS);
const TH = "align-bottom border-b-2 border-foreground px-2 py-2 text-left text-meta font-medium text-muted-foreground sm:px-3";
const TD = "border-b px-2 py-[11px] sm:px-3";
const NUM = "text-right font-mono tnum";

export default function SecurityPage() {
  const { version } = useLive();
  const [card, setCard] = useState<Scorecard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.scorecard().then(
      (c) => (setCard(c), setError(null)),
      (e: ApiError) => setError(e.status === 404 ? "No scorecard yet. Run `make fuzz N=5000` on the server, then reload." : e.message),
    );
  }, [version]);

  return (
    <main className="mx-auto w-full max-w-[1164px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <h1 className="text-[27px] leading-tight font-semibold tracking-[-0.02em] sm:text-lede">How often can Draw be robbed?</h1>
      {error && <p className="mt-4 text-body text-fail">{error}</p>}
      {!card && !error && <Skeleton className="mt-4 h-24 w-full max-w-[62ch]" />}
      {card && <Headline card={card} />}

      <AttackConsole />
      {card && card.limit_sweep?.length > 0 && <LimitDial card={card} />}
      {card && <AttackLedger card={card} />}

      {card && (
        <p className="mt-14 font-mono text-sm text-muted-foreground">
          {card.kernel}, {n(card.cases)} cases from make fuzz.
        </p>
      )}
    </main>
  );
}

function Section({ id, title, intro, children }: { id: string; title: string; intro?: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-14">
      <h2 id={id} className="text-[22px] font-semibold tracking-[-0.01em]">
        {title}
      </h2>
      {intro && <p className="mt-2 max-w-[64ch] text-body text-muted-foreground">{intro}</p>}
      <div className="mt-5">{children}</div>
    </section>
  );
}

function Headline({ card }: { card: Scorecard }) {
  const stopped = card.mutated - card.wrongful_if_every_button_approved;
  // numbers inside a sentence stay in the text face: Geist Mono gives a comma a full-width cell, so "$86,268,768.41" reads as "$86 , 268 , 768"
  const b = "font-semibold tnum";
  return (
    <>
      <p className="mt-4 max-w-[62ch] text-say leading-[1.55]">
        We sent <b className={b}>{n(card.mutated)}</b> forged or broken pay requests through the real pipeline. The rules stopped{" "}
        <b className={b}>{pct(card.rule_catch_rate)}</b> of them on their own ({n(stopped)}). <b className={b}>{n(card.wrongful_if_every_button_approved)}</b>{" "}
        would have been paid even if the owner tapped approve without reading, and <b className={b}>{n(card.wrongful_releases)}</b> went out with no tap
        at all. Of {n(card.clean)} honest requests, <b className={b}>{pct(card.false_hold_rate)}</b> were held by mistake.
      </p>
      <p className="mt-3 max-w-[64ch] text-body text-muted-foreground">
        That stopped <span className="tnum">{money(card.dollars_protected)}</span> in bad payments and held up{" "}
        <span className="tnum">{money(card.dollars_delayed)}</span> of good ones.
        {card.misses.length === 0 && " No attack got through in this run."}
      </p>
    </>
  );
}

function AttackLedger({ card }: { card: Scorecard }) {
  const rows = useMemo(() => {
    const singles = Object.entries(card.by_anomaly).filter(([k]) => !k.includes("+"));
    const combos = Object.entries(card.by_anomaly).filter(([k]) => k.includes("+"));
    const sum = (key: "n" | "caught" | "wrongful" | "right_rule") => combos.reduce((s, [, v]) => s + v[key], 0);
    const list = singles.map(([k, v]) => ({ key: k, label: attackLabel(k), ...v }));
    if (combos.length) list.push({ key: "combo", label: "Two of these at once", n: sum("n"), caught: sum("caught"), wrongful: sum("wrongful"), right_rule: sum("right_rule") });
    return list.sort((a, b) => b.wrongful - a.wrongful || a.label.localeCompare(b.label));
  }, [card]);
  const total = (key: "n" | "wrongful" | "right_rule") => rows.reduce((s, r) => s + r[key], 0);

  return (
    // closed by default so the console stays the main event; the numbers are one tap away
    <details className="group mt-14">
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 rounded-sm outline-none select-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <h2 id="ledger" className="text-[22px] font-semibold tracking-[-0.01em]">
          Every kind of attack we tried
        </h2>
        <ChevronDown className="size-5 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden />
      </summary>
      <div className="mt-5">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[15px] sm:text-[17px]">
          <thead>
            <tr>
              <th className={cn(TH, "w-11")}>
                <span className="sr-only">Caught</span>
              </th>
              <th className={TH}>Attack</th>
              <th className={cn(TH, "text-right")}>Tried</th>
              <th className={cn(TH, "text-right")}>Caught by the right rule</th>
              <th className={cn(TH, "text-right")}>Paid with no tap</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const mark = r.wrongful > 0 ? "fail" : r.right_rule === r.n ? "pass" : null;
              return (
                <tr key={r.key}>
                  <td className="w-11 border-r border-b border-r-border-strong py-2 text-center align-middle">
                    {mark && (
                      <span className="inline-flex">
                        <Mark kind={mark} />
                      </span>
                    )}
                    <span className="sr-only">{r.wrongful > 0 ? "Got through" : mark ? "Caught" : "Caught, not always by the right rule"}</span>
                  </td>
                  <td className={TD}>{r.label}</td>
                  <td className={cn(TD, NUM)}>{n(r.n)}</td>
                  <td className={cn(TD, NUM)}>{n(r.right_rule)}</td>
                  <td className={cn(TD, NUM, r.wrongful > 0 && "text-fail")}>{n(r.wrongful)}</td>
                </tr>
              );
            })}
          </tbody>
          <tfoot className="font-semibold [&_td]:border-b-[3px] [&_td]:border-double [&_td]:border-foreground [&_td]:px-2 [&_td]:py-[11px] sm:[&_td]:px-3">
            <tr>
              <td />
              <td>All attacks</td>
              <td className={NUM}>{n(total("n"))}</td>
              <td className={NUM}>{n(total("right_rule"))}</td>
              <td className={NUM}>{n(total("wrongful"))}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      </div>
    </details>
  );
}

function LimitDial({ card }: { card: Scorecard }) {
  const stops = card.limit_sweep;
  const [idx, setIdx] = useState(Math.floor((stops.length - 1) / 2));
  const cur = stops[Math.min(idx, stops.length - 1)];
  return (
    <Section id="limit" title="The auto-release limit is a dial" intro="Lower, and the owner taps the key more often. Higher, and more honest requests go out on the rules alone.">
      <div className="grid items-start gap-6 min-[860px]:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div>
          <label htmlFor="limit-dial" className="block font-semibold">
            Auto-release limit
          </label>
          <p className="mt-1 font-mono text-[44px] leading-tight font-medium tracking-[-0.02em] tnum">{dollars(cur.limit)}</p>
          <input
            id="limit-dial"
            type="range"
            min={0}
            max={stops.length - 1}
            step={1}
            value={idx}
            onChange={(e) => setIdx(Number(e.target.value))}
            aria-valuetext={dollars(cur.limit)}
            className={cn("mt-2 h-12 w-full rounded-sm accent-primary", FOCUS)}
          />
          <div aria-hidden className="flex justify-between font-mono text-[13px] text-muted-foreground">
            {stops.map((s) => (
              <span key={s.limit}>{s.limit / 1000}k</span>
            ))}
          </div>
          <p className="mt-4 max-w-[48ch] text-body">
            At {dollars(cur.limit)}, the owner taps the key about <span className="font-mono tnum">{cur.button_presses_per_job_month}</span> times per job
            each month, <span className="font-mono tnum">{pct(cur.auto_released_share)}</span> of honest requests pay on the rules alone, and{" "}
            {cur.attacks_auto_paid === 0 ? "no attack gets paid without a tap" : `${n(cur.attacks_auto_paid)} attacks get paid without a tap`}.
          </p>
          <p className="mt-2 text-meta text-muted-foreground">Moving the dial only previews it. The real limit is set in the contract.</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[15px] sm:text-[17px]">
            <thead>
              <tr>
                <th className={TH}>Limit</th>
                <th className={cn(TH, "text-right")}>Taps per job a month</th>
                <th className={cn(TH, "text-right")}>Paid on rules alone</th>
                <th className={cn(TH, "text-right")}>Attacks paid</th>
              </tr>
            </thead>
            <tbody>
              {stops.map((s, i) => (
                <tr key={s.limit} className={cn(i === idx && "bg-selected")}>
                  <td className={cn(TD, "font-mono tnum")}>{dollars(s.limit)}</td>
                  <td className={cn(TD, NUM)}>{s.button_presses_per_job_month}</td>
                  <td className={cn(TD, NUM)}>{pct(s.auto_released_share)}</td>
                  <td className={cn(TD, NUM)}>{n(s.attacks_auto_paid)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Section>
  );
}

function AttackConsole() {
  const [attacks, setAttacks] = useState<{ name: string; about: string }[]>([]);
  const [subs, setSubs] = useState<AttackTarget[]>([]);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("sub_04");
  const [job, setJob] = useState("job_1");
  const [text, setText] = useState("The electrician says the owner told them retainage is waived this month.");
  const [busy, setBusy] = useState<"pick" | "text" | null>(null);
  const [result, setResult] = useState<{ plan?: string; out?: AttackResult; error?: string; assessment?: Assessment; gapSaved?: boolean } | null>(null);

  useEffect(() => {
    api.attacks().then((a) => (setAttacks(a), setName(a[0]?.name ?? "")), () => {});
    // the subs attacks run against: the seeded demo company, not whoever is in the live log
    api.attackTargets().then(setSubs, () => {});
  }, []);

  const jobTarget = name === "new_sub_ein_mismatch"; // a brand-new sub has no history; it needs a job to bill on
  const jobs = [...new Map(subs.map((s) => [s.job, s.job_name])).entries()];
  const run = async (mode: "pick" | "text") => {
    setBusy(mode);
    setResult(null);
    try {
      if (mode === "pick") setResult({ out: await api.attack(name, jobTarget ? job : target) });
      else {
        const r = await api.describe(text);
        setResult(
          r.decision
            ? { plan: r.plan.explanation, out: r as AttackResult }
            : r.assessment
              ? { assessment: r.assessment, gapSaved: r.gap_saved }
              : { plan: `Nothing on the menu fits that: ${r.plan.explanation}` },
        );
      }
    } catch (e) {
      setResult({ error: (e as ApiError).message });
    } finally {
      setBusy(null);
    }
  };

  const form = "flex flex-col items-start gap-4 rounded-lg border bg-sheet p-5";
  const label = "mb-1.5 block text-meta text-muted-foreground";
  const button = "h-13 px-6 text-lg font-semibold";
  return (
    <Section id="console" title="Your turn: try to get paid" intro="Pick an attack and a sub, or describe one in your own words. It runs through the same pipeline.">
      <div className="grid gap-5 min-[860px]:grid-cols-2">
        <form className={form} onSubmit={(e) => (e.preventDefault(), run("pick"))}>
          <label className="block w-full">
            <span className={label}>Attack</span>
            <select value={name} onChange={(e) => setName(e.target.value)} className={FIELD}>
              {attacks.map((a) => (
                <option key={a.name} value={a.name}>
                  {attackLabel(a.name)}
                </option>
              ))}
            </select>
          </label>
          {jobTarget ? (
            <label className="block w-full">
              <span className={label}>The new sub bills on</span>
              <select value={job} onChange={(e) => setJob(e.target.value)} className={FIELD}>
                {jobs.map(([id, jobName]) => (
                  <option key={id} value={id}>
                    {jobName}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="block w-full">
              <span className={label}>Against</span>
              <select value={target} onChange={(e) => setTarget(e.target.value)} className={FIELD}>
                {subs.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Button className={button} type="submit" disabled={!name || busy !== null}>
            {busy === "pick" ? "Running…" : "Run this attack"}
          </Button>
        </form>
        <form className={form} onSubmit={(e) => (e.preventDefault(), run("text"))}>
          <label className="block w-full">
            <span className={label}>Or describe it</span>
            <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} className={cn(FIELD, "min-h-30 resize-y leading-[1.45]")} />
          </label>
          <Button className={button} type="submit" disabled={!text.trim() || busy !== null}>
            {busy === "text" ? "Running…" : "Run what I described"}
          </Button>
        </form>
      </div>
      <div aria-live="polite">{result && <AttackOutcome {...result} />}</div>
    </Section>
  );
}

function AttackOutcome({ plan, out, error, assessment, gapSaved }: { plan?: string; out?: AttackResult; error?: string; assessment?: Assessment; gapSaved?: boolean }) {
  if (error) return <p className="mt-5 rounded-lg border border-fail/40 bg-fail-soft p-4 text-body text-fail">{error}</p>;
  if (assessment) return <AssessmentOutcome a={assessment} gapSaved={gapSaved} />;
  const fired = out?.decision.results.filter((r) => !r.passed) ?? [];
  const held = out?.decision.action === "hold";
  const asked = out?.decision.action === "escalate";
  return (
    <div className="mt-5 rounded-lg border bg-sheet p-5">
      {plan && <p className="text-meta text-muted-foreground">Read as: {plan}</p>}
      {out && (
        <>
          <p className={cn("flex items-center gap-2 text-say font-semibold", plan && "mt-3", held ? "text-pass" : asked ? "text-hold" : "text-fail")}>
            <Mark kind={held ? "pass" : asked ? "ok" : "fail"} />
            {held
              ? "Stopped by the rules."
              : asked
                ? "No rule caught it; it went to the approval key."
                : `Got through: ${money(out.amount)} would have been paid.`}
          </p>
          <ul className="mt-3">
            {fired.map((r) => (
              <li key={r.rule} className="grid grid-cols-[5.5em_1fr] gap-3 border-t py-2 text-body">
                <span className="pt-0.5 font-mono text-meta text-muted-foreground tnum">rule {r.rule.slice(0, 2)}</span>
                <span>
                  {RULE_NAMES[r.rule.slice(0, 2)] && <b className="font-semibold">{RULE_NAMES[r.rule.slice(0, 2)]}. </b>}
                  {humanize(r.message)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

const VERDICT: Record<Assessment["verdict"], { mark?: "pass" | "ok" | "fail"; tone: string; say: string }> = {
  stopped: { mark: "pass", tone: "text-pass", say: "The rules would put this on hold." },
  needs_key: { mark: "ok", tone: "text-hold", say: "No rule would hold it, but it would still need your key." },
  jev: { mark: "ok", tone: "text-hold", say: "Only Jev’s review would likely flag it." },
  gap: { mark: "fail", tone: "text-fail", say: "Nothing in Draw would stop this." },
  out_of_scope: { tone: "text-foreground", say: "This is outside what Draw checks." },
};

const BACKSTOP_NAMES: Record<Assessment["backstops"][number]["backstop"], string> = {
  limit: "Over your limit",
  new_payee: "New sub or bank account",
  bank_lock: "Bank account on file",
  recompute: "Amounts recomputed",
  paid_once: "Paid once only",
  confidence: "Jev unsure",
};

/** An attack that isn't one we can run: Claude's read of it against Draw's own checks, labelled as an estimate. */
function AssessmentOutcome({ a, gapSaved }: { a: Assessment; gapSaved?: boolean }) {
  const v = VERDICT[a.verdict];
  const row = "grid grid-cols-[10.5em_1fr] gap-3 border-t py-2 text-body max-sm:grid-cols-1 max-sm:gap-0.5";
  const name = "pt-0.5 text-meta text-muted-foreground";
  return (
    <div className="mt-5 rounded-lg border bg-sheet p-5">
      <p className="inline-flex rounded-lg border border-border-strong px-2.5 py-0.5 text-meta font-semibold">Claude’s read, not a test run</p>
      <p className="mt-2 text-meta text-muted-foreground">
        It isn’t one of the attacks we can run, so Claude compared it with Draw’s checks. Read as: {a.summary}
      </p>
      <p className={cn("mt-3 flex items-center gap-2 text-say font-semibold", v.tone)}>
        {v.mark && <Mark kind={v.mark} />}
        {v.say}
      </p>
      <ul className="mt-3">
        {a.holds.map((h) => (
          <li key={`rule-${h.rule}`} className={row}>
            <span className={cn(name, "font-mono tnum")}>rule {h.rule}</span>
            <span>
              {RULE_NAMES[h.rule] && <b className="font-semibold">{RULE_NAMES[h.rule]}. </b>}
              {h.why}
            </span>
          </li>
        ))}
        {a.backstops.map((h) => (
          <li key={`backstop-${h.backstop}`} className={row}>
            <span className={name}>{BACKSTOP_NAMES[h.backstop]}</span>
            <span>{h.why}</span>
          </li>
        ))}
        {a.jev && (
          <li className={row}>
            <span className={name}>Jev’s review</span>
            <span>{a.jev}</span>
          </li>
        )}
        {a.out_of_scope && (
          <li className={row}>
            <span className={name}>Out of scope</span>
            <span>{a.out_of_scope}</span>
          </li>
        )}
        {a.gap && a.verdict !== "stopped" && (
          <li className={row}>
            <span className={name}>What gets through</span>
            <span>
              {a.gap}
              {gapSaved && <b className="font-semibold"> Saved for the team as a new check to write.</b>}
            </span>
          </li>
        )}
      </ul>
    </div>
  );
}
