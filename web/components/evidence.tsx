import type { ReactNode } from "react";
import { type Line, type RequestDetail, type RuleResult } from "@/lib/api";
import { day, sayChar, shortName } from "@/lib/labels";
import { cn } from "@/lib/utils";

// Evidence shapes the kernel rules emit (kernel/rules/rule_07, _10, _11, _13).
type Overstatement = { item_no: string; claimed_pct: string; printed_pct?: string; video_estimate_pct: string; gap: string; tolerance: string };
type DomainMismatch = {
  email_id: string;
  claimed_domain: string;
  vendor_domain: string;
  differences?: { position: number; claimed: string | null; vendor: string | null }[];
};
type Notice = { supplier: string; notice_date: string };
type Shapes = {
  overstatements?: Overstatement[];
  mismatches?: DomainMismatch[];
  unresolved?: Notice[];
  on_file?: string;
  requested?: string;
  verification_status?: string;
};

/** The failed result for a rule number ("13"), with its evidence, or undefined. */
export const failedRule = (d: RequestDetail, n: string) =>
  d.trace?.results.find((r) => r.rule.startsWith(n) && !r.passed) as (RuleResult & { evidence: Shapes }) | undefined;

const MARKS = {
  pass: "M4 13.5l5 5.5L20.5 5",
  fail: "M6 6l12 12M18 6L6 18",
  ok: "M12 3.5c5 0 8.5 3.6 8.5 8.4s-3.8 8.6-8.6 8.6S3.4 16.6 3.5 12 7 3.6 12.6 3.8",
};
const MARK_COLOR = { pass: "stroke-pass", fail: "stroke-fail", ok: "stroke-hold" };

/** One upright margin mark: tick, cross, or the owner's ring. */
export function Mark({ kind }: { kind: keyof typeof MARKS }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={cn("size-6 fill-none", MARK_COLOR[kind])} strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round">
      <path d={MARKS[kind]} />
    </svg>
  );
}

/** A checklist row with a ruled margin column (DESIGN.md "Checklist with margin marks"). */
export function MarkRow({ kind, sr, children }: { kind: keyof typeof MARKS; sr: string; children: ReactNode }) {
  return (
    <li className="grid grid-cols-[44px_1fr] border-b">
      <span className="flex justify-center border-r border-border-strong pt-3">
        <Mark kind={kind} />
        <span className="sr-only">{sr}</span>
      </span>
      <span className="py-[11px] pl-3.5 text-[17px]">{children}</span>
    </li>
  );
}

/**
 * The proof beside each hold: what the rule saw on the left, the document it came from on the right.
 * Every failing evidence rule gets its own block, in the order progress, bank/email, supplier.
 */
export function Evidence({ detail }: { detail: RequestDetail }) {
  const progress = failedRule(detail, "13")?.evidence.overstatements ?? [];
  const bank = failedRule(detail, "10")?.evidence;
  const domains = failedRule(detail, "11")?.evidence.mismatches ?? [];
  const notices = failedRule(detail, "07")?.evidence.unresolved ?? [];

  const blocks: ReactNode[] = [
    ...progress.map((o, i) => <ProgressBlock key={`p${o.item_no}`} o={o} detail={detail} withFrames={i === 0} />),
    ...(bank || domains.length ? [<BankBlock key="bank" bank={bank} domains={domains} sub={detail.sub} />] : []),
    ...notices.map((n, i) => <SupplierBlock key={`n${i}`} n={n} detail={detail} />),
  ];
  if (!blocks.length) return null;
  return (
    <section aria-label="The proof" className="mt-8 divide-y border-t pt-6">
      {blocks.map((b, i) => (
        <div key={i} className="py-6 first:pt-0 last:pb-0">
          {b}
        </div>
      ))}
    </section>
  );
}

