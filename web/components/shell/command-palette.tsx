"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Flag, Layers, Monitor, Moon, Sun, Target } from "lucide-react";

import { NAV_ICONS } from "@/components/shell/nav";
import { useApplyTheme } from "@/components/shell/theme-switcher";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { api, type components } from "@/lib/api/client";
import type { NavItem } from "@/lib/nav";
import { flagPath, projectPath } from "@/lib/routes";
import type { Theme } from "@/lib/theme";

type FlagSummary = components["schemas"]["FlagSummary"];
type Named = { key: string; name: string };
type ProjectLists = {
  project: string;
  segments: Named[];
  environments: Named[];
};

const THEMES: { theme: Theme; label: string; Icon: typeof Sun }[] = [
  { theme: "light", label: "Use light theme", Icon: Sun },
  { theme: "dark", label: "Use dark theme", Icon: Moon },
  { theme: "system", label: "Use system theme", Icon: Monitor },
];

/** Searches flags as you type (debounced) and the project's segments and environments once. */
function useProjectSearch(
  open: boolean,
  project: string | null,
  query: string,
) {
  const [flags, setFlags] = useState<FlagSummary[]>([]);
  const [lists, setLists] = useState<ProjectLists | null>(null);
  const request = useRef(0);
  const q = query.trim();

  useEffect(() => {
    if (!open || !project || !q) return;
    const id = ++request.current;
    const timer = setTimeout(async () => {
      const { data } = await api.GET("/api/projects/{project}/flags", {
        params: { path: { project }, query: { q, limit: 8 } },
      });
      if (id === request.current) setFlags(data?.items ?? []);
    }, 200);
    return () => clearTimeout(timer);
  }, [open, project, q]);

  useEffect(() => {
    if (!open || !project || lists?.project === project) return;
    let current = true;
    void Promise.all([
      api.GET("/api/projects/{project}/segments", {
        params: { path: { project } },
      }),
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ]).then(([segments, environments]) => {
      if (current)
        setLists({
          project,
          segments: segments.data?.items ?? [],
          environments: environments.data ?? [],
        });
    });
    return () => {
      current = false;
    };
  }, [open, project, lists?.project]);

  const ready = project !== null && lists?.project === project;
  return {
    flags: project && q ? flags : [],
    segments: ready ? lists.segments : [],
    environments: ready ? lists.environments : [],
  };
}

/** The ⌘K / Ctrl+K palette: pages, flags, segments, environments and theme actions. Full screen
 * below 768 px. */
export function CommandPalette({
  open,
  onOpenChange,
  project,
  items,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string | null;
  items: NavItem[];
}) {
  const router = useRouter();
  const applyTheme = useApplyTheme();
  const [query, setQuery] = useState("");
  const { flags, segments, environments } = useProjectSearch(
    open,
    project,
    query,
  );

  function change(next: boolean) {
    if (!next) setQuery("");
    onOpenChange(next);
  }

  function run(action: () => void) {
    change(false);
    action();
  }

  return (
    <CommandDialog
      open={open}
      onOpenChange={change}
      title="Search"
      description="Search flags, segments, environments, pages and actions"
      className="sm:max-w-none md:max-w-lg max-md:top-0 max-md:left-0 max-md:h-dvh max-md:w-screen max-md:max-w-none max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none!"
    >
      <Command className="max-md:rounded-none!">
        <CommandInput
          value={query}
          onValueChange={setQuery}
          placeholder="Search flags, segments, settings…"
          className="max-md:text-base"
        />
        <CommandList className="max-md:max-h-none max-md:flex-1">
          <CommandEmpty>No results</CommandEmpty>
          <CommandGroup heading="Pages">
            {items.map((item) => {
              const Icon = NAV_ICONS[item.key];
              return (
                <CommandItem
                  key={item.key}
                  value={item.label}
                  onSelect={() => run(() => router.push(item.href))}
                >
                  <Icon aria-hidden />
                  {item.label}
                </CommandItem>
              );
            })}
          </CommandGroup>
          {project && flags.length > 0 && (
            <CommandGroup heading="Flags">
              {flags.map((flag) => (
                <CommandItem
                  key={flag.key}
                  value={`${flag.key} ${flag.name}`}
                  onSelect={() =>
                    run(() => router.push(flagPath(project, flag.key)))
                  }
                >
                  <Flag aria-hidden />
                  <span className="min-w-0 truncate font-mono">{flag.key}</span>
                  <span className="min-w-0 truncate text-muted-foreground">
                    {flag.name}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {project && segments.length > 0 && (
            <CommandGroup heading="Segments">
              {segments.map((segment) => (
                <CommandItem
                  key={segment.key}
                  value={`${segment.name} ${segment.key}`}
                  onSelect={() =>
                    run(() => router.push(projectPath(project, "segments")))
                  }
                >
                  <Target aria-hidden />
                  {segment.name}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {project && environments.length > 0 && (
            <CommandGroup heading="Environments">
              {environments.map((env) => (
                <CommandItem
                  key={env.key}
                  value={`${env.name} ${env.key}`}
                  onSelect={() =>
                    run(() => router.push(projectPath(project, "environments")))
                  }
                >
                  <Layers aria-hidden />
                  {env.name}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          <CommandGroup heading="Actions">
            {THEMES.map(({ theme, label, Icon }) => (
              <CommandItem
                key={theme}
                value={label}
                onSelect={() => run(() => applyTheme(theme))}
              >
                <Icon aria-hidden />
                {label}
              </CommandItem>
            ))}
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
