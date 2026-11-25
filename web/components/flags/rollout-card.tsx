"use client";

import { OctagonX, Pause, Play } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DANGER_TRIGGER } from "@/components/flags/delete-flag-dialog";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { StatusPill } from "@/components/patterns/status-pill";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  currentPercent,
  durationText,
  rolloutPill,
  type StepState,
  stepStates,
  variationText,
} from "@/lib/flags/rollout-plan";

type Rollout = components["schemas"]["ProgressiveRolloutOut"];
type RolloutView = components["schemas"]["RolloutView"];

const TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function isLive(r: Rollout | null): r is Rollout {
  return r?.status === "active" || r?.status === "paused";
}

function fromTo(r: Rollout): string {
  return `${variationText(r.from_variation)} → ${variationText(r.to_variation)}`;
}

function At({ iso }: { iso: string }) {
  return <time dateTime={iso}>{TIME.format(new Date(iso))}</time>;
}

/** When the step after the current one applies: its time, or the wait left while paused. */
function NextAt({ rollout }: { rollout: Rollout }) {
  if (rollout.status === "paused" && rollout.remaining_seconds !== null) {
    const minutes = Math.max(1, Math.ceil(rollout.remaining_seconds / 60));
    return (
      <span>
        <time dateTime={`PT${minutes}M`}>{durationText(minutes)}</time> left
      </span>
    );
  }
  if (rollout.next_step_at) return <At iso={rollout.next_step_at} />;
  return null;
}

const STATE_LABEL: Record<StepState["state"], string> = {
  done: "Done",
  current: "Current",
  next: "Later",
};

