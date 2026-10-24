"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Evidence, failedRule, Mark, MarkRow } from "@/components/evidence";
import { StatusChip } from "@/components/status-chip";
import { Timeline } from "@/components/timeline";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, money, moneyShort, ruleNumber, type RequestDetail } from "@/lib/api";
import { category, day, dayShort, humanize, missingPaperwork, PAPERWORK_ACTION, rowLine, RULE_NAMES, shortName } from "@/lib/labels";
import { cn } from "@/lib/utils";

const ESCALATION_RULES = new Set(["14_within_spend_authority", "15_confidence_floor"]);

/** The status sentence, built from the evidence (names, numbers, dates all from the API). */
function say(d: RequestDetail): string {
  const name = shortName(d.sub);
  if (d.status === "ready") return `Every check passed and it's under your ${moneyShort(d.limit)} limit.`;
  if (d.status === "needs_ok" && d.why.length === 1 && d.why[0] === "over_limit")
    return `Every check passed, but it's over your ${moneyShort(d.limit)} limit, so the owner signs on the approval key.`;
  if (d.status === "on_hold") {
    const o = failedRule(d, "13")?.evidence.overstatements?.[0];
    const line = o && d.lines.find((l) => l.item_no === o.item_no);
    if (o && line && d.site_evidence)
      return `On hold: ${name} billed ${line.description.toLowerCase()} at ${Number(o.claimed_pct)}%, but the site video from ${dayShort(d.site_evidence.date)} shows ${Number(o.video_estimate_pct)}%.`;
    if (failedRule(d, "10") && failedRule(d, "11")?.evidence.mismatches?.length)
      return `On hold: an email that looks like it's from ${name} asks us to pay a new bank account. It came from a lookalike domain.`;
    const notices = failedRule(d, "07")?.evidence.unresolved ?? [];
    if (notices.length === 1)
      return `On hold: ${name}'s supplier, ${notices[0].supplier}, filed a notice to owner and hasn't released.`;
  }
  return humanize(d.sentence);
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
  const overLimit = failedRule(data, "14")?.evidence.limit as string | undefined;

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
      <Link href={backHref} scroll={false} className="mb-3 inline-flex min-h-11 items-center rounded-sm text-primary underline underline-offset-[3px] outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
        <span className="lg:hidden">Back to pay requests</span>
        <span className="hidden lg:inline">Close</span>
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

      <div className="mt-5 flex flex-col items-start gap-2.5">
        <StatusChip category={category(data)} />
        <p className="max-w-[44ch] text-say font-medium">{say(data)}</p>
        {data.new_sub_note && <p className="max-w-[52ch] text-body">{data.new_sub_note}</p>}
      </div>

      <div className="mt-5 flex flex-col items-start gap-2.5">
        <NextStep detail={data} />
        {data.status === "ready" && <PayNow key={data.id} detail={data} />}
        {data.status === "needs_ok" && (
          <Button size="lg" nativeButton={false} render={<Link href="/approve" />}>
            Send it to the approval key
          </Button>
        )}
        {data.status === "on_hold" && overLimit && (
          <p className="max-w-[52ch] text-base text-muted-foreground">
            Once that&apos;s settled, this still needs the owner&apos;s OK: it&apos;s over the {moneyShort(overLimit)} limit.
          </p>
        )}
      </div>

      <Evidence detail={data} />
      <Timeline id={data.id} version={version} />

      {results.length > 0 && (
        <details className="group mt-8 border-t pt-4">
          <summary className="flex min-h-12 cursor-pointer list-none items-center rounded-sm text-lg font-semibold outline-none select-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
            {results.length} checks, {passed.length} passed
            <span className="ml-3 text-meta font-normal text-primary underline underline-offset-[3px]">
              <span className="group-open:hidden">Show</span>
              <span className="hidden group-open:inline">Hide</span>
            </span>
          </summary>
          <div className="mt-2">
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
          </div>
        </details>
      )}

      {data.trace && (
        <details className="mt-4 border-t pt-4">
          <summary className="flex min-h-12 cursor-pointer list-none items-center rounded-sm text-primary underline underline-offset-[3px] outline-none select-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
            Show details
          </summary>
          <pre className="mt-2 max-h-[480px] overflow-auto rounded-[4px] bg-muted p-4 font-mono text-[13px] leading-normal whitespace-pre-wrap">
            {JSON.stringify(data.judgment ? { decision: data.trace, review: data.judgment } : data.trace, null, 2)}
          </pre>
        </details>
      )}
    </article>
  );
}

/** What to do about this hold (DESIGN.md "Buttons"): paperwork steps, the phone instruction, or an email. */
function NextStep({ detail }: { detail: RequestDetail }) {
  if (detail.status !== "on_hold") return null;
  if (category(detail) === "paperwork") return <PaperworkSteps detail={detail} />;
  const bank = failedRule(detail, "10")?.evidence;
  if (bank || failedRule(detail, "11")) {
    // the safe action is a phone call to a number we already had, so it's a sentence, not a button
    return (
      <p className="max-w-[52ch] text-body">
        {bank && !bank.on_file
          ? `Call ${detail.sub} at a number you trust, not one from their paperwork, and confirm the account ending ${detail.bank_account.slice(-4)}.`
          : `Call ${detail.sub} at the number in your own records, not one from the email, and ask whether they changed banks.`}
      </p>
    );
  }
  const action = detail.next_action?.replace(/\.$/, "");
  if (!action) return null;
  if (/^hold\b/i.test(action)) {
    // some rules' next step is "keep holding", not something to send: say it, don't make it a button
    return <p className="max-w-[52ch] text-base text-muted-foreground">{action.replace(/^hold;?\s*/i, "Keep it on hold: ")}.</p>;
  }
  return <MailButton detail={detail} action={action} />;
}

