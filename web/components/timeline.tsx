"use client";

import { useEffect, useState } from "react";
import { Mark, MarkRow } from "@/components/evidence";
import { api, money, type RequestEvents, type Step } from "@/lib/api";
import { jevAnswer, JEV_QUESTION } from "@/lib/labels";

const ACTION_WORD: Record<string, string> = { release: "ready to pay", escalate: "needs your OK", hold: "on hold" };
const when = (iso: string) =>
  new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function word(s: Step): string {
  switch (s.kind) {
    case "received":
      return s.read_from_file ? "Received and read from the file" : "Received";
    case "reviewed":
      return s.source === "claude" ? "Reviewed by Claude (Jev was unreachable)" : s.source === "jev" ? "Reviewed by Jev" : "Review couldn't run";
    case "checked":
      return `Checked: ${ACTION_WORD[s.action] ?? s.action}${s.times > 1 ? `, ${s.times} times` : ""}`;
    case "signed":
      return s.rejected ? "Refused on the key" : "Signed on the key";
    case "paid":
      return s.amount ? `Paid ${money(s.amount)}` : "Paid";
    case "payment_failed":
      return "The bank didn't take the payment";
  }
}

const mark = (s: Step) =>
  s.kind === "reviewed" && s.source !== "jev" && s.source !== "claude"
    ? "fail"
    : s.kind === "checked"
    ? s.action === "hold" ? "fail" : s.action === "escalate" ? "ok" : "pass" // a check's mark is its result
    : s.kind === "payment_failed" || (s.kind === "signed" && s.rejected) ? "fail" : s.kind === "signed" ? "ok" : "pass";
const SR = { pass: "Done:", fail: "Held or failed:", ok: "Needs the owner:" } as const;

/** What happened to this request, from the event log only, and what the review said (DESIGN.md "Timeline", "Review"). */
export function Timeline({ id, version }: { id: string; version: number }) {
  const [data, setData] = useState<RequestEvents | null>(null);
  useEffect(() => {
    let live = true;
    api.requestEvents(id).then((d) => live && setData(d), () => live && setData({ steps: [], threshold: null }));
    return () => {
      live = false;
    };
  }, [id, version]);
  if (!data) return null;

  // Re-checks append to the log (it's append-only), so earlier reviews and decisions were replaced:
  // show the latest review and the current decision once, counting how often it was checked.
  const latest = (k: Step["kind"]) => [...data.steps].reverse().find((s) => s.kind === k);
  const review = latest("reviewed");
  const check = latest("checked");
  const checks = data.steps.filter((s) => s.kind === "checked").reduce((n, s) => n + (s.kind === "checked" ? s.times : 0), 0);
  const shown = [
    ...data.steps.filter((s) => s.kind === "received"),
    ...(review ? [review] : []),
    ...(check && check.kind === "checked" ? [{ ...check, times: checks }] : []),
    ...data.steps.filter((s) => s.kind === "signed" || s.kind === "paid" || s.kind === "payment_failed"),
  ];
  const floor = data.threshold;
  return (
    <div className="mt-8 grid gap-x-8 gap-y-6 border-t pt-6 xl:grid-cols-2">
      <section aria-labelledby="timeline">
        <h3 id="timeline" className="mb-2 text-lg font-semibold">Timeline</h3>
        <ul className="border-t">
          {shown.map((s) => (
            <MarkRow key={s.seq} kind={mark(s)} sr={SR[mark(s)]}>
              {word(s)} <span className="text-meta text-muted-foreground">{when(s.at)}</span>
            </MarkRow>
          ))}
        </ul>
      </section>
      <section aria-labelledby="review">
        <h3 id="review" className="mb-2 text-lg font-semibold">Review</h3>
        {!review || review.kind !== "reviewed" ? (
          <p className="text-body text-muted-foreground">No review has run on this request yet.</p>
        ) : (
          <ul className="border-t">
            {Object.entries(review.answers).map(([q, a]) => {
              const ok = floor == null || a.confidence == null || a.confidence >= floor;
              return (
                <li key={q} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2.5 border-b py-2.5">
                  <Mark kind={ok ? "pass" : "fail"} />
                  <div>
                    <p className="text-meta text-muted-foreground">{JEV_QUESTION[q] ?? q.replaceAll("_", " ")}</p>
                    <p>
                      <span className="font-semibold">{jevAnswer(q, a.value)}</span>
                      {a.confidence != null && (
                        <span className="text-muted-foreground">
                          {", "}
                          <span className="font-mono tnum">{Math.round(a.confidence * 100)}%</span> sure
                        </span>
                      )}
                    </p>
                    {!ok && floor != null && <p className="text-meta text-fail">Below the {Math.round(floor * 100)}% floor</p>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
