"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Evidence, failedRule, MarkRow } from "@/components/evidence";
import { StatusChip } from "@/components/status-chip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, money, moneyShort, ruleNumber, type RequestDetail } from "@/lib/api";
import { day, dayShort, humanize, rowLine, RULE_NAMES, shortName } from "@/lib/labels";
import { cn } from "@/lib/utils";

const ESCALATION_RULES = new Set(["14_within_spend_authority", "15_confidence_floor"]);

/** The status sentence, built from the evidence (names, numbers, dates all from the API); null when it can't be. */
function say(d: RequestDetail): string | null {
  if (d.status !== "on_hold") return null;
  const name = shortName(d.sub);
  const o = failedRule(d, "13")?.evidence.overstatements?.[0];
  const line = o && d.lines.find((l) => l.item_no === o.item_no);
  if (o && line && d.site_evidence)
    return `On hold: ${name} billed ${line.description.toLowerCase()} at ${Number(o.claimed_pct)}%, but the site video from ${dayShort(d.site_evidence.date)} shows ${Number(o.video_estimate_pct)}%.`;
  if (failedRule(d, "10") && failedRule(d, "11")?.evidence.mismatches?.length)
    return `On hold: an email that looks like it's from ${name} asks us to pay a new bank account. It came from a lookalike domain.`;
  const notices = failedRule(d, "07")?.evidence.unresolved ?? [];
  if (notices.length === 1)
    return `On hold: ${name}'s supplier, ${notices[0].supplier}, filed a notice to owner and hasn't released.`;
  return null;
}

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
  const kind = (r: (typeof results)[number]) => (r.passed ? "pass" : ESCALATION_RULES.has(r.rule) ? "ok" : "fail");
  const word = { pass: "Passed:", fail: "Didn't pass:", ok: "Needs the owner:" } as const;
  const attention = results.filter((r) => !r.passed);
  const passed = results.filter((r) => r.passed);
  const flagged = new Set((failedRule(data, "13")?.evidence.overstatements ?? []).map((o) => o.item_no));
  const limit = failedRule(data, "14")?.evidence.limit as string | undefined;
  const sentence = say(data);

  const item = (r: (typeof results)[number]) => {
    const name = RULE_NAMES[ruleNumber(r.rule)];
    return (
      <MarkRow key={r.rule} kind={kind(r)} sr={word[kind(r)]}>
        {name && <b className="font-semibold">{name}.</b>} {humanize(r.message)}
      </MarkRow>
    );
  };

  return (
    <article aria-labelledby="d-title" className="-mx-4 bg-sheet px-4 py-4 sm:-mx-8 sm:px-8 lg:mx-0 lg:rounded-lg lg:border lg:p-8">
      <Link href={backHref} className="mb-3 inline-flex min-h-11 items-center text-primary underline underline-offset-[3px] lg:hidden">
        Back to pay requests
      </Link>
      <header className="flex flex-col gap-2">
        <div>
          <h2 id="d-title" className="text-title font-semibold tracking-tight text-balance">{data.sub}</h2>
          <p className="text-muted-foreground">
            {data.job}, period ending {day(data.period_end)}
          </p>
        </div>
        <p className="font-mono text-amount font-medium tracking-tight tnum">
          {money(data.amount)} <span className="font-sans text-[15px] font-normal tracking-normal text-muted-foreground">this period</span>
        </p>
      </header>
      {sentence ? (
        <p className="mt-5 max-w-[40ch] text-say font-medium">{sentence}</p>
      ) : (
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <StatusChip status={data.status} />
          <p className="text-say font-medium">{rowLine(data.status, data.sentence, data.reason)}</p>
        </div>
      )}
      <div className="mt-5 flex flex-col items-start gap-2.5">
        <NextAction detail={data} />
        {data.status === "on_hold" && limit && (
          <p className="max-w-[52ch] text-base text-muted-foreground">
            Once that&apos;s settled, this still needs the owner&apos;s OK: it&apos;s over the {moneyShort(limit)} limit.
          </p>
        )}
      </div>

      <Evidence detail={data} />

      {results.length > 0 && (
        <section aria-labelledby="checks" className="mt-8 border-t pt-6">
          <h3 id="checks" className="mb-3.5 text-lg font-semibold">
            {results.length} checks, {passed.length} passed
          </h3>
          {attention.length > 0 && (
            <>
              <p className="mb-1.5 text-base text-muted-foreground">Needs attention</p>
              <ul className="border-t">{attention.map(item)}</ul>
            </>
          )}
          {passed.length > 0 && (
            <>
              <p className={cn("mb-1.5 text-base text-muted-foreground", attention.length > 0 && "mt-[18px]")}>Passed</p>
              <ul className="border-t">{passed.map(item)}</ul>
            </>
          )}
        </section>
      )}

      <section aria-labelledby="lines" className="mt-8 border-t pt-6">
        <h3 id="lines" className="mb-3.5 text-lg font-semibold">Lines on this request</h3>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-base">
            <thead className="text-left text-sm text-muted-foreground">
              <tr className="border-b border-foreground">
                <th className="px-2 py-1.5 font-medium">Item</th>
                <th className="px-2 py-1.5 font-medium">Description</th>
                <th className="px-2 py-1.5 text-right font-medium">Billed</th>
                <th className="px-2 py-1.5 text-right font-medium">Video</th>
                <th className="px-2 py-1.5 text-right font-medium">To date</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((l) => {
                const flag = flagged.has(l.item_no);
                return (
                  <tr key={l.item_no} className={cn("border-b", flag && "bg-hold-soft")}>
                    <td className="px-2 py-[9px] font-mono whitespace-nowrap">{l.item_no}</td>
                    <td className="px-2 py-[9px]">
                      {l.description}
                      {flag && <span className="sr-only"> (claims more than the site video shows)</span>}
                    </td>
                    <td className={cn("px-2 py-[9px] text-right font-mono whitespace-nowrap tnum", flag && "font-semibold text-hold")}>{Number(l.pct_complete)}%</td>
                    <td className="px-2 py-[9px] text-right font-mono whitespace-nowrap tnum">{l.video_pct === null ? "none" : `${Number(l.video_pct)}%`}</td>
                    <td className="px-2 py-[9px] text-right font-mono whitespace-nowrap tnum">{money(l.total_to_date)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {data.site_evidence && <p className="mt-2 text-sm text-muted-foreground">Video from the site walk on {day(data.site_evidence.date)}.</p>}
      </section>

      {data.trace && (
        <details className="mt-8 border-t pt-6">
          <summary className="flex min-h-12 cursor-pointer list-none items-center text-primary underline underline-offset-[3px] select-none [&::-webkit-details-marker]:hidden">
            Show details
          </summary>
          <pre className="mt-2 max-h-[480px] overflow-auto rounded-[4px] bg-muted p-4 font-mono text-[13px] leading-normal whitespace-pre-wrap">
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
  if (/^hold\b/i.test(action)) {
    // some rules' next step is "keep holding", not something to send: say it, don't make it a button
    return <p className="max-w-[52ch] text-base text-muted-foreground">{action.replace(/^hold;?\s*/i, "Keep it on hold: ")}.</p>;
  }

  if (/call/i.test(action)) {
    return (
      <div className="flex flex-col items-start gap-2.5">
        <Button size="lg" onClick={() => setShown(true)}>
          Call to verify the new bank details
        </Button>
        {shown && (
          <p className="max-w-[52ch] text-base text-muted-foreground">
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
      {action}
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
