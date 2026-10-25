"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/", label: "Pay requests" },
  { href: "/approve", label: "Approve" },
  { href: "/upload", label: "Upload Portal" },
  { href: "/log", label: "Log" },
  { href: "/security", label: "Security" },
  { href: "/how", label: "How it works" },
];

export function TopBar() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b bg-sheet">
      <div className="mx-auto flex min-h-16 max-w-[1440px] flex-wrap items-center gap-x-8 gap-y-2 px-4 sm:px-8">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2 *:whitespace-nowrap">
          <Link href="/" className="flex items-center">
            <Image src="/imgs/draw-logo.png" alt="Draw" width={676} height={238} priority className="h-8 w-auto" />
          </Link>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto max-sm:-mx-4 max-sm:w-[calc(100%+2rem)] sm:ml-auto">
          {TABS.map((t) => {
            const active = t.href === "/" ? path === "/" : path.startsWith(t.href);
            return (
              <Link
                key={t.href}
                href={t.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-[52px] items-center border-b-[3px] border-transparent px-2.5 font-medium sm:px-3.5 whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground focus-visible:-outline-offset-[3px] sm:min-h-16",
                  active && "border-primary font-semibold text-foreground",
                )}
              >
                {t.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
