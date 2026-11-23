"use client";

import { ChevronDown } from "lucide-react";

import { DiffViewer } from "@/components/patterns/diff-viewer";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { components } from "@/lib/api/schema";
import { snapshotText } from "@/lib/audit/actions";

type Entry = components["schemas"]["AuditEntryOut"];

/**
 * An audit entry's "Details" disclosure: the before and after snapshots as a diff, and the
 * sign-in method, address, user agent and request ID it was recorded with, each when set.
 */
export function AuditEntryDetails({ entry }: { entry: Entry }) {
  const facts = [
    { label: "Sign-in method", value: entry.actor.auth_method },
    { label: "IP address", value: entry.ip },
    { label: "User agent", value: entry.user_agent, note: "as recorded" },
    { label: "Request ID", value: entry.request_id },
  ].filter((f) => f.value);
  const diff = entry.before !== null || entry.after !== null;

  return (
    <Collapsible>
      <CollapsibleTrigger className="group/details inline-flex min-h-11 min-w-11 cursor-pointer items-center gap-1 text-sm font-medium">
        Details
        <ChevronDown
          aria-hidden
          className="size-4 transition-transform group-data-[panel-open]/details:rotate-180"
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="flex min-w-0 flex-col gap-3 pb-2">
        {diff && (
          <DiffViewer
            label="Before and after"
            before={snapshotText(entry.before)}
            after={snapshotText(entry.after)}
          />
        )}
        {facts.length > 0 && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            {facts.map((f) => (
              <div key={f.label} className="contents">
                <dt className="text-muted-foreground">
                  <span>{f.label}</span>{" "}
                  {f.note && <span className="block text-xs">{f.note}</span>}
                </dt>
                <dd className="min-w-0 break-all">{f.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {!diff && facts.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Nothing more was recorded.
          </p>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
