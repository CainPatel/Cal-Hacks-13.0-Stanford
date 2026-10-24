"use client";

import { LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, money, moneyShort, type AttackResult, type RequestRow, type Scorecard } from "@/lib/api";
import { attackLabel, humanize } from "@/lib/labels";
import { cn } from "@/lib/utils";

const pct = (x: number | null | undefined, digits = 1) => (x == null ? "n/a" : `${(x * 100).toFixed(digits)}%`);
const n = (x: number) => x.toLocaleString("en-US");

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
    <div className="dark flex-1 bg-background text-foreground">
      <main className="mx-auto w-full max-w-[1200px] space-y-12 px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
        <header className="max-w-3xl space-y-4">
          <h1 className="text-3xl font-semibold tracking-tight">How often can Draw be robbed?</h1>
          {error && <p className="text-[17px] text-fail">{error}</p>}
          {!card && !error && <Skeleton className="h-20 w-full" />}
          {card && <Headline card={card} />}
        </header>

        {card && <AttackLedger card={card} />}
        {card && card.limit_sweep?.length > 0 && <LimitChart card={card} />}
        <AttackConsole />

        {card && (
          <p className="font-mono text-[13px] text-muted-foreground">
            {card.kernel} · {n(card.cases)} cases from <code>make fuzz</code> · demo company, synthetic data
          </p>
        )}
      </main>
    </div>
  );
}

