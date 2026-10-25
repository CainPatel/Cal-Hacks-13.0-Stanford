"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { CheckExplainer } from "@/components/check-explainer";
import { MonthOverview, type View } from "@/components/month-overview";
import { RequestDetailPanel } from "@/components/request-detail";
import { Waiting } from "@/components/waiting";
import { StatusMark } from "@/components/status-chip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, money, MONTHS, monthName, type RequestRow } from "@/lib/api";
import { category, CATEGORY, CATEGORY_ORDER, phrase } from "@/lib/labels";
import { cn } from "@/lib/utils";

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton />}>
      <PayRequests />
    </Suspense>
  );
}


function PayRequests() {
  const params = useSearchParams();
  const router = useRouter();
  const month = Number(params.get("m") ?? 6);
  const selected = params.get("r");
  const view: View = params.get("v") === "status" ? "status" : "job";
  const { version, connected } = useLive();
  const [rows, setRows] = useState<RequestRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    let live = true;
    api.requests(month).then(
      (r) => live && (setRows(r), setError(null)),
      (e: ApiError) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [month, version]);

  const href = (q: { m?: number; r?: string | null; v?: View }) => {
    const u = new URLSearchParams();
    const m = q.m ?? month;
    if (m !== 6) u.set("m", String(m));
    if (q.r) u.set("r", q.r);
    if ((q.v ?? view) === "status") u.set("v", "status");
    const s = u.toString();
    return s ? `/?${s}` : "/";
  };

  const jobs = useMemo(() => {
    const byJob = new Map<string, RequestRow[]>();
    for (const r of rows ?? []) byJob.set(r.job, [...(byJob.get(r.job) ?? []), r]);
    const rank = (r: RequestRow) => CATEGORY_ORDER.indexOf(category(r));
    for (const list of byJob.values()) list.sort((a, b) => rank(a) - rank(b) || Number(b.amount) - Number(a.amount));
    // jobs that most need help first: compare their requests' ranks in order
    return [...byJob.entries()].sort(([an, a], [bn, b]) => {
      for (let i = 0; i < Math.max(a.length, b.length); i++) {
        const d = (a[i] ? rank(a[i]) : 99) - (b[i] ? rank(b[i]) : 99);
        if (d) return d;
      }
      return an.localeCompare(bn);
    });
  }, [rows]);

  const total = (list: RequestRow[]) => list.reduce((s, r) => s + Number(r.amount), 0);
  const count = (st: RequestRow["status"]) => rows?.filter((r) => r.status === st).length ?? 0;
  const unchecked = rows !== null && rows.length > 0 && rows.every((r) => r.status === "not_checked");

  const check = async () => {
    setChecking(true);
    try {
      await api.replay(month);
      setRows(await api.requests(month));
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setChecking(false);
    }
  };

  return (
    <main
      className={cn(
        "mx-auto grid w-full max-w-[1440px] flex-1 gap-10 px-4 py-6 sm:px-8 sm:py-10",
        selected && "lg:grid-cols-[minmax(0,4fr)_minmax(0,6fr)]",
      )}
    >
      <header className={cn("min-w-0", selected && "hidden lg:col-span-2 lg:block")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 id="month" className="text-[1.625rem] leading-tight font-semibold tracking-tight sm:text-lede">
            {monthName(month)}
          </h1>
          <label className="flex items-center gap-2 text-meta text-muted-foreground">
            Month
            <select
              value={month}
              onChange={(e) => router.push(href({ m: Number(e.target.value), r: null }))}
              className="h-12 rounded-lg border border-border-strong bg-sheet px-3 text-body text-foreground"
            >
              {MONTHS.map((name, i) => (
                <option key={name} value={i + 1}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {rows && rows.length > 0 && !unchecked && (
          <>
            <MonthOverview rows={rows} view={view} onView={(v) => router.replace(href({ v }), { scroll: false })} requestHref={(id) => `${href({ r: id })}#detail`} />
            <p className="mt-3 text-meta text-muted-foreground">{connected ? "Updating live." : "Live updates paused."}</p>
          </>
        )}
      </header>

      <section aria-labelledby="month" className={cn("min-w-0 space-y-8", selected && "hidden lg:block")}>
        {error && (
          <div role="alert" className="rounded-lg border border-fail bg-fail-soft p-4 text-fail">
            {error}
          </div>
        )}

        {count("not_checked") > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border bg-sheet p-6">
            <p>
              {unchecked
                ? "These pay requests haven't been checked yet."
                : `${count("not_checked")} pay ${count("not_checked") === 1 ? "request hasn't" : "requests haven't"} been checked yet.`}
            </p>
            <div className="flex items-center gap-2">
              <Button size="lg" onClick={check} disabled={checking}>
                {checking ? "Checking…" : `Check ${MONTHS[month - 1]}`}
              </Button>
              <CheckExplainer month={MONTHS[month - 1]} />
            </div>
          </div>
        )}

        {rows === null && !error && <ListSkeleton bare />}

        {rows !== null && rows.length === 0 && (
          <p className="rounded-lg border bg-sheet p-6">No pay requests have come in for {monthName(month)}.</p>
        )}

        {rows && rows.length > 0 && !unchecked && <Waiting rows={rows} month={month} version={version} wide={!selected} requestHref={(id) => `${href({ r: id })}#detail`} />}

        <div className={cn("grid items-start gap-x-10 gap-y-8", !selected && "xl:grid-cols-2")}>
          {jobs.map(([job, list]) => (
            <details key={job} open id={`job-${list[0].job_id}`} className="group min-w-0 scroll-mt-24">
              <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 rounded-sm border-b-2 border-foreground pb-2 outline-none select-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
                <h2 className="min-w-0 text-[1.3125rem] font-semibold tracking-tight">{job}</h2>
                <span className="text-base whitespace-nowrap text-muted-foreground">{list.length} requests</span>
                <ChevronDown className="ml-auto size-5 shrink-0 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden />
              </summary>
              <ul className="divide-y divide-border">
                {list.map((r) => {
                  const cat = category(r);
                  const said = phrase(r);
                  return (
                    <li key={r.id}>
                      <Link
                        href={href({ r: selected === r.id ? null : r.id })}
                        scroll={false}
                        aria-current={selected === r.id ? "true" : undefined}
                        className={cn(
                          "grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-1.5 px-3 py-3 sm:grid-cols-[8.75rem_minmax(0,1fr)_auto] outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50",
                          selected === r.id && "bg-selected shadow-[inset_4px_0_0_var(--primary)] hover:bg-selected",
                        )}
                      >
                        <span className={cn("inline-flex min-h-7 items-center gap-1.5 self-start rounded-lg px-2 text-[0.9375rem] font-semibold whitespace-nowrap col-span-2 justify-self-start sm:col-span-1", CATEGORY[cat].chip)}>
                          <StatusMark category={cat} />
                          {CATEGORY[cat].short}
                          <span className="sr-only">: {CATEGORY[cat].label}</span>
                        </span>
                        <span className="min-w-0">
                          <span className="block font-semibold">{r.sub}</span>
                          {said && <span className="block text-[15px] text-muted-foreground">{said}</span>}
                        </span>
                        <span className="text-right font-mono whitespace-nowrap tnum">{money(r.amount)}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
              <div className="flex items-baseline justify-between gap-4 px-3 pt-2.5">
                <span className="text-base text-muted-foreground">Total this period</span>
                <span className="border-b-[3px] border-double border-foreground pb-0.5 font-mono whitespace-nowrap tnum">{money(total(list))}</span>
              </div>
            </details>
          ))}
        </div>
      </section>

      {selected && (
        <aside id="detail" aria-label="Pay request detail" className="min-w-0 scroll-mt-20">
          <div className="lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7.5rem)] lg:overflow-y-auto lg:pr-1">
            <RequestDetailPanel id={selected} version={version} backHref={href({ r: null })} />
          </div>
        </aside>
      )}
    </main>
  );
}

function ListSkeleton({ bare }: { bare?: boolean }) {
  const rows = (
    <div className="space-y-6" aria-busy>
      {[0, 1].map((i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-[280px] w-full" />
        </div>
      ))}
    </div>
  );
  return bare ? rows : <main className="mx-auto w-full max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">{rows}</main>;
}
