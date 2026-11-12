"use client";

import { usePathname, useRouter } from "next/navigation";
import { Check, ChevronsUpDown, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { switchProjectPath } from "@/lib/routes";
import type { ProjectSummary } from "@/lib/shell-data";

/** Picks the project; the current section is kept where the other project has it. */
export function ProjectSwitcher({
  projects,
  current,
}: {
  /** Null when the list failed to load. */
  projects: ProjectSummary[] | null;
  current: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();

  if (projects === null) {
    return (
      <div className="flex flex-col gap-1 px-2 text-sm group-data-[collapsible=icon]:hidden">
        <p className="text-muted-foreground">Couldn&apos;t load projects</p>
        <Button variant="outline" size="sm" onClick={() => router.refresh()}>
          <RotateCw aria-hidden />
          Retry
        </Button>
      </div>
    );
  }
  if (projects.length === 0) return null;

  const selected = projects.find((p) => p.key === current);
  const label = selected?.name ?? "Choose a project";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <SidebarMenuButton
            size="lg"
            className="border border-sidebar-border group-data-[collapsible=icon]:border-0"
          />
        }
      >
        <span
          aria-hidden
          className="flex size-8 shrink-0 items-center justify-center rounded-md bg-sidebar-primary font-semibold text-sidebar-primary-foreground uppercase"
        >
          {label.charAt(0)}
        </span>
        <span className="flex min-w-0 flex-1 flex-col text-left leading-tight">
          <span aria-hidden className="text-xs text-muted-foreground">
            Project
          </span>
          <span className="truncate font-medium">
            <span className="sr-only">Project: </span>
            {label}
          </span>
        </span>
        <ChevronsUpDown aria-hidden className="ml-auto" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="min-w-56">
        {projects.map((project) => (
          <DropdownMenuItem
            key={project.key}
            onClick={() =>
              router.push(switchProjectPath(pathname, project.key))
            }
          >
            <span className="min-w-0 flex-1 truncate">{project.name}</span>
            {project.key === current && <Check aria-label="Current" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
