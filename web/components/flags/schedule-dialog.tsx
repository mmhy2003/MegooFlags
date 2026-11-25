"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { toast } from "sonner";

import { RequestFields } from "@/components/flags/request-fields";
import { WeightsEditor } from "@/components/flags/weights-editor";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import {
  defaultRunAt,
  localToIso,
  runAtProblem,
  SCHEDULE_ACTIONS,
  schedulePermission,
  utcText,
} from "@/lib/flags/schedules";
import { evenWeights, weightsValid } from "@/lib/flags/weights";
import type { MePermissions } from "@/lib/nav";
import { projectPath } from "@/lib/routes";

type FlagOut = components["schemas"]["FlagOut"];
type ScheduledAction = components["schemas"]["ScheduledAction"];
type Weight = components["schemas"]["WeightIn"];

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="text-sm text-destructive">
      {message}
    </p>
  );
}

function ScheduleForm({
  project,
  flag,
  env,
  envName,
  request,
  perms,
  onDone,
  onApprovalRequired,
}: {
  project: string;
  flag: FlagOut;
  env: string;
  envName: string;
  request: boolean;
  perms: MePermissions | null;
  onDone: () => void;
  onApprovalRequired: () => void;
}) {
  const id = useId();
  const allowed = (action: ScheduledAction) =>
    can(perms, schedulePermission(action), env);
  const [action, setAction] = useState<ScheduledAction>(
    () => SCHEDULE_ACTIONS.find((a) => allowed(a.value))?.value ?? "enable",
  );
  const [weights, setWeights] = useState<Weight[]>(() =>
    evenWeights(flag.variations.map((v) => v.key)),
  );
  const [now, setNow] = useState(() => new Date());
  const [when, setWhen] = useState(() => defaultRunAt(now));
  const [title, setTitle] = useState(
    `Schedule a change to ${flag.key} in ${envName}`,
  );
  const [description, setDescription] = useState("");
  const [sending, setSending] = useState(false);

  const iso = localToIso(when);
  const whenError = runAtProblem(when, now);
  const rollout = action === "set_rollout";
  const weightsError =
    rollout && !weightsValid(weights)
      ? "Weights must add up to 100 %"
      : undefined;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const at = new Date();
    setNow(at);
    if (iso === null || runAtProblem(when, at) || weightsError) return;
    setSending(true);
    const body = {
      action,
      run_at: iso,
      ...(rollout ? { weights } : {}),
    };
    try {
      if (request) {
        await must(
          api.POST("/api/projects/{project}/change-requests", {
            params: { path: { project } },
            body: {
              environment: env,
              flag: flag.key,
              kind: "schedule",
              title: title.trim(),
              description,
              payload: body,
            },
          }),
        );
        toast("Change request created", {
          description: (
            <Link
              href={projectPath(project, "change-requests")}
              className="underline"
            >
              View change requests
            </Link>
          ),
        });
      } else {
        await must(
          api.POST(
            "/api/projects/{project}/flags/{flag}/environments/{env}/schedules",
            { params: { path: { project, flag: flag.key, env } }, body },
          ),
        );
        toast.success(`Change scheduled in ${envName}`);
      }
      onDone();
    } catch (error) {
      setSending(false);
      if (error instanceof ApiError) {
        if (error.failure.code === "approval_required" && !request) {
          // The environment became protected meanwhile: ask for approval instead.
          onApprovalRequired();
          return;
        }
        toast.error(failureText(error.failure));
        return;
      }
      console.error("schedule failed", error);
      toast.error("Couldn't reach the server");
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <p id={`${id}-change`} className="font-medium">
          Change
        </p>
        <div
          role="radiogroup"
          aria-labelledby={`${id}-change`}
          className="flex flex-col"
        >
          {SCHEDULE_ACTIONS.map((a) => (
            <label
              key={a.value}
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2 has-disabled:text-muted-foreground"
            >
              <input
                type="radio"
                name={`${id}-action`}
                className="size-5 shrink-0 accent-primary"
                checked={action === a.value}
                disabled={!allowed(a.value)}
                onChange={() => setAction(a.value)}
              />
              {a.label}
            </label>
          ))}
        </div>
      </div>

      {rollout && (
        <div className="flex min-w-0 flex-col gap-3">
          <WeightsEditor
            type={flag.type}
            weights={weights}
            onChange={setWeights}
            readOnly={false}
            error={weightsError}
          />
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-when`}>When</Label>
        <Input
          id={`${id}-when`}
          type="datetime-local"
          value={when}
          aria-invalid={!!whenError}
          aria-describedby={`${id}-when-help${whenError ? ` ${id}-when-error` : ""}`}
          onChange={(e) => {
            setWhen(e.target.value);
            setNow(new Date());
          }}
          className="w-auto max-w-full text-base"
        />
        <p id={`${id}-when-help`} className="text-sm text-muted-foreground">
          Your time zone: {zone}
          {iso && (
            <>
              {" "}
              (<time dateTime={iso}>{utcText(iso)}</time>)
            </>
          )}
        </p>
        <FieldError id={`${id}-when-error`} message={whenError} />
      </div>

      {request && (
        <>
          <p className="text-sm text-muted-foreground">
            The change waits for a reviewer. If it is approved after this time,
            it fails.
          </p>
          <RequestFields
            title={title}
            onTitle={setTitle}
            description={description}
            onDescription={setDescription}
          />
        </>
      )}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button
          type="submit"
          disabled={
            sending ||
            !!whenError ||
            !!weightsError ||
            !allowed(action) ||
            (request && title.trim() === "")
          }
        >
          {request ? "Request schedule" : "Schedule"}
        </Button>
      </div>
    </form>
  );
}

/**
 * Schedules a change of the flag in one environment (turn on, turn off, or a new rollout of the
 * default rule) or, where the environment needs approval, opens a `schedule` change request for
 * it. A schedule refused because approval became required switches to the request form.
 */
export function ScheduleDialog({
  project,
  flag,
  env,
  envName,
  needsApproval,
  perms,
  open,
  onOpenChange,
  onDone,
  onApprovalRequired,
}: {
  project: string;
  flag: FlagOut;
  env: string;
  envName: string;
  needsApproval: boolean;
  perms: MePermissions | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** After a schedule or a request. */
  onDone: () => void;
  /** The API answered that the environment needs approval. */
  onApprovalRequired: () => void;
}) {
  const [switched, setSwitched] = useState(false);
  const request = needsApproval || switched;
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setSwitched(false);
        onOpenChange(next);
      }}
      title={
        request
          ? `Request a schedule in ${envName}`
          : `Schedule a change in ${envName}`
      }
      description={
        request
          ? "This environment needs approval, so the schedule waits for a reviewer."
          : "The change applies on its own at the time you pick."
      }
    >
      {open && (
        <ScheduleForm
          project={project}
          flag={flag}
          env={env}
          envName={envName}
          request={request}
          perms={perms}
          onDone={() => {
            setSwitched(false);
            onDone();
          }}
          onApprovalRequired={() => {
            setSwitched(true);
            onApprovalRequired();
          }}
        />
      )}
    </ResponsiveDialog>
  );
}
