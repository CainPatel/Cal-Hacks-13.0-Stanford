import { Check, CircleCheck, CircleDashed, CircleDot, X } from "lucide-react";
import type { ComponentProps } from "react";
import { CATEGORY, type Category } from "@/lib/labels";
import { cn } from "@/lib/utils";

// lucide's Pause draws two outlined boxes; the design's waiting mark is two plain bars.
function Pause(props: ComponentProps<"svg">) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" {...props}>
      <path d="M5.5 3.5v9M10.5 3.5v9" />
    </svg>
  );
}

const MARK: Record<Category, typeof Check | typeof Pause> = {
  fraud: X,
  paperwork: Pause,
  ok: CircleDot,
  unchecked: CircleDashed,
  ready: Check,
  paid: CircleCheck,
};

/** The category's upright mark; its word always sits beside it (or in sr-only text). */
export function StatusMark({ category, className }: { category: Category; className?: string }) {
  const Icon = MARK[category];
  return <Icon className={cn("size-[15px] shrink-0", className)} strokeWidth={2.4} aria-hidden />;
}

/** Status is said in words and a mark; colour only repeats it. */
export function StatusChip({ category, className }: { category: Category; className?: string }) {
  return (
    <span className={cn("inline-flex min-h-7 items-center gap-1.5 rounded-lg px-2.5 text-[0.9375rem] font-semibold", CATEGORY[category].chip, className)}>
      <StatusMark category={category} />
      {CATEGORY[category].label}
    </span>
  );
}
