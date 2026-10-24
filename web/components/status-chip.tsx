import { CircleCheck, CircleDashed, Hand, OctagonPause } from "lucide-react";
import type { Status } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";

const LOOK: Record<Status, { icon: typeof CircleCheck; className: string }> = {
  ready: { icon: CircleCheck, className: "bg-pass-soft text-pass" },
  needs_ok: { icon: Hand, className: "bg-hold-soft text-hold" },
  on_hold: { icon: OctagonPause, className: "bg-fail-soft text-fail" },
  not_checked: { icon: CircleDashed, className: "bg-pending-soft text-pending" },
};

/** Status is said in words and an icon; color only repeats it. */
export function StatusChip({ status, className }: { status: Status; className?: string }) {
  const { icon: Icon, className: look } = LOOK[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-medium whitespace-nowrap", look, className)}>
      <Icon className="size-4" aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}
