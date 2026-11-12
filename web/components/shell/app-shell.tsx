"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { toast } from "sonner";

import { Brand } from "@/components/shell/brand";
import { Nav } from "@/components/shell/nav";
import { ProjectSwitcher } from "@/components/shell/project-switcher";
import { SiteHeader } from "@/components/shell/site-header";
import { ThemeSync } from "@/components/shell/theme-sync";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarMenuButton,
  SidebarProvider,
  useSidebar,
} from "@/components/ui/sidebar";
import { useMediaQuery } from "@/hooks/use-media-query";
import { navItems } from "@/lib/nav";
import { writeCookie } from "@/lib/cookie";
import { savePreferences } from "@/lib/preferences";
import type { ShellData } from "@/lib/shell-data";
import { SIDEBAR_COOKIE } from "@/lib/theme";

function CollapseButton() {
  const { open, toggleSidebar } = useSidebar();
  const label = open ? "Collapse sidebar" : "Expand sidebar";
  const Icon = open ? PanelLeftClose : PanelLeftOpen;
  return (
    <SidebarMenuButton
      tooltip={label}
      onClick={toggleSidebar}
      className="hidden lg:flex"
    >
      <Icon aria-hidden />
      <span>{label}</span>
    </SidebarMenuButton>
  );
}

/**
 * The signed-in layout. From 1024 px the sidebar is open or collapsed as the user chose (saved
 * on the user); from 768 to 1023 px it is forced to icons; below 768 px it is a drawer.
 */
export function AppShell({
  shell,
  children,
}: {
  shell: ShellData;
  children: React.ReactNode;
}) {
  const params = useParams<{ project?: string }>();
  const project = params.project
    ? decodeURIComponent(params.project)
    : shell.permissionsProject;
  const permissions = useProjectPermissions(
    project,
    shell.permissions,
    shell.permissionsProject,
  );
  const items = navItems(project, permissions);
  const isDesktop = useMediaQuery("(min-width: 1024px)", true);
  const [userOpen, setUserOpen] = useState(shell.sidebarOpen);

  // Below 1024 px the sidebar is forced closed, so a toggle (Ctrl/⌘+B) changes nothing and
  // must not overwrite the saved choice.
  function onOpenChange(open: boolean) {
    if (!isDesktop) return;
    setUserOpen(open);
    writeCookie(SIDEBAR_COOKIE, String(open));
    void savePreferences({ sidebar_collapsed: !open }).then((saved) => {
      if (!saved)
        toast.error(
          "Couldn't save this preference. It applies in this browser only.",
        );
    });
  }

  return (
    <SidebarProvider
      open={isDesktop ? userOpen : false}
      onOpenChange={onOpenChange}
    >
      <ThemeSync stored={shell.me?.preferences?.theme} />
      <Sidebar collapsible="icon">
        <SidebarHeader className="gap-2">
          <Link
            href="/"
            aria-label="MegooFlags home"
            className="flex min-h-11 items-center rounded-md px-1"
          >
            <Brand />
          </Link>
          <ProjectSwitcher projects={shell.projects} current={project} />
        </SidebarHeader>
        <SidebarContent>
          <Nav items={items} />
        </SidebarContent>
        <SidebarFooter>
          <CollapseButton />
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="min-w-0">
        <SiteHeader me={shell.me} project={project} items={items} />
        <main
          id="main"
          className="min-w-0 flex-1 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] md:p-6"
        >
          {children}
        </main>
      </SidebarInset>
    </SidebarProvider>
  );
}
