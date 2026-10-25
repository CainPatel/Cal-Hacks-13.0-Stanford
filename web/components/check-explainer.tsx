"use client";

import { Popover } from "@base-ui/react/popover";
import { Info } from "lucide-react";

/**
 * What "Check {month}" does, in plain words, next to the button. Opens on hover, tap or keyboard
 * focus, so it works on a phone as well as a desk. The wording keeps the product rule: the checks
 * decide, the AI only reviews.
 */
export function CheckExplainer({ month }: { month: string }) {
  return (
    <Popover.Root>
      <Popover.Trigger
        openOnHover
        delay={150}
        aria-label={`What "Check ${month}" does`}
        className="inline-flex size-12 shrink-0 items-center justify-center rounded-full text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <Info className="size-6" aria-hidden />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={8} className="z-50">
          <Popover.Popup className="w-[min(24rem,calc(100vw-2rem))] rounded-lg border border-border-strong bg-sheet p-5 text-body text-foreground outline-none">
            <Popover.Title className="font-semibold">What &ldquo;Check {month}&rdquo; does</Popover.Title>
            <ol className="mt-3 list-decimal space-y-2 pl-5">
              <li>Gets the latest from the bank: which owner&rsquo;s draws have cleared and what&rsquo;s already been paid.</li>
              <li>
                Jev, the AI reviewer, reads each new pay request and says how sure it is about the lien release, the
                bank details, the progress claimed, and whether it&rsquo;s a repeat.
              </li>
              <li>
                Draw&rsquo;s 15 checks decide: ready to pay, needs your OK, or on hold. If Jev wasn&rsquo;t sure, it
                comes to you.
              </li>
            </ol>
            <p className="mt-3 text-meta text-muted-foreground">Pay requests that were already checked stay as they are.</p>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
