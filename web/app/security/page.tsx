"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Mark } from "@/components/evidence";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, money, type AttackResult, type Calibration, type RequestRow, type Scorecard } from "@/lib/api";
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
  const [calib, setCalib] = useState<Calibration | { error: string; missing: boolean } | null>(null);

  useEffect(() => {
    api.scorecard().then(
      (c) => (setCard(c), setError(null)),
      (e: ApiError) => setError(e.status === 404 ? "No scorecard yet. Run `make fuzz N=5000` on the server, then reload." : e.message),
    );
    api.calibration().then(setCalib, (e: ApiError) => setCalib({ error: e.message, missing: e.status === 404 }));
  }, [version]);

  return (
    <main className="mx-auto w-full max-w-[1164px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <h1 className="text-[27px] leading-tight font-semibold tracking-[-0.02em] sm:text-lede">How often can Draw be robbed?</h1>
      {error && <p className="mt-4 text-body text-fail">{error}</p>}
      {!card && !error && <Skeleton className="mt-4 h-24 w-full max-w-[62ch]" />}
      {card && <Headline card={card} />}

      {card && <AttackLedger card={card} />}
      {card && card.limit_sweep?.length > 0 && <LimitDial card={card} />}
      <CalibrationCurve calib={calib} />
      <AttackConsole />

      {card && (
        <p className="mt-14 font-mono text-sm text-muted-foreground">
          {card.kernel}, {n(card.cases)} cases from make fuzz. Sample company, synthetic data.
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
  const b = "font-mono font-semibold tnum";
  return (
    <>
      <p className="mt-4 max-w-[62ch] text-say leading-[1.55]">
        We sent <b className={b}>{n(card.mutated)}</b> forged or broken pay requests through the real pipeline. The rules stopped{" "}
        <b className={b}>{pct(card.rule_catch_rate)}</b> of them on their own ({n(stopped)}). <b className={b}>{n(card.wrongful_if_every_button_approved)}</b>{" "}
        would have been paid even if the owner tapped approve without reading, and <b className={b}>{n(card.wrongful_releases)}</b> went out with no tap
        at all. Of {n(card.clean)} honest requests, <b className={b}>{pct(card.false_hold_rate)}</b> were held by mistake.
      </p>
      <p className="mt-3 max-w-[64ch] text-body text-muted-foreground">
        That stopped <span className="font-mono tnum">{money(card.dollars_protected)}</span> in bad payments and held up{" "}
        <span className="font-mono tnum">{money(card.dollars_delayed)}</span> of good ones.
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
    <Section id="ledger" title="Every kind of attack we tried">
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
    </Section>
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

function CalibrationCurve({ calib }: { calib: Calibration | { error: string; missing: boolean } | null }) {
  const intro = "When Jev says it's 80% sure, it should be right about 80% of the time. That's what makes the escalation threshold mean something.";
  if (calib && "curve" in calib && calib.curve.bins.length > 0) {
    const ece = calib.curve.expected_calibration_error;
    const axis = { fill: "var(--muted-foreground)", fontSize: 12 };
    return (
      <Section id="calib" title="Is Jev's confidence honest?" intro={intro}>
        <div className="h-[320px] max-w-[640px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={calib.curve.bins} margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
              <CartesianGrid stroke="var(--border)" />
              <XAxis
                type="number"
                dataKey="mean_confidence"
                domain={[0, 1]}
                ticks={[0, 0.25, 0.5, 0.75, 1]}
                tickFormatter={(v) => pct(v, 0)}
                tick={axis}
                stroke="var(--border-strong)"
                label={{ value: "How sure Jev said it was", position: "bottom", offset: 4, fill: "var(--muted-foreground)", fontSize: 13 }}
              />
              <YAxis
                type="number"
                domain={[0, 1]}
                ticks={[0, 0.25, 0.5, 0.75, 1]}
                tickFormatter={(v) => pct(v, 0)}
                tick={axis}
                stroke="var(--border-strong)"
                width={48}
                label={{ value: "How often it was right", angle: -90, position: "insideLeft", fill: "var(--muted-foreground)", fontSize: 13 }}
              />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--border-strong)" strokeDasharray="6 6" ifOverflow="extendDomain" />
              <Tooltip
                contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--foreground)" }}
                labelFormatter={(v) => `Said ${pct(Number(v), 0)} sure`}
                formatter={(v) => [pct(Number(v), 0), "Right"]}
              />
              <Line dataKey="accuracy" stroke="var(--foreground)" strokeWidth={2.5} dot={{ r: 4, fill: "var(--foreground)" }} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-3 max-w-[64ch] text-body">
          The dashed line is perfect honesty. Across <span className="font-mono tnum">{n(calib.answers)}</span> answers
          {ece != null && (
            <>
              , Jev&apos;s stated confidence was off from how often it was right by <span className="font-mono tnum">{(ece * 100).toFixed(1)}</span> points
              on average
            </>
          )}
          .
        </p>
      </Section>
    );
  }
  const ticks = [0, 50, 100];
  return (
    <Section id="calib" title="Is Jev's confidence honest?" intro={intro}>
      <div className="max-w-[640px] rounded-lg border border-dashed border-border-strong p-5">
        <svg viewBox="0 0 600 300" role="img" aria-label="Empty chart: no calibration run yet" className="block h-auto w-full">
          <g className="stroke-border-strong">
            <line x1="50" y1="260" x2="580" y2="260" />
            <line x1="50" y1="20" x2="50" y2="260" />
            <line x1="50" y1="260" x2="580" y2="20" strokeDasharray="6 6" />
          </g>
          <g fill="var(--muted-foreground)" fontSize="12">
            {ticks.map((t) => (
              <g key={t}>
                <text x={50 + t * 5.3} y="280" textAnchor="middle">
                  {t}%
                </text>
                <text x="40" y={264 - t * 2.4} textAnchor="end">
                  {t}%
                </text>
              </g>
            ))}
            <text x="315" y="298" textAnchor="middle">
              How sure Jev said it was
            </text>
            <text x="440" y="70" textAnchor="middle">
              Honest confidence
            </text>
          </g>
        </svg>
        <p className="mt-3 text-body">
          {!calib ? (
            "Loading the calibration run…"
          ) : "error" in calib && !calib.missing ? (
            `Couldn't load the calibration: ${calib.error}`
          ) : (
            <>
              No calibration run yet. It shows up here after <span className="font-mono">python -m fuzzer.calibration</span> runs on the server.
            </>
          )}
        </p>
      </div>
    </Section>
  );
}

function AttackConsole() {
  const [attacks, setAttacks] = useState<{ name: string; about: string }[]>([]);
  const [subs, setSubs] = useState<RequestRow[]>([]);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("sub_04");
  const [job, setJob] = useState("job_1");
  const [text, setText] = useState("The electrician says the owner told them retainage is waived this month.");
  const [busy, setBusy] = useState<"pick" | "text" | null>(null);
  const [result, setResult] = useState<{ plan?: string; out?: AttackResult; error?: string } | null>(null);

  useEffect(() => {
    api.attacks().then((a) => (setAttacks(a), setName(a[0]?.name ?? "")), () => {});
    api.requests(6).then(setSubs, () => {});
  }, []);

  const jobTarget = name === "new_sub_ein_mismatch"; // a brand-new sub has no history; it needs a job to bill on
  const jobs = [...new Map(subs.map((s) => [s.job_id, s.job])).entries()];
  const run = async (mode: "pick" | "text") => {
    setBusy(mode);
    setResult(null);
    try {
      if (mode === "pick") setResult({ out: await api.attack(name, jobTarget ? job : target) });
      else {
        const r = await api.describe(text);
        setResult(r.decision ? { plan: r.plan.explanation, out: r as AttackResult } : { plan: `Nothing on the menu fits that: ${r.plan.explanation}` });
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
                  <option key={s.sub_id} value={s.sub_id}>
                    {s.sub}
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

function AttackOutcome({ plan, out, error }: { plan?: string; out?: AttackResult; error?: string }) {
  if (error) return <p className="mt-5 rounded-lg border border-fail/40 bg-fail-soft p-4 text-body text-fail">{error}</p>;
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
