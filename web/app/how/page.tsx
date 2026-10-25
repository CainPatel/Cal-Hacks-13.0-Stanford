import type { Metadata } from "next";
import type { ReactNode } from "react";
import { StatusMark } from "@/components/status-chip";
import { CATEGORY, RULE_NAMES, type Category } from "@/lib/labels";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "How Draw works",
  description: "One pay application, from the inbox to the bank: who reads it, who decides, who signs.",
};

type Station = { n: number; name: string; who: string; text: string };

// What happens to one pay application, in order. The first four may use AI; from the checks on, none does.
const READ: Station[] = [
  {
    n: 1,
    name: "The pay application arrives",
    who: "By email, or the Upload Portal",
    text: "A sub emails its G702 and G703 to Draw's inbox. They're filed under the sub only when the sender's address is exactly the domain on file, so a lookalike domain matches nobody.",
  },
  {
    n: 2,
    name: "Draw reads it",
    who: "Code first; Claude for scans and site video",
    text: "Every line, total, date and the remit-to account is read off the document and checked against its own arithmetic. Nothing is guessed: a page that can't be read with certainty goes to a person.",
  },
  {
    n: 3,
    name: "It's written to the log",
    who: "The append-only log",
    text: "Each fact is a new row that's never edited or deleted. Every later step reads from here, so any decision can be traced to what Draw knew at the time.",
  },
  {
    n: 4,
    name: "Jev reviews it",
    who: "Jev, TypeSafe's judgment model; Claude if Jev is down",
    text: "Five questions: is the lien release the right form, does a bank change look like fraud, is the progress believable, is it a repeat, and what should happen. Jev only answers. How sure it is counts as one of the checks.",
  },
];

const DECIDE: Station = {
  n: 5,
  name: "15 checks decide",
  who: "Fixed rules, the same every time",
  text: "The checks an office manager does by hand: the math against last month, retainage, lien releases, insurance, supplier notices, the owner's draw, vendor identity, bank details, the sender's email, duplicates and progress against the site video.",
};

const OUTCOMES: { category: Category; title: string; text: string; next: string }[] = [
  {
    category: "ready",
    title: "Ready to pay",
    text: "Every check passed and it's under your auto-release limit.",
    next: "Pay it from the dashboard. No tap needed.",
  },
  {
    category: "ok",
    title: "Needs your OK",
    text: "Nothing failed, but it's over your limit, a first payment, a new bank account, or the review wasn't sure.",
    next: "It goes to the approval key on your desk.",
  },
  {
    category: "fraud",
    title: "On hold",
    text: "Something is missing or wrong: a lien release, insurance, the math, a lookalike email, a changed bank account.",
    next: "Draw shows the proof and what to do next. Nothing is paid.",
  },
];

const PAY: Station[] = [
  {
    n: 6,
    name: "You approve on the key",
    who: "Only for \u201cNeeds your OK\u201d: the approval key, a Raspberry Pi on your desk",
    text: "It shows the payee and amount. Green signs that exact payment, once, before it expires; red refuses it. The signing key never leaves the device, so no software can approve for you.",
  },
  {
    n: 7,
    name: "The bank pays",
    who: "Capital One's Nessie sandbox",
    text: "The one part of Draw that can move money checks that every rule passed and the signature matches, asks the bank whether it was already paid, and only then pays.",
  },
  {
    n: 8,
    name: "It's all in the log",
    who: "The Log tab",
    text: "Received, read, reviewed, checked, signed and paid, each with its time, newest first.",
  },
];

const MONEY: [string, string, string][] = [
  ["Reading the documents", "Code, with Claude for scans and video", "No"],
  ["Reviewing", "Jev", "No"],
  ["Deciding", "15 fixed rules", "No. It can only say ready, needs your OK, or on hold"],
  ["Approving big or unusual payments", "You, on the approval key", "Signs one exact payment"],
  ["Paying", "Draw's executor, through the bank", "Only when every check passed, and with your signature when it's needed"],
];