function Headline({ card }: { card: Scorecard }) {
  const stopped = card.mutated - card.wrongful_if_every_button_approved;
  return (
    <p className="text-xl leading-relaxed text-balance">
      We sent <b className="font-mono tnum">{n(card.mutated)}</b> forged or broken pay requests through the real pipeline. The rules
      stopped <b className="font-mono tnum text-pass">{pct(card.rule_catch_rate)}</b> of them on their own ({n(stopped)}).{" "}
      <b className="font-mono tnum">{n(card.wrongful_if_every_button_approved)}</b> would have been paid even if the owner tapped
      approve without reading, and <b className="font-mono tnum">{n(card.wrongful_releases)}</b> went out with no tap at all. Of{" "}
      {n(card.clean)} honest requests, <b className="font-mono tnum">{pct(card.false_hold_rate)}</b> were held by mistake.
    </p>
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

  return (
    <section aria-labelledby="ledger" className="space-y-4">
      <h2 id="ledger" className="text-xl font-semibold">Every kind of attack we tried</h2>
      <div className="overflow-x-auto rounded-lg border bg-sheet">
        <table className="w-full text-[15px] sm:min-w-[640px]">
          <thead className="text-left text-sm text-muted-foreground">
            <tr className="border-b">
              <th className="px-4 py-3 font-medium">Attack</th>
              <th className="px-4 py-3 text-right font-medium">Tried</th>
              <th className="px-4 py-3 font-medium">Caught by the right rule</th>
              <th className="px-4 py-3 text-right font-medium">Paid with no tap</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((r) => {
              const share = r.n ? r.right_rule / r.n : 0;
              return (
                <tr key={r.key}>
                  <td className="px-4 py-3">{r.label}</td>
                  <td className="px-4 py-3 text-right font-mono tnum">{n(r.n)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <div className="hidden h-2 w-32 overflow-hidden rounded-full bg-muted sm:block" aria-hidden>
                        <div className={cn("h-full rounded-full", share === 1 ? "bg-pass" : "bg-hold")} style={{ width: `${share * 100}%` }} />
                      </div>
                      <span className="font-mono tnum">{pct(share, 0)}</span>
                    </div>
                  </td>
                  <td className={cn("px-4 py-3 text-right font-mono tnum", r.wrongful > 0 ? "text-fail" : "text-muted-foreground")}>{n(r.wrongful)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function LimitChart({ card }: { card: Scorecard }) {
  const data = card.limit_sweep.map((s) => ({
    limit: moneyShort(s.limit),
    taps: s.button_presses_per_job_month,
    paid: Number(s.dollars_auto_paid),
  }));
  const nothingPaid = data.every((d) => d.paid === 0);
  return (
    <section aria-labelledby="limit" className="space-y-4">
      <div className="max-w-3xl space-y-2">
        <h2 id="limit" className="text-xl font-semibold">The auto-release limit is a dial</h2>
        <p className="text-[15px] leading-relaxed text-muted-foreground">
          Lower, and the owner taps the key more often. Higher, and more goes out on the rules alone. Bars: dollars from attacks that
          would have been paid with no tap. Line: taps per job per month on an honest month.
        </p>
        {nothingPaid && (
          <p className="text-[15px] leading-relaxed">
            Right now no attack gets paid at any of these limits, so there are no bars: the limit only changes how often the owner taps.
          </p>
        )}
      </div>
      <div className="h-[320px] rounded-lg border bg-sheet p-4">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="limit" tick={{ fill: "var(--muted-foreground)", fontSize: 13 }} axisLine={false} tickLine={false} />
            <YAxis yAxisId="paid" hide={nothingPaid} tickFormatter={(v) => moneyShort(v)} tick={{ fill: "var(--muted-foreground)", fontSize: 12 }} axisLine={false} tickLine={false} width={84} />
            <YAxis yAxisId="taps" orientation="right" tick={{ fill: "var(--muted-foreground)", fontSize: 12 }} axisLine={false} tickLine={false} width={36} />
            <Tooltip
              contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--foreground)" }}
              formatter={(v, name) => (name === "paid" ? [money(Number(v)), "Paid with no tap"] : [Number(v).toFixed(2), "Taps per job per month"])}
            />
            <Bar yAxisId="paid" dataKey="paid" fill="var(--fail)" radius={[4, 4, 0, 0]} maxBarSize={56} />
            <Line yAxisId="taps" dataKey="taps" stroke="var(--primary)" strokeWidth={2.5} dot={{ r: 4, fill: "var(--primary)" }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
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

  return (
    <section aria-labelledby="console" className="space-y-4">
      <div className="max-w-3xl space-y-2">
        <h2 id="console" className="text-xl font-semibold">Your turn: try to get paid</h2>
        <p className="text-[15px] text-muted-foreground">Pick an attack and a sub, or describe one in your own words. It runs through the same pipeline.</p>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <form className="space-y-4 rounded-lg border bg-sheet p-5" onSubmit={(e) => (e.preventDefault(), run("pick"))}>
          <label className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">Attack</span>
            <select value={name} onChange={(e) => setName(e.target.value)} className="h-11 w-full rounded-md border bg-background px-3 text-[15px]">
              {attacks.map((a) => (
                <option key={a.name} value={a.name}>
                  {attackLabel(a.name)}
                </option>
              ))}
            </select>
          </label>
          {jobTarget ? (
            <label className="block space-y-1.5">
              <span className="text-sm text-muted-foreground">The new sub bills on</span>
              <select value={job} onChange={(e) => setJob(e.target.value)} className="h-11 w-full rounded-md border bg-background px-3 text-[15px]">
                {jobs.map(([id, jobName]) => (
                  <option key={id} value={id}>
                    {jobName}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="block space-y-1.5">
              <span className="text-sm text-muted-foreground">Against</span>
              <select value={target} onChange={(e) => setTarget(e.target.value)} className="h-11 w-full rounded-md border bg-background px-3 text-[15px]">
                {subs.map((s) => (
                  <option key={s.sub_id} value={s.sub_id}>
                    {s.sub}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Button size="lg" type="submit" disabled={!name || busy !== null}>
            {busy === "pick" && <LoaderCircle className="animate-spin" aria-hidden />} Run this attack
          </Button>
        </form>
        <form className="space-y-4 rounded-lg border bg-sheet p-5" onSubmit={(e) => (e.preventDefault(), run("text"))}>
          <label className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">Or describe it</span>
            <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} className="text-[15px]" />
          </label>
          <Button size="lg" type="submit" disabled={!text.trim() || busy !== null}>
            {busy === "text" && <LoaderCircle className="animate-spin" aria-hidden />} Run what I described
          </Button>
        </form>
      </div>
      <div aria-live="polite">{result && <AttackOutcome {...result} />}</div>
    </section>
  );
}

function AttackOutcome({ plan, out, error }: { plan?: string; out?: AttackResult; error?: string }) {
  if (error) return <p className="rounded-lg border border-fail/40 bg-fail-soft p-4 text-[15px] text-fail">{error}</p>;
  const fired = out?.decision.results.filter((r) => !r.passed) ?? [];
  const held = out?.decision.action === "hold";
  const asked = out?.decision.action === "escalate";
  return (
    <div className="space-y-3 rounded-lg border bg-sheet p-5">
      {plan && <p className="text-[15px] text-muted-foreground">Read as: {plan}</p>}
      {out && (
        <>
          <p className={cn("text-lg font-semibold", held ? "text-pass" : asked ? "text-hold" : "text-fail")}>
            {held
              ? "Stopped by the rules."
              : asked
                ? "No rule caught it; it went to the approval key."
                : `Got through: ${money(out.amount)} would have been paid.`}
          </p>
          <ul className="space-y-1.5 text-[15px]">
            {fired.map((r) => (
              <li key={r.rule} className="flex gap-3">
                <span className="font-mono text-muted-foreground tnum">rule {r.rule.slice(0, 2)}</span>
                <span>{humanize(r.message)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
