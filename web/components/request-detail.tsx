"use client";

import { ArrowLeft, Check, Hand, Mail, Phone, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Evidence } from "@/components/evidence";
import { StatusChip } from "@/components/status-chip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, money, type RequestDetail } from "@/lib/api";
import { humanize, rowLine } from "@/lib/labels";
import { cn } from "@/lib/utils";

const ESCALATION_RULES = new Set(["14_within_spend_authority", "15_confidence_floor"]);
const SHOWS_EVIDENCE = new Set(["07", "10", "11", "13"]);

export function RequestDetailPanel({ id, version, backHref }: { id: string; version: number; backHref: string }) {
  const [data, setData] = useState<RequestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.request(id).then(
      (d) => live && (setData(d), setError(null)),
      (e: ApiError) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [id, version]);

  if (error) return <p className="p-6 text-[15px] text-fail">{error}</p>;
  if (!data || data.id !== id) return <DetailSkeleton />;

  const results = data.trace?.results ?? [];
  const holding = results.filter((r) => !r.passed && !ESCALATION_RULES.has(r.rule));
  const period = new Date(`${data.period_end}T12:00:00`).toLocaleDateString("en-US", { month: "long", year: "numeric" });

  return (
    <article className="space-y-8">
      <header className="space-y-4">
        <Link href={backHref} className="inline-flex items-center gap-1.5 text-[15px] text-muted-foreground hover:text-foreground lg:hidden">
          <ArrowLeft className="size-4" aria-hidden /> All pay requests
        </Link>
        <div className="grid gap-x-6 gap-y-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
          <div className="min-w-0 space-y-1">
            <h2 className="text-2xl font-semibold tracking-tight text-balance">{data.sub}</h2>
            <p className="text-[15px] text-muted-foreground">
              {data.job} · application for {period}
            </p>
          </div>
          <div className="sm:text-right">
            <p className="font-mono text-3xl font-medium tnum tracking-tight">{money(data.amount)}</p>
            <p className="text-sm text-muted-foreground">due this period</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip status={data.status} />
          <p className="text-[17px] leading-snug">{rowLine(data.status, data.sentence, data.reason)}</p>
        </div>
        <NextAction detail={data} />
      </header>

      {holding.length > 0 && (
        <section aria-labelledby="proof" className="space-y-5 rounded-lg border bg-sheet p-5">
          <h3 id="proof" className="text-lg font-semibold">What we found</h3>
          {holding.map((r) => (
            <div key={r.rule} className="space-y-3 border-t pt-4 first:border-t-0 first:pt-0">
              <p className="font-medium">{humanize(r.message)}</p>
              {SHOWS_EVIDENCE.has(r.rule.slice(0, 2)) && <Evidence result={r} lines={data.lines} />}
            </div>
          ))}
        </section>
      )}

      {results.length > 0 && (
        <section aria-labelledby="checks" className="space-y-3">
          <h3 id="checks" className="text-lg font-semibold">Every check</h3>
          <ul className="divide-y rounded-lg border bg-sheet">
            {results.map((r) => {
              const ask = !r.passed && ESCALATION_RULES.has(r.rule);
              const Icon = r.passed ? Check : ask ? Hand : X;
              return (
                <li key={r.rule} className="flex items-start gap-3 px-4 py-3">
                  <Icon aria-hidden className={cn("mt-0.5 size-[18px] shrink-0", r.passed ? "text-pass" : ask ? "text-hold" : "text-fail")} />
                  <p className={cn("text-[15px] leading-snug", r.passed && "text-muted-foreground")}>
                    <span className="sr-only">{r.passed ? "Passed: " : ask ? "Needs your OK: " : "Failed: "}</span>
                    {humanize(r.message)}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section aria-labelledby="lines" className="space-y-3">
        <h3 id="lines" className="text-lg font-semibold">Line items</h3>
        <div className="overflow-x-auto rounded-lg border bg-sheet">
          <table className="w-full min-w-[340px] text-[15px] sm:min-w-[560px]">
            <thead className="text-left text-sm text-muted-foreground">
              <tr className="border-b">
                <th className="px-4 py-2.5 font-medium">Work</th>
                <th className="hidden px-4 py-2.5 text-right font-medium sm:table-cell">Contract</th>
                <th className="px-4 py-2.5 text-right font-medium">This period</th>
                <th className="px-4 py-2.5 text-right font-medium">Claimed</th>
                <th className="px-4 py-2.5 text-right font-medium">Video</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.lines.map((l) => {
                const over = l.video_pct !== null && Number(l.pct_complete) - Number(l.video_pct) > 10;
                return (
                  <tr key={l.item_no}>
                    <td className="px-4 py-2.5">
                      {l.description} <span className="text-sm text-muted-foreground">{l.item_no}</span>
                    </td>
                    <td className="hidden px-4 py-2.5 text-right font-mono tnum sm:table-cell">{money(l.scheduled_value)}</td>
                    <td className="px-4 py-2.5 text-right font-mono tnum">{money(l.this_period)}</td>
                    <td className={cn("px-4 py-2.5 text-right font-mono tnum", over && "font-semibold text-fail")}>{Number(l.pct_complete).toFixed(0)}%</td>
                    <td className="px-4 py-2.5 text-right font-mono tnum text-muted-foreground">
                      {l.video_pct === null ? "no video" : `${Number(l.video_pct).toFixed(0)}%`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {data.site_evidence && (
          <p className="text-sm text-muted-foreground">
            Video from the site walk on{" "}
            {new Date(`${data.site_evidence.date}T12:00:00`).toLocaleDateString("en-US", { month: "long", day: "numeric" })}.
          </p>
        )}
      </section>

      {data.trace && (
        <details className="group rounded-lg border bg-sheet">
          <summary className="cursor-pointer list-none px-4 py-3 text-[15px] font-medium select-none hover:bg-muted/60">
            Show details <span className="text-muted-foreground">(the full rule trace)</span>
          </summary>
          <pre className="max-h-[480px] overflow-auto border-t px-4 py-3 font-mono text-[13px] leading-relaxed whitespace-pre-wrap">
            {JSON.stringify(data.trace, null, 2)}
          </pre>
        </details>
      )}
    </article>
  );
}

/** The one thing the office manager can do about this hold, as a button. */
function NextAction({ detail }: { detail: RequestDetail }) {
  const [shown, setShown] = useState(false);
  const action = detail.next_action?.replace(/\.$/, "");
  if (detail.status !== "on_hold" || !action) return null;

  if (/call/i.test(action)) {
    return (
      <div className="space-y-2">
        <Button size="lg" onClick={() => setShown(true)}>
          <Phone aria-hidden /> Call to verify the bank change
        </Button>
        {shown && (
          <p className="max-w-prose text-[15px] leading-snug">
            Call {detail.sub} at the number in your own records, not one from the email, and ask whether they changed banks.
          </p>
        )}
      </div>
    );
  }
  const subject = `${detail.job}: ${action.toLowerCase()}`;
  const body = `Hi,\n\nWe can't include your pay request for this period yet. ${rowLine(detail.status, detail.sentence, null)}\n\n${action}.\n\nThanks`;
  const to = /super/i.test(action) ? "" : `accounting@${detail.vendor.email_domain}`;
  return (
    <Button size="lg" nativeButton={false} render={<a href={`mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`} />}>
      <Mail aria-hidden /> {action}
    </Button>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-6" aria-busy>
      <div className="space-y-3">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-9 w-40" />
      </div>
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
