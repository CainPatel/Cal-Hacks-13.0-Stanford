import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Pipeline } from "@/components/pipeline";
import { RULE_NAMES } from "@/lib/labels";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "How Draw works",
  description: "One pay application, from the inbox to the bank: every call it makes, and who can move money.",
};

const MONEY: [string, string, string][] = [
  ["Reading the documents", "Code, with Claude for scans and video", "No"],
  ["Reviewing", "Jev", "No"],
  ["Deciding", "15 fixed rules", "No. It can only say ready, needs your OK, or on hold"],
  ["Approving big or unusual payments", "You, on the approval key", "Signs one exact payment"],
  ["Paying", "Draw’s executor, through the bank", "Only when every check passed, and with your signature when it’s needed"],
];

const TH = "align-bottom border-b-2 border-foreground px-2 py-2 text-left text-meta font-medium text-muted-foreground sm:px-3";
const TD = "border-b px-2 py-[11px] align-top sm:px-3";

export default function HowPage() {
  return (
    <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <h1 className="text-[27px] leading-tight font-semibold tracking-[-0.02em] sm:text-lede">How Draw decides</h1>
      <p className="mt-4 max-w-[62ch] text-say leading-[1.55]">
        One pay application, from the inbox to the bank, with every call it makes. AI reads and reviews it; fixed
        rules decide; your key signs anything big. The AI never touches the money.
      </p>

      <Section id="path" title="The path of one pay application">
        <Pipeline />
      </Section>

      <Section id="money" title="Who can move money" intro="Only the last step, and only after everything before it.">
        <table className="w-full max-w-[1164px] border-collapse text-body">
          <thead>
            <tr>
              <th className={TH}>Step</th>
              <th className={TH}>Done by</th>
              <th className={TH}>Can it move money?</th>
            </tr>
          </thead>
          <tbody>
            {MONEY.map(([step, who, money]) => (
              <tr key={step}>
                <td className={cn(TD, "font-semibold")}>{step}</td>
                <td className={TD}>{who}</td>
                <td className={TD}>{money}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section
        id="checks"
        title="The 15 checks"
        intro="A failed check puts the request on hold with its proof, except the last two: being over your limit, or a review that wasn't sure, sends it to you instead."
      >
        <ol className="grid max-w-[1164px] list-decimal gap-x-10 gap-y-2 pl-6 text-body sm:grid-cols-2 lg:grid-cols-3">
          {/* by number: JS lists keys "10".."15" before "01".."09", so the object's own order won't do */}
          {Object.keys(RULE_NAMES).sort().map((k) => (
            <li key={k} className="pl-1">
              {RULE_NAMES[k]}
            </li>
          ))}
        </ol>
      </Section>
    </main>
  );
}

function Section({ id, title, intro, children }: { id: string; title: string; intro?: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-14">
      <h2 id={id} className="text-[22px] font-semibold tracking-[-0.01em]">
        {title}
      </h2>
      {intro && <p className="mt-2 max-w-[64ch] text-body text-muted-foreground">{intro}</p>}
      <div className="mt-5">{children}</div>
    </section>
  );
}
