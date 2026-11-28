"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { Search } from "lucide-react";

import { BrandMark } from "@/components/shell/brand";
import { CommandPalette } from "@/components/shell/command-palette";
import { ThemeSwitcher } from "@/components/shell/theme-switcher";
import { UserMenu } from "@/components/shell/user-menu";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import type { NavItem } from "@/lib/nav";
import type { MeOut } from "@/lib/shell-data";
import { isPaletteShortcut } from "@/lib/shortcuts";

const noop = () => () => {};

/** The sticky top bar. Below 768 px it carries the menu button and the logo; phones switch
 * theme through the command palette. */
export function SiteHeader({
  me,
  project,
  items,
}: {
  me: MeOut | null;
  project: string | null;
  items: NavItem[];
}) {
  const [open, setOpen] = useState(false);
  // The platform is only known in the browser; the server renders Ctrl+K.
  const mac = useSyncExternalStore(
    noop,
    () => /Mac|iPhone|iPad/.test(navigator.platform),
    () => false,
  );

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (!isPaletteShortcut(e)) return;
      e.preventDefault();
      setOpen((o) => !o);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

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
      <Button
        variant="outline"
        aria-keyshortcuts="Meta+K Control+K"
        onClick={() => setOpen(true)}
        className="hidden min-h-11 max-w-md min-w-0 flex-1 justify-start text-muted-foreground md:flex"
      >
        <Search aria-hidden />
        <span className="min-w-0 truncate">
          Search flags, segments, settings…
        </span>
        <kbd className="ml-auto rounded border px-1.5 font-mono text-xs">
          {mac ? "⌘K" : "Ctrl+K"}
        </kbd>
      </Button>
      <div className="ml-auto flex min-w-0 items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Search"
          onClick={() => setOpen(true)}
          className="md:hidden"
        >
          <Search aria-hidden />
        </Button>
        <ThemeSwitcher className="hidden md:inline-flex" />
        {me && <UserMenu me={me} />}
      </div>
      <CommandPalette
        open={open}
        onOpenChange={setOpen}
        project={project}
        items={items}
      />
    </header>
  );
}