const TH = "align-bottom border-b-2 border-foreground px-2 py-2 text-left text-meta font-medium text-muted-foreground sm:px-3";
const TD = "border-b px-2 py-[11px] align-top sm:px-3";

export default function HowPage() {
  return (
    <main className="mx-auto w-full max-w-[1164px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <h1 className="text-[27px] leading-tight font-semibold tracking-[-0.02em] sm:text-lede">How Draw decides</h1>
      <p className="mt-4 max-w-[62ch] text-say leading-[1.55]">
        One pay application, from the inbox to the bank. AI reads and reviews it; fixed rules decide; your key signs
        anything big. The AI never touches the money.
      </p>

      <Section id="path" title="The path of one pay application">
        <ol className="rounded-lg border bg-sheet px-4 py-2 sm:px-8 sm:py-4" aria-label="Steps">
          {READ.map((s) => (
            <Step key={s.n} station={s} />
          ))}
          <Divider>From here on, no AI: rules, your key and the bank.</Divider>
          <Step station={DECIDE} />
          <li className="pb-2 pl-14 sm:pl-16">
            <p className="text-meta text-muted-foreground">Each pay application comes out one of three ways:</p>
            <ul className="mt-3 grid gap-3 md:grid-cols-3" aria-label="Outcomes">
              {OUTCOMES.map((o) => (
                <li key={o.title} className={cn("rounded-lg border border-t-4 p-4", CATEGORY[o.category].tone)}>
                  <p className={cn("flex items-center gap-1.5 font-semibold", toneText(o.category))}>
                    <StatusMark category={o.category} />
                    {o.title}
                  </p>
                  <p className="mt-2 text-body">{o.text}</p>
                  <p className="mt-2 text-body text-muted-foreground">{o.next}</p>
                </li>
              ))}
            </ul>
          </li>
          {PAY.map((s, i) => (
            <Step key={s.n} station={s} last={i === PAY.length - 1} />
          ))}
        </ol>
      </Section>

      <Section id="money" title="Who can move money" intro="Only the last step, and only after everything before it.">
        <div>
          <table className="w-full border-collapse text-body">
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
        </div>
      </Section>

      <Section
        id="checks"
        title="The 15 checks"
        intro="A failed check puts the request on hold with its proof, except the last two: being over your limit, or a review that wasn't sure, sends it to you instead."
      >
        <ol className="grid list-decimal gap-x-10 gap-y-2 pl-6 text-body sm:grid-cols-2 lg:grid-cols-3">
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

/** One station on the path: a numbered node on the spine, the step, who does it, and what it means. */
function Step({ station, last = false }: { station: Station; last?: boolean }) {
  return (
    <li className="relative flex gap-4 py-4 sm:gap-6">
      {!last && <span aria-hidden className="absolute top-14 bottom-0 left-[19px] w-px bg-border-strong" />}
      <span
        aria-hidden
        className="relative flex size-10 shrink-0 items-center justify-center rounded-full border-2 border-foreground bg-sheet font-mono text-body font-semibold tnum"
      >
        {station.n}
      </span>
      <div className="min-w-0 max-w-[64ch] pt-1.5">
        <h3 className="text-body font-semibold">
          <span className="sr-only">Step {station.n}: </span>
          {station.name}
        </h3>
        <p className="text-meta text-muted-foreground">{station.who}</p>
        <p className="mt-2 text-body">{station.text}</p>
      </div>
    </li>
  );
}

/** The line between what AI may help with and what it never does. */
function Divider({ children }: { children: ReactNode }) {
  return (
    <li className="my-3 border-t-[3px] border-double border-foreground pt-3 pl-14 text-body font-semibold sm:pl-16">
      {children}
    </li>
  );
}

function toneText(c: Category) {
  return c === "ready" ? "text-pass" : c === "ok" ? "text-hold" : "text-fail";
}
