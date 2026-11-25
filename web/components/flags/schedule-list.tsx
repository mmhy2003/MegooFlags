"use client";

import Link from "next/link";
import { useState } from "react";

import { StatusPill } from "@/components/patterns/status-pill";
import { useVisibleInterval } from "@/hooks/use-visible-interval";
import type { components } from "@/lib/api/schema";
import {
  past,
  relativeText,
  scheduleText,
  schedulePill,
  upcoming,
} from "@/lib/flags/schedules";
import { changeRequestPath } from "@/lib/routes";

type Schedule = components["schemas"]["ScheduleOut"];

const TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function At({ iso }: { iso: string }) {
  return <time dateTime={iso}>{TIME.format(new Date(iso))}</time>;
}

function PastList({ schedules }: { schedules: Schedule[] }) {
  if (schedules.length === 0)
    return <p className="text-muted-foreground">No past changes</p>;
  return (
    <ul aria-label="Past changes" className="flex flex-col gap-2">
      {schedules.map((s) => {
        const pill = schedulePill(s.status);
        return (
          <li
            key={s.id}
            className="flex flex-col gap-1 border-b pb-2 last:border-b-0"
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <StatusPill status={pill.status} label={pill.label} />
              <span className="min-w-0 break-words">{scheduleText(s)}</span>
              <span className="text-sm text-muted-foreground">
                <At iso={s.executed_at ?? s.run_at} />
              </span>
            </div>
            {s.error && (
              <p className="text-sm break-words text-destructive">{s.error}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** The environment's scheduled changes: upcoming ones soonest first, then the past ones folded. */
export function ScheduleList({
  project,
  schedules,
}: {
  project: string;
  schedules: Schedule[];
}) {
  const [now, setNow] = useState(() => new Date());
  useVisibleInterval(() => setNow(new Date()), 30_000);
  const next = upcoming(schedules);
  const done = past(schedules);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <h3 className="font-semibold">Upcoming</h3>
        {next.length === 0 ? (
          <p className="text-muted-foreground">No scheduled changes</p>
        ) : (
          <ul aria-label="Upcoming" className="flex flex-col gap-2">
            {next.map((s) => (
              <li
                key={s.id}
                className="flex flex-col gap-1 border-b pb-2 last:border-b-0"
              >
                <span className="font-medium break-words">
                  {scheduleText(s)}
                </span>
                <span className="text-sm text-muted-foreground">
                  <At iso={s.run_at} /> (
                  <time dateTime={s.run_at}>{relativeText(s.run_at, now)}</time>
                  )
                </span>
                <span className="flex flex-wrap items-center gap-x-3 text-sm text-muted-foreground">
                  <span className="break-words">By {s.created_by}</span>
                  {s.change_request_id && (
                    <Link
                      href={changeRequestPath(project, s.change_request_id)}
                      className="inline-flex min-h-11 items-center text-primary underline underline-offset-4"
                    >
                      Change request
                    </Link>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <details>
        <summary className="flex min-h-11 cursor-pointer items-center font-semibold">
          Past changes ({done.length})
        </summary>
        <PastList schedules={done} />
      </details>
    </div>
  );
}