/** Opens the mail app with the message filled in, to the sub's accounting address (or blank for the super). */
function MailButton({ detail, action }: { detail: RequestDetail; action: string }) {
  const subject = `${detail.job}: ${action.toLowerCase()}`;
  const body = `Hi,\n\nWe can't include your pay request for this period yet. ${rowLine(detail.status, detail.sentence, null)}\n\n${action}.\n\nThanks`;
  const to = /super/i.test(action) || !detail.vendor.email_domain ? "" : `accounting@${detail.vendor.email_domain}`;
  return (
    <Button size="lg" nativeButton={false} render={<a href={`mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`} />}>
      {action}
    </Button>
  );
}

/** A hold that's only waiting on paperwork: each missing piece as a sentence with its own next action. */
function PaperworkSteps({ detail }: { detail: RequestDetail }) {
  const [crs, setCrs] = useState<{ busy?: boolean; said?: string; failed?: boolean }>({});
  const verify = async () => {
    setCrs({ busy: true });
    try {
      const r = await api.verifyVendor(detail.sub_id);
      await api.replay(6).catch(() => {});
      setCrs(r.crs_status === "verified"
        ? { said: "Identity checked: CRS verified them." }
        : { said: `CRS came back "${r.crs_status}", so it stays on hold.${r.reasons.length ? ` ${r.reasons.join(" ")}` : ""}`, failed: true });
    } catch (e) {
      setCrs({ said: (e as ApiError).message, failed: true });
    }
  };
  return (
    <ul className="w-full border-t">
      {missingPaperwork(detail).map((n) => {
        const step = PAPERWORK_ACTION[n];
        return (
          <li key={n} className="flex flex-col items-start gap-2.5 border-b py-3.5">
            <p className="flex items-start gap-2 text-body">
              <Mark kind="fail" />
              <span>{step.say}</span>
            </p>
            {step.does === "verify" ? (
              <>
                <Button size="lg" onClick={verify} disabled={crs.busy}>
                  {crs.busy ? "Checking…" : step.act}
                </Button>
                {crs.said && <p className={cn("max-w-[52ch] text-base", crs.failed ? "text-fail" : "text-pass")}>{crs.said}</p>}
              </>
            ) : step.does === "mail" && step.act ? (
              <MailButton detail={detail} action={step.act} />
            ) : step.kind && step.act ? (
              <Button size="lg" nativeButton={false} render={<Link href={`/upload?kind=${step.kind}&sub=${encodeURIComponent(detail.sub_id)}`} />}>
                {step.act}
              </Button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

type PayStep = "idle" | "confirm" | "paying" | "paid" | "unknown" | "checking" | "error";

/** Every check passed: pay it from the operating account, through the bank. */
function PayNow({ detail }: { detail: RequestDetail }) {
  const [step, setStep] = useState<PayStep>("idle");
  const [said, setSaid] = useState<string | null>(null);

  const pay = async () => {
    setStep("paying");
    try {
      await api.pay(detail.id);
      setSaid(null);
      setStep("paid");
    } catch (e) {
      const message = (e as ApiError).message;
      setSaid(message);
      setStep(/unknown outcome/i.test(message) ? "unknown" : "error");
    }
  };
  const ask = async () => {
    setStep("checking");
    try {
      const r = await api.resolve(detail.id);
      setSaid(r.outcome === "paid" ? "The bank shows it was paid. It's recorded now." : "The bank has no payment for this, so it can be paid again.");
      setStep(r.outcome === "paid" ? "paid" : "idle");
    } catch (e) {
      setSaid((e as ApiError).message);
      setStep("unknown");
    }
  };

  if (step === "paid")
    return (
      <p className="flex items-start gap-2 text-body text-pass">
        <Mark kind="pass" />
        <span>{said ?? `Paid ${money(detail.amount)} to ${detail.sub}. It's in the log.`}</span>
      </p>
    );
  if (step === "unknown" || step === "checking")
    return (
      <div className="flex flex-col items-start gap-2.5">
        <p className="max-w-[52ch] text-base text-fail">
          We sent the payment but the bank didn&apos;t confirm it. Check with the bank before paying again.
        </p>
        {said && <p className="max-w-[52ch] text-base text-muted-foreground">{said}</p>}
        <Button size="lg" onClick={ask} disabled={step === "checking"}>
          {step === "checking" ? "Checking…" : "Check with the bank"}
        </Button>
      </div>
    );
  if (step === "confirm")
    return (
      <div className="flex flex-col items-start gap-2.5">
        <p className="max-w-[52ch] text-body">
          Pay {money(detail.amount)} to {detail.sub}, to the account ending {detail.bank_account.slice(-4)}?
        </p>
        <div className="flex flex-wrap gap-3">
          <Button size="lg" onClick={pay}>
            Yes, pay it
          </Button>
          <Button size="lg" variant="outline" onClick={() => setStep("idle")}>
            Cancel
          </Button>
        </div>
      </div>
    );
  return (
    <div className="flex flex-col items-start gap-2.5">
      <Button size="lg" onClick={() => setStep("confirm")} disabled={step === "paying"}>
        {step === "paying" ? "Paying…" : `Pay ${money(detail.amount)} now`}
      </Button>
      {step === "error" && said && (
        <p className="flex max-w-[52ch] items-start gap-2 text-base text-fail">
          <Mark kind="fail" />
          <span>{said}</span>
        </p>
      )}
    </div>
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
