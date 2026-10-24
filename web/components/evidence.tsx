import type { Line, RuleResult } from "@/lib/api";
import { sayChar } from "@/lib/labels";

// Evidence shapes the kernel rules emit (kernel/rules/rule_07, _10, _11, _13).
type Overstatement = { item_no: string; claimed_pct: string; video_estimate_pct: string; gap: string; tolerance: string };
type DomainMismatch = {
  email_id: string;
  claimed_domain: string;
  vendor_domain: string;
  differences?: { position: number; claimed: string | null; vendor: string | null }[];
};
type Notice = { supplier: string; notice_date: string };
type Shapes = { overstatements?: Overstatement[]; mismatches?: DomainMismatch[]; unresolved?: Notice[]; on_file?: string; requested?: string };

/** The proof beside a hold: what the rule saw, drawn so a person can check it at a glance. */
export function Evidence({ result, lines }: { result: RuleResult; lines: Line[] }) {
  const e = result.evidence as Shapes;
  switch (result.rule.slice(0, 2)) {
    case "13":
      return <ProgressEvidence items={e.overstatements ?? []} lines={lines} />;
    case "11":
      return <DomainEvidence mismatches={e.mismatches ?? []} />;
    case "10":
      return <BankEvidence onFile={e.on_file} requested={e.requested} />;
    case "07":
      return <SupplierEvidence unresolved={e.unresolved ?? []} />;
    default:
      return null;
  }
}

function ProgressEvidence({ items, lines }: { items: Overstatement[]; lines: Line[] }) {
  return (
    <div className="space-y-5">
      {items.map((o) => {
        const claimed = Number(o.claimed_pct);
        const seen = Number(o.video_estimate_pct);
        const tol = Number(o.tolerance);
        const line = lines.find((l) => l.item_no === o.item_no);
        return (
          <figure key={o.item_no} className="space-y-2">
            <figcaption className="text-[15px]">
              <span className="font-medium">{line?.description ?? o.item_no}</span>{" "}
              <span className="text-muted-foreground">(line {o.item_no})</span>
            </figcaption>
            <div className="relative h-7 rounded-md bg-muted" role="img"
              aria-label={`Claimed ${claimed}%, site video shows about ${seen}%, allowed up to ${seen + tol}%`}>
              <div className="absolute inset-y-0 left-0 rounded-l-md bg-pending/35" style={{ width: `${seen}%` }} />
              <div className="absolute inset-y-0 bg-[repeating-linear-gradient(135deg,transparent_0_5px,color-mix(in_oklch,var(--pending)_30%,transparent)_5px_7px)]"
                style={{ left: `${seen}%`, width: `${Math.min(tol, 100 - seen)}%` }} />
              <div className="absolute -inset-y-1 w-[3px] rounded-full bg-fail" style={{ left: `calc(${Math.min(claimed, 100)}% - 1.5px)` }} />
            </div>
            <p className="text-[15px] leading-snug">
              Claimed <b className="font-mono tnum">{claimed.toFixed(0)}%</b>; the site video shows about{" "}
              <b className="font-mono tnum">{seen.toFixed(0)}%</b>.{" "}
              <span className="text-fail">{Number(o.gap).toFixed(0)} points over</span>; the contract allows {tol.toFixed(0)}.
            </p>
          </figure>
        );
      })}
    </div>
  );
}

function DomainEvidence({ mismatches }: { mismatches: DomainMismatch[] }) {
  return (
    <div className="space-y-4">
      {mismatches.map((m) => {
        const diffs = m.differences ?? [];
        const at = new Set(diffs.map((d) => d.position));
        return (
          <div key={m.email_id} className="space-y-2">
            <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-4 gap-y-1.5 font-mono text-[17px]">
              <dt className="font-sans text-sm text-muted-foreground">Real</dt>
              <dd className="break-all">{m.vendor_domain}</dd>
              <dt className="font-sans text-sm text-muted-foreground">Email</dt>
              <dd className="break-all">
                {[...m.claimed_domain].map((c: string, i: number) =>
                  at.has(i) ? (
                    <mark key={i} className="rounded-sm bg-fail-soft px-0.5 text-fail underline decoration-2 underline-offset-4">{c}</mark>
                  ) : (
                    <span key={i}>{c}</span>
                  ),
                )}
              </dd>
            </dl>
            {diffs.length > 0 && (
              <p className="text-[15px] leading-snug">
                {diffs.map((d, i) => (
                  <span key={i}>
                    {i > 0 && "; "}
                    {d.claimed ? `${sayChar(d.claimed)} where the real domain has ${d.vendor ? sayChar(d.vendor) : "nothing"}` : `a letter missing`}
                  </span>
                ))}
                . Don&apos;t act on this email.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

const lastFour = (acct?: string) => (acct ? `ending ${acct.replace(/\D/g, "").slice(-4)}` : "none");

function BankEvidence({ onFile, requested }: { onFile?: string; requested?: string }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-4 gap-y-1.5 text-[15px]">
      <dt className="text-muted-foreground">Paid last time</dt>
      <dd className="font-mono tnum">{lastFour(onFile)}</dd>
      <dt className="text-muted-foreground">This request</dt>
      <dd className="font-mono tnum text-fail">{lastFour(requested)}</dd>
    </dl>
  );
}

function SupplierEvidence({ unresolved }: { unresolved: Notice[] }) {
  return (
    <ul className="space-y-1.5 text-[15px]">
      {unresolved.map((n, i) => (
        <li key={i}>
          <span className="font-medium">{n.supplier}</span> filed a notice to owner on{" "}
          {new Date(`${n.notice_date}T12:00:00`).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })} and
          hasn&apos;t released.
        </li>
      ))}
    </ul>
  );
}
