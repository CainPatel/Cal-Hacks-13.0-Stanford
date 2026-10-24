"use client";

import Link from "next/link";
import { StatusMark } from "@/components/status-chip";
import { money, type RequestRow } from "@/lib/api";
import { CATEGORY, category, CATEGORY_ORDER, shortName, type Category } from "@/lib/labels";
import { cn } from "@/lib/utils";

export type View = "job" | "status";

const FOCUS = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";

/**
 * The month at a glance (web/DESIGN.md "Month overview"): one bar per job, or the status board.
 * Only one view shows at a time; the page keeps the choice in the URL.
 */
export function MonthOverview({ rows, view, onView, requestHref }: {
  rows: RequestRow[];
  view: View;
  onView: (v: View) => void;
  requestHref: (id: string) => string;
}) {
  const items = rows.map((r) => ({ ...r, cat: category(r) }));
  const present = CATEGORY_ORDER.filter((c) => items.some((r) => r.cat === c));
  const of = (c: Category, list = items) => list.filter((r) => r.cat === c);
  const dollars = (list: typeof items) => list.reduce((s, r) => s + Number(r.amount), 0);

  return (
    <div className="mt-6">
      <div role="group" aria-label="Show the month" className="inline-flex rounded-lg border border-border-strong bg-sheet p-1">
        {(["job", "status"] as const).map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={view === v}
            onClick={() => onView(v)}
            className={cn(
              "min-h-12 rounded-md px-5 text-body font-medium",
              FOCUS,
              view === v ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {v === "job" ? "By job" : "By status"}
          </button>
        ))}
      </div>

      {view === "job" ? <ByJob items={items} of={of} /> : <ByStatus present={present} of={of} requestHref={requestHref} />}

      <ul className="mt-5 flex flex-wrap gap-x-7 gap-y-2" aria-label="Totals by status">
        {present.map((c) => (
          <li key={c} className="flex items-center gap-2 text-body">
            <span className={cn("inline-flex size-6 items-center justify-center rounded", CATEGORY[c].fill)}>
              <StatusMark category={c} className="size-3.5" />
            </span>
            <span className="font-mono font-semibold tnum">{of(c).length}</span>
            <span>{CATEGORY[c].label}</span>
            <span className="font-mono text-muted-foreground tnum">{money(dollars(of(c)))}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type Item = RequestRow & { cat: Category };

function ByJob({ items, of }: { items: Item[]; of: (c: Category, list?: Item[]) => Item[] }) {
  const jobs = [...new Map(items.map((r) => [r.job_id, r.job])).entries()].map(([id, name]) => ({
    id,
    name,
    list: items.filter((r) => r.job_id === id),
  }));
  // worst job first: compare fraud counts, then waiting, then needs-your-OK
  const need = (list: Item[]) => CATEGORY_ORDER.slice(0, 3).map((c) => of(c, list).length);
  jobs.sort((a, b) => {
    const [x, y] = [need(a.list), need(b.list)];
    return y[0] - x[0] || y[1] - x[1] || y[2] - x[2] || a.name.localeCompare(b.name);
  });

  return (
    <ul className="mt-5 space-y-4 md:space-y-3" aria-label="Pay requests by job">
      {jobs.map((j) => {
        const parts = CATEGORY_ORDER.map((c) => [c, of(c, j.list).length] as const).filter(([, n]) => n > 0);
        return (
          <li key={j.id} className="grid items-center gap-x-5 gap-y-1.5 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)_auto]">
            <a href={`#job-${j.id}`} className={cn("truncate rounded-sm font-semibold underline-offset-[3px] hover:underline", FOCUS)}>
              {j.name}
            </a>
            <div className="flex h-8 gap-[2px] overflow-hidden rounded" aria-hidden>
              {parts.map(([c, n]) => (
                <span key={c} title={`${n} ${CATEGORY[c].label}`} className={cn("flex items-center justify-center", CATEGORY[c].fill)} style={{ flex: n }}>
                  <StatusMark category={c} className="size-3.5" />
                </span>
              ))}
            </div>
            <p className="flex gap-3 text-meta md:justify-end">
              {parts.map(([c, n]) => (
                <span key={c} className="inline-flex items-center gap-1">
                  <StatusMark category={c} className="size-3.5" />
                  <span className="font-mono tnum">{n}</span>
                  <span className="sr-only">{CATEGORY[c].label}</span>
                </span>
              ))}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

function ByStatus({ present, of, requestHref }: { present: Category[]; of: (c: Category) => Item[]; requestHref: (id: string) => string }) {
  return (
    <div className="mt-5 grid grid-cols-1 items-start gap-3 min-[480px]:grid-cols-2 lg:flex">
      {present.map((c) => {
        const list = of(c).sort((a, b) => Number(b.amount) - Number(a.amount));
        return (
          <section key={c} aria-label={CATEGORY[c].label} className={cn("min-w-0 rounded-lg border border-t-4 bg-sheet p-3 lg:flex-1", CATEGORY[c].tone)}>
            <h3 className="flex items-start justify-between gap-2 pb-2 text-meta font-semibold">
              <span className="flex items-start gap-1.5">
                <StatusMark category={c} className="mt-[3px]" />
                {CATEGORY[c].label}
              </span>
              <span className="font-mono tnum">{list.length}</span>
            </h3>
            <ul>
              {list.map((r) => (
                <li key={r.id} className="border-t">
                  <Link
                    href={requestHref(r.id)}
                    className={cn("flex min-h-11 items-center justify-between gap-2 rounded-sm px-1 text-[15px] hover:bg-muted", FOCUS)}
                  >
                    <span className="min-w-0 truncate">{shortName(r.sub)}</span>
                    <span className="font-mono text-muted-foreground tnum">{money(r.amount)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
