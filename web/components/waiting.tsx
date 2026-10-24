"use client";

import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Mark } from "@/components/evidence";
import { api, type EmailRow, type RequestRow } from "@/lib/api";
import { category, dayShort, missingPaperwork, PAPERWORK_ACTION, rulePhrase, shortName } from "@/lib/labels";
import { cn } from "@/lib/utils";

const FOCUS = "rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
const time = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });

/**
 * "Waiting for documents" (DESIGN.md): requests held only for paperwork, each missing piece with what to do,
 * then the month's email. Built from what the log already has; attachments that needed a person aren't logged yet.
 */
export function Waiting({ rows, month, version, wide, requestHref }: { rows: RequestRow[]; month: number; version: number; wide: boolean; requestHref: (id: string) => string }) {
  const [emails, setEmails] = useState<EmailRow[] | null>(null);
  useEffect(() => {
    api.emails(month).then(setEmails, () => setEmails([]));
  }, [month, version]);

  const waiting = rows.filter((r) => category(r) === "paperwork");
  if (!waiting.length && !emails?.length) return null;

  return (
    <details open={waiting.length > 0} className="group rounded-lg border bg-sheet">
      <summary className={cn("flex min-h-14 cursor-pointer list-none items-center gap-3 px-4 select-none [&::-webkit-details-marker]:hidden", FOCUS)}>
        <h2 className="text-[1.3125rem] font-semibold tracking-tight">Waiting for documents</h2>
        <span className="font-mono text-muted-foreground tnum">{waiting.length}</span>
        <ChevronDown className="ml-auto size-5 text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden />
      </summary>
      <div className={cn("grid gap-x-10 gap-y-2 border-t px-4 pt-3 pb-4", wide && "xl:grid-cols-2")}>
        <section aria-label="Paperwork missing">
          {waiting.length === 0 ? (
            <p className="text-muted-foreground">Nothing missing.</p>
          ) : (
            <ul>
              {waiting.map((r) => (
                <li key={r.id} className="border-b py-3 last:border-b-0">
                  <Link href={requestHref(r.id)} className={cn("font-semibold underline-offset-[3px] hover:underline", FOCUS)}>
                    {shortName(r.sub)}
                  </Link>
                  <span className="text-meta text-muted-foreground"> {r.job}</span>
                  <ul className="mt-1.5 space-y-1.5">
                    {missingPaperwork(r).map((n) => {
                      const step = PAPERWORK_ACTION[n];
                      return (
                        <li key={n} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                          <span className="flex items-center gap-1.5">
                            <Mark kind="fail" />
                            {rulePhrase(n, r)}
                          </span>
                          {step.kind && step.act ? (
                            <Link href={`/upload?kind=${step.kind}&sub=${encodeURIComponent(r.sub_id)}`} className={cn("text-primary underline underline-offset-[3px]", FOCUS)}>
                              {step.act}
                            </Link>
                          ) : step.act ? (
                            <Link href={requestHref(r.id)} className={cn("text-primary underline underline-offset-[3px]", FOCUS)}>
                              {step.act}
                            </Link>
                          ) : (
                            <span className="text-meta text-muted-foreground">{step.say}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="mail-in">
          <h3 id="mail-in" className="pt-3 text-meta font-semibold text-muted-foreground">Email this month</h3>
          {!emails ? null : emails.length === 0 ? (
            <p className="mt-2 text-muted-foreground">No email has come in.</p>
          ) : (
            <ul className="mt-1">
              {emails.map((e) => (
                <li key={e.id} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2.5 border-b py-2.5 last:border-b-0">
                  <Mark kind={e.sub ? "pass" : "fail"} />
                  <div className="min-w-0">
                    <p className="truncate">{e.subject || "(no subject)"}</p>
                    <p className="text-meta text-muted-foreground">
                      <span className="font-mono [overflow-wrap:anywhere]">{e.from_addr}</span>
                      {", "}
                      {dayShort(e.sent_at)} {time(e.sent_at)}
                    </p>
                    <p className={cn("text-meta", e.sub ? "text-pass" : "text-fail")}>{e.sub ? shortName(e.sub) : "Not a known sub"}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </details>
  );
}
