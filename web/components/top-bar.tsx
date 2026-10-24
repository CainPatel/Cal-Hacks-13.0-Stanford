"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/", label: "Pay requests" },
  { href: "/upload", label: "Upload" },
  { href: "/approve", label: "Approve" },
  { href: "/security", label: "Security" },
];

export function TopBar() {
  const path = usePathname();
  const dark = path.startsWith("/security");
  return (
    <header className={cn("sticky top-0 z-20 border-b bg-sheet/95 backdrop-blur-sm", dark && "dark border-border bg-background/95 text-foreground")}>
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-3 px-4 sm:gap-6 sm:px-6 lg:px-8">
        <Link href="/" className="flex items-baseline gap-2 text-lg font-semibold tracking-tight">
          <span aria-hidden className="inline-block size-3 translate-y-px rounded-[3px] bg-primary" />
          Draw
        </Link>
        <nav aria-label="Main" className="-mb-px flex h-full items-stretch gap-1 overflow-x-auto">
          {TABS.map((t) => {
            const active = t.href === "/" ? path === "/" : path.startsWith(t.href);
            return (
              <Link
                key={t.href}
                href={t.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center border-b-2 border-transparent px-2 text-[15px] sm:px-3 font-medium whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground",
                  active && "border-primary text-foreground",
                )}
              >
                {t.label}
              </Link>
            );
          })}
        </nav>
        <p className="ml-auto hidden text-sm text-muted-foreground md:block">Ridgeline Builders · demo data, synthetic</p>
      </div>
    </header>
  );
}
