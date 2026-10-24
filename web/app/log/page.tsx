"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, type LogEvent, type LogKind } from "@/lib/api";
import { cn } from "@/lib/utils";

const KINDS: { kind: LogKind | ""; label: string }[] = [
  { kind: "", label: "Everything" },
  { kind: "fact", label: "Documents and bank records" },
  { kind: "judgment", label: "Jev's reviews" },
  { kind: "kernel_trace", label: "Decisions" },
  { kind: "signature", label: "Approvals" },
  { kind: "execution", label: "Payments" },
];
const KIND_WORD: Record<LogKind, string> = {
  fact: "Record",
  judgment: "Review",
  kernel_trace: "Decision",
  signature: "Approval",
  execution: "Payment",
};
const PAGE = 100;

const FOCUS = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
const FIELD = cn("min-h-12 rounded-md border border-border-strong bg-background px-3 py-2.5 text-[17px]", FOCUS);

/** "Oct 24, 7:53 PM": the clock the server wrote the row with. */
const written = (ts: string) =>
  new Date(ts).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export default function LogPage() {
  return (
    <Suspense>
      <EventLog />
    </Suspense>
  );
}

function EventLog() {
  const params = useSearchParams();
  const { version, connected } = useLive();
  const [kind, setKind] = useState<LogKind | "">("");
  const [draft, setDraft] = useState(params.get("r") ?? "");
  const [request, setRequest] = useState(params.get("r") ?? "");
  const [rows, setRows] = useState<LogEvent[] | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [older, setOlder] = useState(false);
  const shown = useRef(""); // which filter the rows on screen belong to

  useEffect(() => {
    let live = true;
    const filter = `${kind}|${request}`;
    api.log({ limit: PAGE, kind, request_id: request }).then(
      (p) => {
        if (!live) return;
        const changed = shown.current !== filter;
        shown.current = filter;
        // a live update brings a fresh first page; older pages already loaded stay below it
        setRows((prev) => {
          if (changed || !prev) return p.events;
          const oldest = p.events.at(-1)?.seq ?? Infinity;
          return [...p.events, ...prev.filter((e) => e.seq < oldest)];
        });
        if (changed) setMore(p.more);
        setError(null);
      },
      (e: ApiError) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [kind, request, version]);

  const loadOlder = async () => {
    if (!rows?.length) return;
    setOlder(true);
    try {
      const p = await api.log({ limit: PAGE, kind, request_id: request, before: rows[rows.length - 1].seq });
      setRows([...rows, ...p.events]);
      setMore(p.more);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setOlder(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-[1164px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <h1 className="text-[27px] leading-tight font-semibold tracking-[-0.02em] sm:text-lede">Everything Draw has recorded</h1>
      <p className="mt-3 max-w-[64ch] text-body text-muted-foreground">
        Every document, bank record, review, decision, approval and payment, newest first. Nothing here is ever changed or deleted.{" "}
        {connected ? "Updating live." : "Live updates paused."}
      </p>

      <form
        className="mt-8 flex flex-wrap items-end gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          setRequest(draft.trim());
        }}
      >
        <label className="block">
          <span className="block text-meta text-muted-foreground">Show</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as LogKind | "")} className={cn(FIELD, "mt-1.5")}>
            {KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="block text-meta text-muted-foreground">For one pay request</span>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. pr_sub_03_6"
            autoComplete="off"
            className={cn(FIELD, "mt-1.5 w-64 font-mono")}
          />
        </label>
        <Button type="submit" size="lg" variant="outline" className="min-h-12">
          Filter
        </Button>
        {request && (
          <Button
            type="button"
            size="lg"
            variant="ghost"
            className="min-h-12"
            onClick={() => {
              setDraft("");
              setRequest("");
            }}
          >
            Show all requests
          </Button>
        )}
      </form>

      {error && (
        <div role="alert" className="mt-6 rounded-lg border border-fail/30 bg-fail-soft p-4 text-meta text-fail">
          {error}
        </div>
      )}
      {rows === null && !error && <Skeleton className="mt-6 h-96 w-full" />}
      {rows?.length === 0 && <p className="mt-6 rounded-lg border bg-sheet p-6 text-body">Nothing recorded yet for this filter.</p>}

      {rows && rows.length > 0 && (
        <ol className="mt-6 divide-y rounded-lg border bg-sheet">
          {rows.map((e) => (
            <li key={e.seq} className="grid gap-x-6 gap-y-1 px-4 py-3.5 sm:grid-cols-[150px_minmax(0,1fr)] sm:px-5">
              <span className="font-mono text-sm whitespace-nowrap text-muted-foreground tnum">
                #{e.seq} · {written(e.ts)}
              </span>
              <div className="min-w-0">
                <p className="text-body">{e.line}</p>
                <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
                  <span className="font-semibold">{KIND_WORD[e.kind] ?? e.kind}</span>
                  {e.request_id && (
                    <Link href={`/?r=${encodeURIComponent(e.request_id)}`} className={cn("font-mono text-primary underline underline-offset-[3px]", FOCUS)}>
                      {e.request_id}
                    </Link>
                  )}
                  <details className="w-full">
                    <summary className={cn("w-fit cursor-pointer text-primary underline underline-offset-[3px] select-none", FOCUS)}>Show details</summary>
                    <pre className="mt-2 max-h-[360px] overflow-auto rounded-[4px] bg-muted p-3 font-mono text-[13px] leading-normal whitespace-pre-wrap text-foreground">
                      {JSON.stringify({ seq: e.seq, written: e.ts, business_time: e.at, kind: e.kind, table: e.table, data: e.data }, null, 2)}
                    </pre>
                  </details>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {rows && more && (
        <Button size="lg" variant="outline" className="mt-6" onClick={loadOlder} disabled={older}>
          {older ? "Loading…" : "Show older"}
        </Button>
      )}
    </main>
  );
}
