"use client";

import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";
import { toast } from "sonner";

import { RequestFields } from "@/components/flags/request-fields";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  addStep,
  defaultFrom,
  defaultPlan,
  durationText,
  MAX_STEPS,
  planProblems,
  removeStep,
  type RolloutPlan,
  type StepDraft,
  toRolloutIn,
  totalMinutes,
  updateStep,
  WAIT_UNITS,
  type WaitUnit,
} from "@/lib/flags/rollout-plan";
import { projectPath } from "@/lib/routes";

type FlagOut = components["schemas"]["FlagOut"];
type Serve = components["schemas"]["Serve"];

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="text-sm text-destructive">
      {message}
    </p>
  );
}

function StepRow({
  step,
  n,
  errors,
  onChange,
  onRemove,
}: {
  step: StepDraft;
  n: number;
  errors: { percent?: string; wait?: string };
  onChange: (patch: Partial<Omit<StepDraft, "id">>) => void;
  onRemove: () => void;
}) {
  const id = useId();
  return (
    <li className="flex flex-col gap-1 border-b pb-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-14 font-medium">Step {n}</span>
        <div className="flex items-center gap-1">
          <Input
            type="number"
            inputMode="decimal"
            min={0}
            max={100}
            step="any"
            aria-label={`Step ${n} percent`}
            aria-invalid={!!errors.percent}
            aria-describedby={errors.percent ? `${id}-percent` : undefined}
            value={step.percent}
            onChange={(e) => onChange({ percent: e.target.value })}
            className="w-20 text-base"
          />
          <span aria-hidden>%</span>
        </div>
        <span className="text-muted-foreground">then wait</span>
        <Input
          type="number"
          inputMode="numeric"
          min={1}
          step={1}
          aria-label={`Step ${n} wait`}
          aria-invalid={!!errors.wait}
          aria-describedby={errors.wait ? `${id}-wait` : undefined}
          value={step.wait}
          onChange={(e) => onChange({ wait: e.target.value })}
          className="w-20 text-base"
        />
        <NativeSelect
          aria-label={`Step ${n} wait unit`}
          value={step.unit}
          onChange={(e) => onChange({ unit: e.target.value as WaitUnit })}
          className="w-auto"
        >
          {WAIT_UNITS.map((u) => (
            <option key={u.value} value={u.value}>
              {u.label}
            </option>
          ))}
        </NativeSelect>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="min-h-11 min-w-11"
          aria-label={`Remove step ${n}`}
          onClick={onRemove}
        >
          <Trash2 aria-hidden />
        </Button>
      </div>
      <FieldError id={`${id}-percent`} message={errors.percent} />
      <FieldError id={`${id}-wait`} message={errors.wait} />
    </li>
  );
}

/** When the first step applies and how long until 100 %. */
function summary(plan: RolloutPlan): string {
  const first = plan.steps[0];
  if (!first) return "100 % applies when you start.";
  return `The first step (${Number(first.percent)} %) applies when you start. 100 % after ${durationText(totalMinutes(plan))}.`;
}

