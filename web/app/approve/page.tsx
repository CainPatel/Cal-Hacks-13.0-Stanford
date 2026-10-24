"use client";

import { CircleCheck, CircleX, KeyRound, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/hooks/use-live";
import { api, ApiError, money, type RequestRow } from "@/lib/api";

const MONTH = 6; // this month: October 2026

type Outcome = { kind: "waiting" } | { kind: "paid" } | { kind: "refused"; reason: string } | { kind: "error"; reason: string };

export default function ApprovePage() {
  const { version } = useLive();
  const [rows, setRows] = useState<RequestRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Record<string, Outcome>>({});

  useEffect(() => {
    api.requests(MONTH).then(
      (r) => (setRows(r.filter((x) => x.status === "needs_ok")), setError(null)),
      (e: ApiError) => setError(e.message),
    );
  }, [version]);

  const send = async (id: string) => {
    setOutcome((o) => ({ ...o, [id]: { kind: "waiting" } }));
    try {
      const r = await api.approve(id);
      setOutcome((o) => ({
        ...o,
        [id]: r.paid ? { kind: "paid" } : { kind: "refused", reason: r.rejected ? "You pressed red on the approval key. Nothing was paid." : r.reason ?? "Refused." },
      }));
    } catch (e) {
      setOutcome((o) => ({ ...o, [id]: { kind: "error", reason: (e as ApiError).message } }));
    }
  };

  return (
    <main className="mx-auto w-full max-w-[880px] flex-1 space-y-8 px-4 py-6 sm:px-6 lg:py-10">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Payments waiting for your OK</h1>
        <p className="max-w-prose text-[17px] leading-snug text-muted-foreground">
          Every check passed on these. They need you because of their size, a new sub, or new bank details. Nothing goes out until you
          press green on the approval key on your desk.
        </p>
      </header>

      {error && (
        <div role="alert" className="rounded-lg border border-fail/30 bg-fail-soft p-4 text-[15px] text-fail">
          {error}
        </div>
      )}
      {rows === null && !error && <Skeleton className="h-56 w-full" />}
      {rows?.length === 0 && (
        <p className="rounded-lg border bg-sheet p-6 text-[17px]">Nothing is waiting for you this month.</p>
      )}

      <ul className="space-y-5">
        {rows?.map((r) => {
          const o = outcome[r.id];
          return (
            <li key={r.id} className="rounded-xl border bg-sheet p-6 sm:p-8">
              <p className="font-mono text-5xl font-medium tracking-tight tnum sm:text-6xl">{money(r.amount)}</p>
              <p className="mt-3 text-xl font-medium">{r.sub}</p>
              <p className="text-[15px] text-muted-foreground">{r.job}</p>
              {r.reason && <p className="mt-4 text-[17px]">You&apos;re asked because {r.reason}.</p>}

              <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-3">
                {o?.kind !== "paid" && (
                  <Button size="lg" onClick={() => send(r.id)} disabled={o?.kind === "waiting"}>
                    {o?.kind === "waiting" ? <LoaderCircle className="animate-spin" aria-hidden /> : <KeyRound aria-hidden />}
                    {o?.kind === "waiting" ? "Waiting for the key…" : "Send to the approval key"}
                  </Button>
                )}
                <Link href={`/?r=${r.id}`} className="text-[15px] text-primary underline">
                  See every check
                </Link>
              </div>

              <div aria-live="polite" className="mt-4 text-[17px] leading-snug">
                {o?.kind === "waiting" && <p>Press green on the key to pay, red to refuse. It waits 90 seconds.</p>}
                {o?.kind === "paid" && (
                  <p className="flex items-center gap-2 text-pass">
                    <CircleCheck className="size-5" aria-hidden /> Signed on the key and paid. It&apos;s in the log.
                  </p>
                )}
                {(o?.kind === "refused" || o?.kind === "error") && (
                  <p className="flex items-start gap-2 text-fail">
                    <CircleX className="mt-0.5 size-5 shrink-0" aria-hidden /> {o.reason}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </main>
  );
}
