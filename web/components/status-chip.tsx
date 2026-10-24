import { Check, CircleDashed, CircleDot } from "lucide-react";
import type { ComponentProps } from "react";
import type { Status } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";

// lucide's Pause draws two outlined boxes; the design's hold mark is two plain bars.
function Pause(props: ComponentProps<"svg">) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" {...props}>
      <path d="M5.5 3.5v9M10.5 3.5v9" />
    </svg>
  );
}

// Amber means held or waiting on the owner; red is kept for a failed check (web/DESIGN.md).
const LOOK: Record<Status, { icon: typeof Check | typeof Pause; className: string }> = {
  ready: { icon: Check, className: "bg-pass-soft text-pass" },
  needs_ok: { icon: CircleDot, className: "bg-hold-soft text-hold" },
  on_hold: { icon: Pause, className: "bg-hold-soft text-hold" },
  not_checked: { icon: CircleDashed, className: "bg-pending-soft text-pending" },
};

/** Status is said in words and an icon; color only repeats it. */
export function StatusChip({ status, className }: { status: Status; className?: string }) {
  const { icon: Icon, className: look } = LOOK[status];
  return (
    <span className={cn("inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[0.9375rem] font-semibold whitespace-nowrap", look, className)}>
      <Icon className="size-[15px]" strokeWidth={2.4} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}