function RolloutForm({
  project,
  flag,
  env,
  envName,
  request,
  fallthrough,
  onDone,
  onApprovalRequired,
}: {
  project: string;
  flag: FlagOut;
  env: string;
  envName: string;
  request: boolean;
  fallthrough: Serve | null;
  onDone: () => void;
  onApprovalRequired: () => void;
}) {
  const id = useId();
  const variations = flag.variations.map((v) => v.key);
  const [plan, setPlan] = useState<RolloutPlan>(() =>
    defaultPlan(
      fallthrough
        ? defaultFrom(fallthrough, variations)
        : (variations[0] ?? ""),
      variations,
    ),
  );
  // Null until edited, so the default follows To.
  const [title, setTitle] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [sending, setSending] = useState(false);

  const problems = planProblems(plan);
  const problem = (path: string) =>
    problems.find((p) => p.path === path)?.message;
  const shownTitle =
    title ?? `Roll out ${plan.to} in ${flag.key} in ${envName}`;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (problems.length > 0) return;
    setSending(true);
    const body = toRolloutIn(plan);
    try {
      if (request) {
        await must(
          api.POST("/api/projects/{project}/change-requests", {
            params: { path: { project } },
            body: {
              environment: env,
              flag: flag.key,
              kind: "rollout",
              title: shownTitle.trim(),
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
            "/api/projects/{project}/flags/{flag}/environments/{env}/rollout",
            { params: { path: { project, flag: flag.key, env } }, body },
          ),
        );
        toast.success(`Rollout started in ${envName}`);
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
        if (error.failure.code === "invalid_state") {
          // A rollout started meanwhile; the card shows it after the reload.
          toast.error(error.failure.message);
          onDone();
          return;
        }
        toast.error(failureText(error.failure));
        return;
      }
      console.error("rollout failed", error);
      toast.error("Couldn't reach the server");
    }
  };

  const toError = problem("to");
  const stepsError = problem("steps");
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-from`}>From</Label>
          <NativeSelect
            id={`${id}-from`}
            value={plan.from}
            onChange={(e) => setPlan({ ...plan, from: e.target.value })}
          >
            {variations.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-to`}>To</Label>
          <NativeSelect
            id={`${id}-to`}
            value={plan.to}
            aria-invalid={!!toError}
            aria-describedby={toError ? `${id}-to-error` : undefined}
            onChange={(e) => setPlan({ ...plan, to: e.target.value })}
          >
            {variations.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </NativeSelect>
          <FieldError id={`${id}-to-error`} message={toError} />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="font-semibold">Steps</h3>
        <ol aria-label="Rollout steps" className="flex flex-col gap-3">
          {plan.steps.map((s, i) => (
            <StepRow
              key={s.id}
              step={s}
              n={i + 1}
              errors={{
                percent: problem(`steps[${i}].percent`),
                wait: problem(`steps[${i}].wait`),
              }}
              onChange={(patch) => setPlan(updateStep(plan, s.id, patch))}
              onRemove={() => setPlan(removeStep(plan, s.id))}
            />
          ))}
          <li className="font-medium">Step {plan.steps.length + 1}: 100 %</li>
        </ol>
        <FieldError id={`${id}-steps-error`} message={stepsError} />
        <Button
          type="button"
          variant="outline"
          className="min-h-11 self-start"
          disabled={plan.steps.length + 1 >= MAX_STEPS}
          onClick={() => setPlan(addStep(plan))}
        >
          <Plus aria-hidden />
          Add step
        </Button>
      </div>

      {problems.length === 0 && <p>{summary(plan)}</p>}
      <p className="text-sm text-muted-foreground">
        Only the default rule changes. Rules and individual targets stay as they
        are.
      </p>

      {request && (
        <RequestFields
          title={shownTitle}
          onTitle={setTitle}
          description={description}
          onDescription={setDescription}
        />
      )}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button
          type="submit"
          disabled={
            sending ||
            problems.length > 0 ||
            (request && shownTitle.trim() === "")
          }
        >
          {request ? "Request rollout" : "Start rollout"}
        </Button>
      </div>
    </form>
  );
}

/**
 * Plans a progressive rollout of the default rule and starts it, or, where the environment
 * needs approval, opens a `rollout` change request for it. A start refused because approval
 * became required switches to the request form.
 */
export function RolloutDialog({
  project,
  flag,
  env,
  envName,
  needsApproval,
  fallthrough,
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
  /** The default rule as saved, for From; null while it loads. */
  fallthrough: Serve | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** After a start, a request, or a start that found a rollout already running. */
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
      title={`${request ? "Request" : "Start"} a rollout in ${envName}`}
      description={
        request
          ? "This environment needs approval, so the rollout waits for a reviewer."
          : "The default rule moves from one variation to another in steps, on a timer."
      }
    >
      {open && (
        <RolloutForm
          project={project}
          flag={flag}
          env={env}
          envName={envName}
          request={request}
          fallthrough={fallthrough}
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
