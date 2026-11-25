"use client";

import { CalendarClock, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { StatusPill } from "@/components/patterns/status-pill";
import { Button } from "@/components/ui/button";
import { useVisibleInterval } from "@/hooks/use-visible-interval";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
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

/** Cancels a pending schedule after a confirmation. */
function CancelButton({
  project,
  flag,
  env,
  envName,
  schedule,
  onChanged,
}: {
  project: string;
  flag: string;
  env: string;
  envName: string;
  schedule: Schedule;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const cancel = async () => {
    setBusy(true);
    try {
      await must(
        api.DELETE(
          "/api/projects/{project}/flags/{flag}/environments/{env}/schedules/{id}",
          { params: { path: { project, flag, env, id: schedule.id } } },
        ),
      );
      toast.success("Scheduled change cancelled");
    } catch (error) {
      if (error instanceof ApiError) toast.error(failureText(error.failure));
      else {
        console.error("cancel schedule failed", error);
        toast.error("Couldn't reach the server");
      }
    } finally {
      setBusy(false);
      // Also after a failure: the change may have run or been cancelled meanwhile.
      onChanged();
    }
  };
  return (
    <ConfirmDialog
      destructive
      title="Cancel this change?"
      description={
        <>
          {scheduleText(schedule)} in {envName} on <At iso={schedule.run_at} />{" "}
          won&apos;t happen.
        </>
      }
      confirmLabel="Cancel change"
      onConfirm={() => void cancel()}
      trigger={
        <Button variant="outline" className="min-h-11" disabled={busy}>
          <X aria-hidden />
          Cancel
        </Button>
      }
    />
  );
}

/**
 * The environment's scheduled changes: upcoming ones soonest first, then the past ones folded.
 * With `onSchedule`, it offers Schedule a change (or Request, where the environment needs
 * approval); `canCancel` tells which upcoming changes offer Cancel.
 */
export function ScheduleList({
  project,
  flag,
  env,
  envName,
  schedules,
  needsApproval,
  onSchedule,
  canCancel,
  onChanged,
}: {
  project: string;
  flag: string;
  env: string;
  envName: string;
  schedules: Schedule[];
  needsApproval: boolean;
  /** Opens the schedule dialog; absent without the permission. */
  onSchedule?: () => void;
  canCancel: (s: Schedule) => boolean;
  /** After a cancel: reload the schedules. */
  onChanged: () => void;
}) {
  const [now, setNow] = useState(() => new Date());
  useVisibleInterval(() => setNow(new Date()), 30_000);
  const next = upcoming(schedules);
  const done = past(schedules);

  return (
    <div className="flex flex-col gap-4">
      {onSchedule && (
        <Button className="min-h-11 self-start" onClick={onSchedule}>
          <CalendarClock aria-hidden />
          {needsApproval ? "Request a schedule" : "Schedule a change"}
        </Button>
      )}
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
                {canCancel(s) && (
                  <span className="self-start">
                    <CancelButton
                      project={project}
                      flag={flag}
                      env={env}
                      envName={envName}
                      schedule={s}
                      onChanged={onChanged}
                    />
                  </span>
                )}
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
