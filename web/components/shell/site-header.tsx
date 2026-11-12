"use client";

import Link from "next/link";

import { BrandMark } from "@/components/shell/brand";
import { ThemeSwitcher } from "@/components/shell/theme-switcher";
import { UserMenu } from "@/components/shell/user-menu";
import { SidebarTrigger } from "@/components/ui/sidebar";
import type { MeOut } from "@/lib/shell-data";

/** The sticky top bar. Below 768 px it carries the menu button and the logo; phones switch
 * theme through the command palette. */
export function SiteHeader({ me }: { me: MeOut | null }) {
  return (
    <header className="sticky top-0 z-20 flex min-h-[var(--header-height)] min-w-0 items-center gap-2 border-b bg-background/95 px-2 pt-[env(safe-area-inset-top)] backdrop-blur md:px-4">
      <SidebarTrigger aria-label="Open menu" className="md:hidden" />
      <Link
        href="/"
        aria-label="MegooFlags home"
        className="inline-flex size-11 shrink-0 items-center justify-center md:hidden"
      >
        <BrandMark />
      </Link>
      <div className="ml-auto flex min-w-0 items-center gap-1">
        <ThemeSwitcher className="hidden md:inline-flex" />
        {me && <UserMenu me={me} />}
      </div>
    </header>
  );
}