function Columns({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-[repeat(auto-fit,minmax(270px,1fr))] gap-7 [&>*]:min-w-0">{children}</div>;
}

function H4({ children }: { children: ReactNode }) {
  return <h4 className="mb-3 text-base font-semibold">{children}</h4>;
}

function Note({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("mt-1.5 text-base", className)}>{children}</p>;
}

/** Paper: a white frame with a strong rule and the file name in a tinted header. */
function Doc({ name, children }: { name?: string; children: ReactNode }) {
  return (
    <div className="rounded-[4px] border border-border-strong bg-sheet text-[15px]">
      {name && <div className="border-b border-border-strong bg-muted px-3 py-2 font-mono text-[13px] [overflow-wrap:anywhere] text-muted-foreground">{name}</div>}
      {children}
    </div>
  );
}

const pct = (n: number) => `${Number.isInteger(n) ? n : n.toFixed(1)}%`;
const clamp = (n: number) => Math.max(0, Math.min(100, n));

function ProgressBlock({ o, detail, withFrames }: { o: Overstatement; detail: RequestDetail; withFrames: boolean }) {
  const claimed = Number(o.claimed_pct);
  const video = Number(o.video_estimate_pct);
  const tol = Number(o.tolerance);
  const line = detail.lines.find((l) => l.item_no === o.item_no);
  const what = line ? line.description.toLowerCase() : `line ${o.item_no}`;
  const site = detail.site_evidence;
  const lo = clamp(video - tol), hi = clamp(video + tol);
  const g0 = clamp(Math.min(video, claimed)), g1 = clamp(Math.max(video, claimed));
  return (
    <Columns>
      <div>
        <H4>
          Line {o.item_no}, {what}, against the site video
        </H4>
        <div className="relative mx-3.5 h-[132px]" role="img"
          aria-label={`Billed ${pct(claimed)}. Site video ${pct(video)}. Allowed within ${tol} points of the video.`}>
          <div className="absolute top-10 h-[26px] border border-border-strong bg-[repeating-linear-gradient(135deg,var(--muted)_0_4px,var(--border)_4px_5px)]"
            style={{ left: `${lo}%`, width: `${hi - lo}%` }} />
          <div className="absolute inset-x-0 top-[52px] h-0.5 bg-foreground" />
          <div className="absolute top-[50px] h-1.5 bg-hold" style={{ left: `${g0}%`, width: `${g1 - g0}%` }} />
          <div className="absolute top-10 h-10 w-0.5 -translate-x-px bg-foreground" style={{ left: `${clamp(video)}%` }}>
            <span className="absolute top-[42px] left-1/2 -translate-x-1/2 text-[15px] font-semibold whitespace-nowrap">Video {pct(video)}</span>
          </div>
          <div className="absolute top-[22px] h-11 w-0.5 -translate-x-px bg-hold" style={{ left: `${clamp(claimed)}%` }}>
            <span className="absolute -top-6 left-1/2 -translate-x-1/2 text-[15px] font-semibold whitespace-nowrap text-hold">Billed {pct(claimed)}</span>
          </div>
          {[0, 25, 50, 75, 100].map((t) => (
            <span key={t} className="absolute top-[110px] -translate-x-1/2 font-mono text-[13px] text-muted-foreground tnum" style={{ left: `${t}%` }}>
              {t}
            </span>
          ))}
        </div>
        <Note>
          {Number(o.gap)} points apart. Up to {tol} is allowed (the shaded band).
        </Note>
        {withFrames && site && site.frames.length > 0 && (
          <>
            <div className="mt-4 grid grid-cols-3 gap-2" aria-hidden>
              {site.frames.map((f, i) => (
                <div key={f} className="grid aspect-[16/10] place-items-center border border-dashed border-border-strong px-1 text-center text-[13px] leading-tight text-muted-foreground">
                  Frame {i + 1}, not loaded
                </div>
              ))}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Site video, {day(site.date)}, {site.frames.length} frames. Site video frames aren&apos;t loaded in this demo.
            </p>
          </>
        )}
      </div>
      {line && (
        <div>
          <H4>Their G703, line {line.item_no}</H4>
          <G703 line={line} printed={o.printed_pct ?? o.claimed_pct} source={detail.source_doc} />
        </div>
      )}
    </Columns>
  );
}

const cents = (v: string) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function G703({ line, printed, source }: { line: Line; printed: string; source: string }) {
  const rows: [string, string, ReactNode, boolean?][] = [
    ["A", "Item", <span key="a" className="font-mono">{line.item_no}</span>],
    ["B", "Description", line.description],
    ["C", "Scheduled value", cents(line.scheduled_value)],
    ["D", "From previous applications", cents(line.previous_applications)],
    ["E", "This period", cents(line.this_period)],
    ["F", "Materials stored", cents(line.stored_materials)],
    ["G", "Completed and stored to date", cents(line.total_to_date)],
    ["G÷C", "Percent complete", `${Number(printed)}%`, true],
    ["H", "Balance to finish", cents(line.balance_to_finish)],
    ["I", "Retainage", cents(line.retainage)],
  ];
  return (
    <Doc name={source}>
      <table className="w-full border-collapse">
        <tbody className="divide-y">
          {rows.map(([col, label, value, flag]) => (
            <tr key={col} className={cn(flag && "bg-hold-soft")}>
              <td className="w-[2.6em] border-r px-1 pt-2 pb-1.5 text-center align-top font-mono text-[13px] text-muted-foreground">{col}</td>
              <td className="px-2.5 py-1.5 align-top">
                {label}
                {flag && <span className="sr-only"> (this is the claim the video disputes)</span>}
              </td>
              <td className={cn("px-2.5 py-1.5 text-right align-top whitespace-nowrap", typeof value === "string" && col !== "B" && "font-mono tnum", flag && "font-semibold text-hold")}>
                {value}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Doc>
  );
}

const lastFour = (acct?: string) => (acct ? `ending ${acct.replace(/\D/g, "").slice(-4)}` : "none");

/** "a capital I", with the letter itself in mono: in Geist Sans a capital I and a lowercase l look the same. */
function Said({ c }: { c: string | null }) {
  if (!c) return <>nothing</>;
  const s = sayChar(c);
  if (!s.endsWith(c)) return <>{s}</>;
  return (
    <>
      {s.slice(0, -c.length)}
      <span className="font-mono text-[1.15em]">{c}</span>
    </>
  );
}

function Letters({ text, bad }: { text: string; bad: Set<number> }) {
  return (
    <div className="flex">
      {[...text].map((c, i) => (
        <span key={i} className={cn("w-[1.2ch] border-b py-1.5 text-center",
          bad.has(i) && "rounded-[2px] bg-fail-soft text-fail outline-2 -outline-offset-2 outline-fail")}>
          {c}
        </span>
      ))}
    </div>
  );
}

function BankBlock({ bank, domains, sub }: { bank?: Shapes; domains: DomainMismatch[]; sub: string }) {
  const name = shortName(sub);
  return (
    <Columns>
      <div className="@container">
        <H4>{domains.length ? "Where the email came from" : "Where the money would go"}</H4>
        {domains.map((m) => {
          const diffs = m.differences ?? [];
          const bad = new Set(diffs.map((d) => d.position));
          return (
            <div key={m.email_id} className="mb-4">
              <div className="font-mono text-[clamp(14px,5.4cqw,26px)] leading-none">
                <p className="mb-1.5 font-sans text-[15px] text-muted-foreground">The email</p>
                <Letters text={m.claimed_domain} bad={bad} />
                <p className="mt-3.5 mb-1.5 font-sans text-[15px] text-muted-foreground">{name}&apos;s real domain</p>
                <Letters text={m.vendor_domain} bad={new Set()} />
              </div>
              {diffs.length > 0 && (
                <Note>
                  {diffs.map((d, i) => (
                    <span key={i}>
                      {i > 0 && " "}
                      {d.claimed ? (
                        <>
                          Letter {d.position + 1} is <Said c={d.claimed} />, not <Said c={d.vendor} />.
                        </>
                      ) : (
                        <>A letter is missing at position {d.position + 1}.</>
                      )}
                    </span>
                  ))}{" "}
                  On most screens they look the same. Don&apos;t act on this email.
                </Note>
              )}
            </div>
          );
        })}
        {bank && (
          <div className={cn(domains.length > 0 && "mt-[18px] border-t pt-3.5")}>
            <p className="text-[15px] text-muted-foreground">Bank account</p>
            <p className="font-mono text-[19px] tnum">
              {lastFour(bank.on_file)} → {lastFour(bank.requested)}
            </p>
            {bank.verification_status === "unverified" && <Note>The new account hasn&apos;t been verified.</Note>}
          </div>
        )}
      </div>
    </Columns>
  );
}

function SupplierBlock({ n, detail }: { n: Notice; detail: RequestDetail }) {
  return (
    <Columns>
      <div>
        <H4>What&apos;s missing</H4>
        <ul className="border-t">
          <MarkRow kind="pass" sr="Done:">Notice to owner filed {day(n.notice_date)}</MarkRow>
          <MarkRow kind="fail" sr="Missing:">{n.supplier}&apos;s release: not received</MarkRow>
        </ul>
        <Note>
          Until it comes in, {n.supplier} can claim against the property for what {shortName(detail.sub)} owes them.
        </Note>
      </div>
      <div>
        <H4>Notice to owner</H4>
        <Doc>
          <h5 className="px-3 pt-3 pb-1 text-[17px] font-semibold">{n.supplier}</h5>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 px-3 pt-1 pb-2.5 [&>*]:border-t [&>*]:py-1.5 [&>:nth-child(-n+2)]:border-t-0">
            <dt className="text-muted-foreground">Supplying</dt>
            <dd>{detail.sub}</dd>
            <dt className="text-muted-foreground">Job</dt>
            <dd>{detail.job}</dd>
            <dt className="text-muted-foreground">Filed</dt>
            <dd>{day(n.notice_date)}</dd>
            <dt className="text-muted-foreground">Released</dt>
            <dd>No</dd>
          </dl>
        </Doc>
      </div>
    </Columns>
  );
}
