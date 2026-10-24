"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Mark } from "@/components/evidence";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, money, type RequestRow } from "@/lib/api";
import { day } from "@/lib/labels";
import { cn } from "@/lib/utils";

const MONTH = 6; // this month: October 2026
const WAIT_SECONDS = 90;

type Outcome = { kind: "waiting" } | { kind: "paid" } | { kind: "refused"; reason: string } | { kind: "error"; reason: string };

const FOCUS = "rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50";

export default function ApprovePage() {
  const { version } = useLive();
  const [rows, setRows] = useState<RequestRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Record<string, Outcome>>({});
  const [left, setLeft] = useState<number | null>(null);

  useEffect(() => {
    api.requests(MONTH).then(
      (r) => (setRows(r.filter((x) => x.status === "needs_ok")), setError(null)),
      (e: ApiError) => setError(e.message),
    );
  }, [version]);

  useEffect(() => {
    if (!left) return;
    const t = setTimeout(() => setLeft((s) => (s ? s - 1 : s)), 1000);
    return () => clearTimeout(t);
  }, [left]);

  const send = async (id: string) => {
    setOutcome((o) => ({ ...o, [id]: { kind: "waiting" } }));
    setLeft(WAIT_SECONDS);
    try {
      const r = await api.approve(id);
      setOutcome((o) => ({
        ...o,
        [id]: r.paid ? { kind: "paid" } : { kind: "refused", reason: r.rejected ? "You pressed red on the approval key. Nothing was paid." : r.reason ?? "Refused." },
      }));
    } catch (e) {
      setOutcome((o) => ({ ...o, [id]: { kind: "error", reason: (e as ApiError).message } }));
    }
    setLeft(null);
  };

  const busy = Object.values(outcome).some((o) => o.kind === "waiting");
  const [lead, ...rest] = [...(rows ?? [])].sort((a, b) => Number(b.amount) - Number(a.amount));

  const sendButton = (r: RequestRow) =>
    outcome[r.id]?.kind !== "paid" && (
      <Button className="h-13 px-6 text-lg font-semibold" onClick={() => send(r.id)} disabled={busy}>
        {outcome[r.id]?.kind === "waiting" ? "Waiting for the key" : "Send to the approval key"}
      </Button>
    );
  const checksLink = (r: RequestRow) => (
    <Link href={`/?r=${r.id}`} className={cn("text-body text-primary underline underline-offset-[3px]", FOCUS)}>
      See every check
    </Link>
  );

  return (
    <main className="mx-auto w-full max-w-[1024px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <h1 className="text-[27px] leading-tight font-semibold tracking-[-0.02em] sm:text-lede">Payments waiting for your OK</h1>
      <p className="mt-3 max-w-[60ch] text-body text-muted-foreground">
        Every check passed on these. They need you because of their size, a new sub, or new bank details. Nothing goes out until you
        press green on the approval key on your desk.
      </p>

      {error && (
        <div role="alert" className="mt-8 rounded-lg border border-fail/30 bg-fail-soft p-4 text-meta text-fail">
          {error}
        </div>
      )}
      {rows === null && !error && <Skeleton className="mt-8 h-72 w-full" />}
      {rows?.length === 0 && <p className="mt-8 rounded-lg border bg-sheet p-6 text-body">Nothing is waiting for you this month.</p>}

      {lead && (
        <article aria-labelledby={`a-${lead.id}`} className="mt-8 grid gap-8 rounded-lg border bg-sheet px-4 py-5 sm:p-8 min-[860px]:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0">
            <p className="font-mono text-hero font-medium tracking-[-0.03em] tnum">{money(lead.amount)}</p>
            <p id={`a-${lead.id}`} className="mt-3 text-[22px] leading-tight font-semibold">
              {lead.sub}
            </p>
            <p className="mt-1 text-meta text-muted-foreground">
              {lead.job}, period ending {day(lead.period_end)}
            </p>
            <p className="mt-4 max-w-[46ch] text-body">{lead.reason ? `You're asked because ${lead.reason}.` : lead.sentence}</p>
            {lead.new_sub_note && <p className="mt-2 max-w-[46ch] text-body">{lead.new_sub_note}</p>}
            <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-3">
              {sendButton(lead)}
              {checksLink(lead)}
            </div>
            <Status outcome={outcome[lead.id]} left={left} />
            <details className="mt-6">
              <summary className={cn("flex min-h-12 w-fit cursor-pointer list-none items-center text-body text-primary underline underline-offset-[3px] select-none [&::-webkit-details-marker]:hidden", FOCUS)}>
                What your tap signs
              </summary>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-sm">
                {[
                  ["request_id", lead.id],
                  ["payee", lead.sub],
                  ["amount", lead.amount],
                  ["job", lead.job_id],
                  ["sub_id", lead.sub_id],
                ].map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="tnum [overflow-wrap:anywhere]">{v}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-2 text-meta text-muted-foreground">The signature covers this exact payee and amount and pays once.</p>
            </details>
          </div>

          <figure aria-label="What the approval key on your desk shows" className="m-0 self-start rounded-[14px] border-2 border-foreground bg-background p-[18px]">
            <figcaption className="mb-3 text-sm text-muted-foreground">What the key on your desk shows</figcaption>
            <div className="rounded-md border border-border-strong bg-sheet p-4 font-mono">
              <p className="text-[30px] leading-tight font-semibold tracking-[-0.02em] tnum">{money(lead.amount)}</p>
              <p className="mt-1 text-meta">{lead.sub}</p>
            </div>
            <div aria-hidden className="mt-4 flex justify-around text-sm font-semibold">
              {[
                ["Pay", "text-pass", "bg-pass-soft"],
                ["Refuse", "text-fail", "bg-fail-soft"],
              ].map(([label, ink, soft]) => (
                <span key={label} className={cn("flex flex-col items-center gap-1.5", ink)}>
                  <i className={cn("block size-12 rounded-full border-[3px] border-current", soft, outcome[lead.id]?.kind === "waiting" && "motion-safe:animate-pulse")} />
                  {label}
                </span>
              ))}
            </div>
          </figure>
        </article>
      )}

      {rest.map((r) => (
        <article key={r.id} aria-labelledby={`a-${r.id}`} className="mt-5 rounded-lg border bg-sheet px-4 py-5 sm:px-6">
          <div className="grid items-center gap-x-6 gap-y-3 sm:grid-cols-[minmax(0,1fr)_auto]">
            <div className="min-w-0">
              <p className="font-mono text-[30px] leading-tight font-medium tracking-[-0.02em] tnum">{money(r.amount)}</p>
              <p id={`a-${r.id}`} className="mt-1 text-lg font-semibold">
                {r.sub}
              </p>
              <p className="text-meta text-muted-foreground">{r.job}</p>
              <p className="mt-2 max-w-[46ch] text-body">{r.reason ? `You're asked because ${r.reason}.` : r.sentence}</p>
              {r.new_sub_note && <p className="mt-1 max-w-[46ch] text-body">{r.new_sub_note}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              {sendButton(r)}
              {checksLink(r)}
            </div>
          </div>
          <Status outcome={outcome[r.id]} left={left} />
        </article>
      ))}
    </main>
  );
}

/** One line per state, each with its own mark shape and words, so it never relies on colour. */
function Status({ outcome: o, left }: { outcome?: Outcome; left: number | null }) {
  let line = null;
  if (o?.kind === "waiting")
    line = (
      <>
        <Mark kind="ok" />
        <span>
          Waiting for the button on the owner&apos;s desk. Green pays, red refuses. <span className="font-mono tnum">{left ?? 0}</span> seconds left.
        </span>
      </>
    );
  else if (o?.kind === "paid")
    line = (
      <>
        <Mark kind="pass" />
        <span className="text-pass">Signed on the key and paid. It&apos;s in the log.</span>
      </>
    );
  else if (o) {
    const expired = /timeout|expired/i.test(o.reason);
    line = (
      <>
        <Mark kind="fail" />
        <span className="text-fail">
          {expired
            ? "The key wasn't pressed in time, so nothing was paid. Send it again when you're at your desk."
            : /nothing was paid/i.test(o.reason)
              ? o.reason
              : `Nothing was paid. ${o.reason}`}
        </span>
      </>
    );
  }
  return (
    <p aria-live="polite" className="mt-4 flex min-h-[1.5em] items-start gap-2 text-body">
      {line}
    </p>
  );
}
