"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, type LogEvent, type LogKind } from "@/lib/api";
import { cn } from "@/lib/utils";

const KINDS: { kind: LogKind | ""; label: string }[] = [
  { kind: "", label: "All entries" },
  { kind: "fact", label: "Documents and bank records" },
  { kind: "judgment", label: "Reviews" },
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
// a fact is named by the table it went into
const TABLE_WORD: Record<string, string> = {
  pay_requests: "Pay application",
  ledger: "Bank",
  vendors: "Vendor",
  jobs: "Job",
  releases: "Lien release",
  cois: "Insurance",
  emails: "Email",
  site_evidence: "Site video",
  lower_tier_notices: "Notice to owner",
};
const PAGE = 100;

const FOCUS = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
const FIELD = cn("h-10 rounded-md border border-border-strong bg-sheet px-3 text-[15px]", FOCUS);
const TH = "align-bottom border-b-2 border-foreground px-2 py-2 text-left text-meta font-medium text-muted-foreground sm:px-3";
const TD = "border-b px-2 py-2.5 align-top sm:px-3";

const entryType = (e: LogEvent) =>
  e.kind === "fact" && e.table ? (TABLE_WORD[e.table] ?? e.table.replace(/_/g, " ")) : (KIND_WORD[e.kind] ?? e.kind);

/** "Oct 9, 2026" for the business date the row is about. */
const day = (iso: string) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
/** "1:24 PM", or nothing for a row dated to the day (seed facts sit at midnight). */
const clock = (iso: string) =>
  iso.slice(11, 19) === "00:00:00" ? "" : new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
/** "Oct 24, 2026, 1:24:56 PM": the clock the server wrote the row with. */
const stamp = (iso: string) =>
  new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });

function RequestLink({ id, className }: { id: string; className?: string }) {
  return (
    <Link href={`/?r=${encodeURIComponent(id)}`} className={cn("font-mono text-[13px] text-primary underline underline-offset-[3px]", FOCUS, className)}>
      {id}
    </Link>
  );
}

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
  const [open, setOpen] = useState<Set<number>>(new Set());
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

  const toggle = (seq: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(seq)) next.add(seq);
      return next;
    });

  return (
    <main className="mx-auto w-full max-w-[1164px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h1 className="text-title font-semibold tracking-[-0.01em]">Event log</h1>
        <p className="text-meta text-muted-foreground">{connected ? "Updating live" : "Live updates paused"}</p>
      </div>
      <p className="mt-1 text-meta text-muted-foreground">Append-only. Newest entry first.</p>

      <form
        className="mt-6 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setRequest(draft.trim());
        }}
      >
        <label className="block">
          <span className="block text-meta text-muted-foreground">Type</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as LogKind | "")} className={cn(FIELD, "mt-1")}>
            {KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="block text-meta text-muted-foreground">Pay request</span>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="pr_sub_03_6"
            autoComplete="off"
            spellCheck={false}
            className={cn(FIELD, "mt-1 w-52 font-mono")}
          />
        </label>
        <Button type="submit" variant="outline" className="border-border-strong">
          Filter
        </Button>
        {request && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setDraft("");
              setRequest("");
            }}
          >
            Clear
          </Button>
        )}
      </form>

      {error && (
        <div role="alert" className="mt-6 rounded-lg border border-fail/30 bg-fail-soft p-4 text-meta text-fail">
          {error}
        </div>
      )}
      {rows === null && !error && <Skeleton className="mt-6 h-96 w-full" />}
      {rows?.length === 0 && <p className="mt-6 border-y py-6 text-meta text-muted-foreground">No entries for this filter.</p>}

      {rows && rows.length > 0 && (
        <div className="mt-6 overflow-x-auto rounded-lg border bg-sheet">
          <table className="w-full border-collapse text-[15px]">
            <thead>
              <tr>
                <th className={cn(TH, "w-12 text-right sm:w-16")}>No.</th>
                <th className={cn(TH, "w-28 sm:w-32")}>Date</th>
                <th className={cn(TH, "hidden w-36 sm:table-cell")}>Type</th>
                <th className={TH}>Entry</th>
                <th className={cn(TH, "hidden w-36 md:table-cell")}>Request</th>
                <th className={cn(TH, "hidden w-16 sm:table-cell")}>
                  <span className="sr-only">Details</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => {
                const isOpen = open.has(e.seq);
                const detailsToggle = (
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={`raw-${e.seq}`}
                    onClick={() => toggle(e.seq)}
                    className={cn("text-[13px] text-primary underline underline-offset-[3px]", FOCUS)}
                  >
                    {isOpen ? "Hide" : "Details"}
                    <span className="sr-only"> for entry {e.seq}</span>
                  </button>
                );
                return (
                  <Fragment key={e.seq}>
                    <tr className={cn("hover:bg-background", isOpen && "bg-background")}>
                      <td className={cn(TD, "text-right font-mono text-muted-foreground tnum")}>{e.seq}</td>
                      <td className={cn(TD, "whitespace-nowrap tnum")}>
                        {day(e.at)}
                        {clock(e.at) && <span className="block text-[13px] text-muted-foreground">{clock(e.at)}</span>}
                      </td>
                      <td className={cn(TD, "hidden whitespace-nowrap sm:table-cell")}>{entryType(e)}</td>
                      <td className={TD}>
                        <span className="block text-[13px] text-muted-foreground sm:hidden">{entryType(e)}</span>
                        {e.line}
                        {e.request_id && <RequestLink id={e.request_id} className="mt-0.5 block w-fit md:hidden" />}
                        <span className="mt-0.5 block sm:hidden">{detailsToggle}</span>
                      </td>
                      <td className={cn(TD, "hidden whitespace-nowrap md:table-cell")}>
                        {e.request_id ? <RequestLink id={e.request_id} /> : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className={cn(TD, "hidden text-right sm:table-cell")}>{detailsToggle}</td>
                    </tr>
                    {isOpen && (
                      <tr id={`raw-${e.seq}`} className="bg-background">
                        <td className="hidden border-b sm:table-cell" />
                        <td colSpan={5} className="border-b px-2 pb-4 sm:px-3">
                          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-[13px]">
                            <dt className="text-muted-foreground">Recorded</dt>
                            <dd className="font-mono tnum">{stamp(e.ts)}</dd>
                            <dt className="text-muted-foreground">Effective</dt>
                            <dd className="font-mono tnum">{stamp(e.at)}</dd>
                            <dt className="text-muted-foreground">Kind</dt>
                            <dd className="font-mono">
                              {e.kind}
                              {e.table && ` / ${e.table}`}
                            </dd>
                          </dl>
                          <pre className="mt-2 max-h-[360px] overflow-auto rounded-[4px] border bg-sheet p-3 font-mono text-[13px] leading-normal whitespace-pre-wrap">
                            {JSON.stringify(e.data, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows && rows.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-meta text-muted-foreground">
          <span className="tnum">
            {rows.length.toLocaleString("en-US")} {rows.length === 1 ? "entry" : "entries"} shown
          </span>
          {more && (
            <Button variant="outline" className="border-border-strong" onClick={loadOlder} disabled={older}>
              {older ? "Loading…" : "Load older entries"}
            </Button>
          )}
        </div>
      )}
    </main>
  );
}
