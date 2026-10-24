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

const ORDER = { on_hold: 0, needs_ok: 1, not_checked: 2, ready: 3, paid: 4 } as const;

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
    for (const list of byJob.values()) list.sort((a, b) => ORDER[a.status] - ORDER[b.status] || Number(b.amount) - Number(a.amount));
    return [...byJob.entries()];
  }, [rows]);

  const count = (s: RequestRow["status"]) => rows?.filter((r) => r.status === s).length ?? 0;
  const total = (list: RequestRow[]) => list.reduce((s, r) => s + Number(r.amount), 0);
  const totalOf = (s: RequestRow["status"]) => total(rows?.filter((r) => r.status === s) ?? []);
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
    <main className="mx-auto grid w-full max-w-[1440px] flex-1 gap-10 px-4 py-6 sm:px-8 sm:py-10 lg:grid-cols-[minmax(0,4fr)_minmax(0,6fr)]">
      <header className={cn("min-w-0 lg:col-span-2", selected && "hidden lg:block")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-meta text-muted-foreground">{monthName(month)}</p>
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
        <h1 id="month" className="mt-3 max-w-[26ch] text-[1.625rem] leading-tight font-semibold tracking-tight sm:text-lede">
          {rows && rows.length > 0 && !unchecked
            ? `${count("ready")} of ${rows.length} pay requests are ready to pay. ${count("needs_ok")} ${count("needs_ok") === 1 ? "needs" : "need"} your OK and ${count("on_hold")} ${count("on_hold") === 1 ? "is" : "are"} on hold.${count("paid") ? ` ${count("paid")} already paid.` : ""}`
            : monthName(month)}
        </h1>
        {rows && rows.length > 0 && !unchecked && (
          <p className="mt-3 max-w-[60ch] text-muted-foreground">
            Ready <span className="font-mono tnum">{money(totalOf("ready"))}</span>, waiting for your OK{" "}
            <span className="font-mono tnum">{money(totalOf("needs_ok"))}</span>, on hold{" "}
            <span className="font-mono tnum">{money(totalOf("on_hold"))}</span>.{" "}
            {connected ? "Updating live." : "Live updates paused."}
          </p>
        )}
      </header>

      <section aria-labelledby="month" className={cn("min-w-0 space-y-12", selected && "hidden lg:block")}>
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
            <Button size="lg" onClick={check} disabled={checking}>
              {checking ? "Checking…" : `Check ${MONTHS[month - 1]}`}
            </Button>
          </div>
        )}

        {rows === null && !error && <ListSkeleton bare />}

        {rows !== null && rows.length === 0 && (
          <p className="rounded-lg border bg-sheet p-6">No pay requests have come in for {monthName(month)}.</p>
        )}

        {jobs.map(([job, list]) => (
          <section key={job} aria-label={job}>
            <div className="flex items-baseline justify-between gap-4 border-b-2 border-foreground pb-2.5">
              <h2 className="text-[1.3125rem] font-semibold tracking-tight">{job}</h2>
              <span className="text-base whitespace-nowrap text-muted-foreground">{list.length} requests</span>
            </div>
            <ul className="divide-y divide-border">
              {list.map((r) => (
                <li key={r.id}>
                  <Link
                    href={href({ r: r.id })}
                    scroll={false}
                    aria-current={selected === r.id ? "true" : undefined}
                    className={cn(
                      "grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-1.5 px-3 py-4 hover:bg-muted",
                      selected === r.id && "bg-selected shadow-[inset_4px_0_0_var(--primary)] hover:bg-selected",
                    )}
                  >
                    <span className="min-w-0 font-semibold">{r.sub}</span>
                    <span className="text-right font-mono whitespace-nowrap tnum">{money(r.amount)}</span>
                    <span className="col-span-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[1.0625rem]">
                      <StatusChip status={r.status} />
                      <span>{rowLine(r.status, r.sentence, r.reason)}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            <div className="flex items-baseline justify-between gap-4 px-3 pt-2.5">
              <span className="text-base text-muted-foreground">Total this period</span>
              <span className="border-b-[3px] border-double border-foreground pb-0.5 font-mono whitespace-nowrap tnum">{money(total(list))}</span>
            </div>
          </section>
        ))}
      </section>

      <aside aria-label="Pay request detail" className={cn("min-w-0", !selected && "hidden lg:block")}>
        <div className="lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7.5rem)] lg:overflow-y-auto lg:pr-1">
          {selected ? (
            <RequestDetailPanel id={selected} version={version} backHref={href({ r: null })} />
          ) : (
            <div className="rounded-lg border border-dashed border-border-strong p-8 leading-relaxed text-muted-foreground">
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
