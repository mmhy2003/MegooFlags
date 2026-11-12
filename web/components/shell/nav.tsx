"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Flag,
  GitPullRequest,
  KeyRound,
  Layers,
  Plug,
  ScrollText,
  Settings,
  Target,
  type LucideIcon,
} from "lucide-react";
import { cn } from "cn";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import type { NavItem, NavKey } from "@/lib/nav";

const ICONS: Record<NavKey, LucideIcon> = {
  flags: Flag,
  segments: Target,
  environments: Layers,
  "change-requests": GitPullRequest,
  audit: ScrollText,
  keys: KeyRound,
  integrations: Plug,
  settings: Settings,
};

function isCurrent(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The main menu. The current item has aria-current, an accent bar and bold text, not colour alone. */
export function Nav({ items }: { items: NavItem[] }) {
  const pathname = usePathname();
  const { isMobile, setOpenMobile } = useSidebar();
  return (
    <nav aria-label="Main" className="p-2">
      <SidebarMenu>
        {items.map((item) => {
          const Icon = ICONS[item.key];
          const active = isCurrent(pathname, item.href);
          return (
            <SidebarMenuItem key={item.key}>
              <SidebarMenuButton
                isActive={active}
                tooltip={item.label}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative",
                  active &&
                    "font-semibold before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-accent",
                )}
                render={
                  <Link
                    href={item.href}
                    onClick={() => {
                      if (isMobile) setOpenMobile(false);
                    }}
                  />
                }
              >
                <Icon aria-hidden />
                <span>{item.label}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
      </SidebarMenu>
    </nav>
  );
}