function Steps({ rollout }: { rollout: Rollout }) {
  const states = stepStates(rollout);
  return (
    <ol aria-label="Steps" className="flex flex-col gap-1">
      {states.map((s, i) => {
        const next = i === rollout.current_step + 1;
        return (
          <li
            key={i}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b py-2 last:border-b-0"
          >
            <span className="w-16 font-medium tabular-nums">{s.percent} %</span>
            <span
              className={
                s.state === "current" ? "font-medium" : "text-muted-foreground"
              }
            >
              {next ? "Next" : STATE_LABEL[s.state]}
            </span>
            {next && (
              <span className="text-muted-foreground">
                <NextAt rollout={rollout} />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Recent({ rollouts }: { rollouts: Rollout[] }) {
  if (rollouts.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-semibold">Recent rollouts</h3>
      <ul aria-label="Recent rollouts" className="flex flex-col gap-2">
        {rollouts.map((r) => {
          const pill = rolloutPill(r.status);
          return (
            <li
              key={r.id}
              className="flex flex-col gap-1 border-b pb-2 last:border-b-0"
            >
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <StatusPill status={pill.status} label={pill.label} />
                <span className="min-w-0 break-words">{fromTo(r)}</span>
                <span className="text-sm text-muted-foreground">
                  <At iso={r.finished_at ?? r.updated_at} />
                </span>
              </div>
              {r.error && (
                <p className="text-sm break-words text-destructive">
                  {r.error}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

type Action = "pause" | "resume" | "abort";

const DONE: Record<Action, string> = {
  pause: "Rollout paused",
  resume: "Rollout resumed",
  abort: "Rollout aborted",
};

function send(
  action: Action,
  path: { project: string; flag: string; env: string },
) {
  const params = { params: { path } };
  if (action === "pause")
    return api.POST(
      "/api/projects/{project}/flags/{flag}/environments/{env}/rollout/pause",
      params,
    );
  if (action === "resume")
    return api.POST(
      "/api/projects/{project}/flags/{flag}/environments/{env}/rollout/resume",
      params,
    );
  return api.POST(
    "/api/projects/{project}/flags/{flag}/environments/{env}/rollout/abort",
    params,
  );
}

/** The live rollout's actions: Pause or Resume, and Abort after a confirmation. */
function Actions({
  project,
  flag,
  env,
  envName,
  rollout,
  onChanged,
}: {
  project: string;
  flag: string;
  env: string;
  envName: string;
  rollout: Rollout;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const act = async (action: Action) => {
    setBusy(true);
    try {
      await must(send(action, { project, flag, env }));
      toast.success(DONE[action]);
      onChanged();
    } catch (error) {
      if (!(error instanceof ApiError)) {
        console.error("rollout action failed", error);
        toast.error("Couldn't reach the server");
      } else if (error.failure.code === "invalid_state") {
        // The rollout moved on meanwhile; show what it is now.
        toast.error(error.failure.message);
        onChanged();
      } else {
        toast.error(failureText(error.failure));
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap gap-2">
      {rollout.status === "active" ? (
        <Button
          variant="outline"
          className="min-h-11"
          disabled={busy}
          onClick={() => void act("pause")}
        >
          <Pause aria-hidden />
          Pause
        </Button>
      ) : (
        <Button
          variant="outline"
          className="min-h-11"
          disabled={busy}
          onClick={() => void act("resume")}
        >
          <Play aria-hidden />
          Resume
        </Button>
      )}
      <ConfirmDialog
        destructive
        title={`Abort the rollout in ${envName}?`}
        description={`The default rule serves ${variationText(rollout.from_variation)} to everyone again. Pause keeps the current split instead.`}
        confirmLabel="Abort rollout"
        onConfirm={() => void act("abort")}
        trigger={
          <Button
            variant="destructive"
            className={DANGER_TRIGGER}
            disabled={busy}
          >
            <OctagonX aria-hidden />
            Abort
          </Button>
        }
      />
    </div>
  );
}

/**
 * The environment's progressive rollout: the live one with its progress and steps, or a short
 * explanation when none runs, then the recent finished ones. With `canWrite`, it offers Start
 * (or Request, where the environment needs approval), Pause, Resume and Abort.
 */
export function RolloutCard({
  project,
  flag,
  env,
  rollout,
  envName,
  enabled,
  canWrite,
  needsApproval,
  onStart,
  onChanged,
}: {
  project: string;
  flag: string;
  env: string;
  rollout: RolloutView;
  envName: string;
  enabled: boolean;
  /** `flag.config.write` in the environment, and the flag is not archived. */
  canWrite: boolean;
  needsApproval: boolean;
  onStart: () => void;
  /** After an action: reload the rollout and the flag. */
  onChanged: () => void;
}) {
  const live = isLive(rollout.current) ? rollout.current : null;
  const recent = rollout.recent.filter((r) => r.id !== live?.id);

  if (!live)
    return (
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <p className="font-medium">No rollout running</p>
          <p className="text-muted-foreground">
            A progressive rollout moves the default rule from one variation to
            another in steps, on a timer.
          </p>
        </div>
        {canWrite && (
          <Button className="min-h-11 self-start" onClick={onStart}>
            <Play aria-hidden />
            {needsApproval ? "Request a rollout" : "Start a rollout"}
          </Button>
        )}
        <Recent rollouts={recent} />
      </div>
    );

  const pill = rolloutPill(live.status);
  const percent = currentPercent(live);
  const to = variationText(live.to_variation);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusPill status={pill.status} label={pill.label} />
        <span className="min-w-0 font-medium break-words">{fromTo(live)}</span>
      </div>
      <p className="text-sm text-muted-foreground">
        Started by {live.created_by} on <At iso={live.created_at} />
      </p>
      {!enabled && (
        <p className="text-sm text-warning">
          The default rule serves nobody until the flag is on in {envName}.
        </p>
      )}
      <div className="flex flex-col gap-2">
        <p>
          Serving {to} to <span className="font-medium">{percent} %</span>
        </p>
        <div
          role="progressbar"
          aria-label={`Serving ${to}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="h-2 w-full overflow-hidden rounded-full bg-muted"
        >
          <div
            className="h-full rounded-full bg-primary"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>
      <Steps rollout={live} />
      {canWrite && (
        <Actions
          project={project}
          flag={flag}
          env={env}
          envName={envName}
          rollout={live}
          onChanged={onChanged}
        />
      )}
      <Recent rollouts={recent} />
    </div>
  );
}
