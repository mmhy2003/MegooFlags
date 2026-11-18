"use client";

import { CircleArrowUp } from "lucide-react";
import { cn } from "cn";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSidebar } from "@/components/ui/sidebar";
import {
  type SystemVersion,
  updateLabel,
  updateTooltip,
  versionLabel,
} from "@/lib/version";

// The same tone as the status pills: a light tint drops the text under 4.5:1, so only the
// dark theme gets a fill.
const PILL =
  "inline-flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-full border border-primary/40 text-sm font-medium whitespace-nowrap text-primary outline-none hover:bg-primary/10 focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-primary/10 dark:hover:bg-primary/20";

/** A link to the newer release's notes, opening in a new tab. Collapsed, it is an icon. */
export function UpdatePill({
  update,
  collapsed,
}: {
  update: { version: string; url: string };
  collapsed: boolean;
}) {
  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <a
              href={update.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${updateTooltip(update)} (opens in a new tab)`}
              className={cn(PILL, "size-11")}
            />
          }
        >
          <CircleArrowUp aria-hidden className="size-4" />
        </TooltipTrigger>
        <TooltipContent side="right" align="center">
          {updateTooltip(update)}
        </TooltipContent>
      </Tooltip>
    );
  }
  return (
    <a
      href={update.url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(PILL, "px-3")}
    >
      <CircleArrowUp aria-hidden className="size-4" />
      {updateLabel(update)}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/** The running version, and the update pill when the API reports a newer release. */
export function VersionFooter({ version }: { version: SystemVersion | null }) {
  const { state, isMobile } = useSidebar();
  if (!version) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 px-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
      <span
        data-testid="app-version"
        className="text-xs text-muted-foreground group-data-[collapsible=icon]:hidden"
      >
        {versionLabel(version.version)}
      </span>
      {version.update && (
        <UpdatePill
          update={version.update}
          collapsed={state === "collapsed" && !isMobile}
        />
      )}
    </div>
  );
}
