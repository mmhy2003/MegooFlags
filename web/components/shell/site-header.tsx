"use client";

import Link from "next/link";

import { BrandMark } from "@/components/shell/brand";
import { SidebarTrigger } from "@/components/ui/sidebar";

/** The sticky top bar. Below 768 px it carries the menu button and the logo. */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-20 flex min-h-[var(--header-height)] items-center gap-2 border-b bg-background/95 px-2 pt-[env(safe-area-inset-top)] backdrop-blur md:px-4">
      <SidebarTrigger aria-label="Open menu" className="md:hidden" />
      <Link
        href="/"
        aria-label="MegooFlags home"
        className="inline-flex size-11 items-center justify-center md:hidden"
      >
        <BrandMark />
      </Link>
    </header>
  );
}
