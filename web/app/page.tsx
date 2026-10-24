"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { RequestDetailPanel } from "@/components/request-detail";
import { StatusChip } from "@/components/status-chip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, money, MONTHS, monthName, type RequestRow } from "@/lib/api";
import { rowLine } from "@/lib/labels";
import { cn } from "@/lib/utils";

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton />}>
      <PayRequests />
    </Suspense>
  );
}

const ORDER = { on_hold: 0, needs_ok: 1, not_checked: 2, ready: 3 } as const;

function PayRequests() {
  const params = useSearchParams();
  const router = useRouter();
  const month = Number(params.get("m") ?? 6);
  const selected = params.get("r");
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

  const href = (q: { m?: number; r?: string | null }) => {
    const u = new URLSearchParams();
    const m = q.m ?? month;
    if (m !== 6) u.set("m", String(m));
    if (q.r) u.set("r", q.r);
    const s = u.toString();
    return s ? `/?${s}` : "/";
  };

  const jobs = useMemo(() => {
    const byJob = new Map<string, RequestRow[]>();
    for (const r of rows ?? []) byJob.set(r.job, [...(byJob.get(r.job) ?? []), r]);
    for (const list of byJob.values()) list.sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.sub.localeCompare(b.sub));
    return [...byJob.entries()];
  }, [rows]);

  const count = (s: RequestRow["status"]) => rows?.filter((r) => r.status === s).length ?? 0;
  const unchecked = rows !== null && rows.length > 0 && count("not_checked") === rows.length;

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
    <main className="mx-auto grid w-full max-w-[1400px] flex-1 gap-8 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,600px)] lg:px-8 lg:py-8">
      <section aria-labelledby="month" className={cn("min-w-0 space-y-6", selected && "hidden lg:block")}>
        <header className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h1 id="month" className="text-3xl font-semibold tracking-tight">
              {monthName(month)}
            </h1>
            <label className="flex items-center gap-2 text-[15px] text-muted-foreground">
              Month
              <select
                value={month}
                onChange={(e) => router.push(href({ m: Number(e.target.value), r: null }))}
                className="h-10 rounded-md border bg-sheet px-3 text-[15px] text-foreground"
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
            <p className="text-[17px] leading-snug">
              {rows.length} pay requests. <b className="font-semibold">{count("ready")}</b> ready to pay,{" "}
              <b className="font-semibold">{count("needs_ok")}</b> need your OK,{" "}
              <b className="font-semibold">{count("on_hold")}</b> on hold.
              <span className="ml-2 text-sm text-muted-foreground">{connected ? "Updating live." : "Live updates paused."}</span>
            </p>
          )}
        </header>

        {error && (
          <div role="alert" className="rounded-lg border border-fail/30 bg-fail-soft p-4 text-[15px] text-fail">
            {error}
          </div>
        )}

        {unchecked && (
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border bg-sheet p-5">
            <p className="text-[17px]">These pay requests haven&apos;t been checked yet.</p>
            <Button size="lg" onClick={check} disabled={checking}>
              {checking ? "Checking…" : `Check ${MONTHS[month - 1]}`}
            </Button>
          </div>
        )}

        {rows === null && !error && <ListSkeleton bare />}

        {rows !== null && rows.length === 0 && (
          <p className="rounded-lg border bg-sheet p-5 text-[17px]">No pay requests have come in for {monthName(month)}.</p>
        )}

        {jobs.map(([job, list]) => (
          <section key={job} aria-label={job} className="space-y-2">
            <h2 className="flex items-baseline justify-between gap-3 text-lg font-semibold">
              {job}
              <span className="font-mono text-[15px] font-normal tnum text-muted-foreground">
                {money(list.reduce((s, r) => s + Number(r.amount), 0))}
              </span>
            </h2>
            <ul className="divide-y overflow-hidden rounded-lg border bg-sheet">
              {list.map((r) => (
                <li key={r.id}>
                  <Link
                    href={href({ r: r.id })}
                    scroll={false}
                    aria-current={selected === r.id ? "true" : undefined}
                    className={cn(
                      "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 px-4 py-3.5 transition-colors hover:bg-muted/60 sm:grid-cols-[minmax(0,1fr)_auto_9.5rem]",
                      selected === r.id && "bg-accent hover:bg-accent",
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[17px] font-medium">{r.sub}</span>
                      <span className="block text-[15px] leading-snug text-muted-foreground">{rowLine(r.status, r.sentence, r.reason)}</span>
                    </span>
                    <span className="text-right font-mono text-[17px] tnum">{money(r.amount)}</span>
                    <StatusChip status={r.status} className="col-span-2 justify-self-start sm:col-span-1 sm:justify-self-end" />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </section>

      <aside aria-label="Pay request detail" className={cn("min-w-0", !selected && "hidden lg:block")}>
        <div className="lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7.5rem)] lg:overflow-y-auto lg:pr-1">
          {selected ? (
            <RequestDetailPanel id={selected} version={version} backHref={href({ r: null })} />
          ) : (
            <div className="rounded-lg border border-dashed p-8 text-[17px] leading-relaxed text-muted-foreground">
              Pick a pay request to see every check, the proof behind any hold, and what to do next.
            </div>
          )}
        </div>
      </aside>
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
