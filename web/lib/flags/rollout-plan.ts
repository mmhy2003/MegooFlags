import type { Status } from "@/components/patterns/status-pill";
import type { components } from "@/lib/api/schema";
import { newId } from "@/lib/flags/config-draft";
import { fromPercent, toPercent, WEIGHT_TOTAL } from "@/lib/flags/weights";

type S = components["schemas"];

export type WaitUnit = "minutes" | "hours" | "days";

export const UNIT_MINUTES: Record<WaitUnit, number> = {
  minutes: 1,
  hours: 60,
  days: 1440,
};

export const WAIT_UNITS: { value: WaitUnit; label: string }[] = [
  { value: "minutes", label: "Minutes" },
  { value: "hours", label: "Hours" },
  { value: "days", label: "Days" },
];

/** The most steps a rollout can have, including the final 100 %. */
export const MAX_STEPS = 20;

/** The longest wait between two steps: 30 days. */
export const MAX_WAIT_MINUTES = 43_200;

/** A step before the final 100 %: its percent and the wait before the next step, as typed. */
export type StepDraft = {
  id: string;
  percent: string;
  wait: string;
  unit: WaitUnit;
};

/** A rollout being planned; the final 100 % step is implicit. */
export type RolloutPlan = { from: string; to: string; steps: StepDraft[] };

/** The variation the default rule serves now: its variation, else the largest weight, else the first. */
export function defaultFrom(
  fallthrough: S["Serve"],
  variations: string[],
): string {
  if (fallthrough.variation) return fallthrough.variation;
  const weights = fallthrough.rollout?.weights ?? [];
  if (weights.length > 0) {
    return weights.reduce((best, w) => (w.weight > best.weight ? w : best))
      .variation;
  }
  return variations[0] ?? "";
}

function step(percent: string): StepDraft {
  return { id: newId(), percent, wait: "1", unit: "hours" };
}

/** A plan from `from` to the first other variation in 10, 25 and 50 % steps an hour apart. */
export function defaultPlan(from: string, variations: string[]): RolloutPlan {
  return {
    from,
    to: variations.find((v) => v !== from) ?? "",
    steps: ["10", "25", "50"].map(step),
  };
}

export function addStep(p: RolloutPlan): RolloutPlan {
  return { ...p, steps: [...p.steps, step("")] };
}

export function removeStep(p: RolloutPlan, id: string): RolloutPlan {
  return { ...p, steps: p.steps.filter((s) => s.id !== id) };
}

export function updateStep(
  p: RolloutPlan,
  id: string,
  patch: Partial<Omit<StepDraft, "id">>,
): RolloutPlan {
  return {
    ...p,
    steps: p.steps.map((s) => (s.id === id ? { ...s, ...patch } : s)),
  };
}

/** A typed percent as a weight, or null when it isn't above 0 and below 100. */
function stepWeight(percent: string): number | null {
  if (percent.trim() === "") return null;
  const n = Number(percent);
  if (!Number.isFinite(n)) return null;
  const weight = fromPercent(n);
  return n > 0 && n < 100 && weight > 0 && weight < WEIGHT_TOTAL
    ? weight
    : null;
}

/** What stops the plan from being started, with the field each problem belongs to. */
export function planProblems(
  p: RolloutPlan,
): { path: string; message: string }[] {
  const problems: { path: string; message: string }[] = [];
  if (p.from === p.to) {
    problems.push({
      path: "to",
      message: "Choose a variation other than From",
    });
  }
  if (p.steps.length + 1 > MAX_STEPS) {
    problems.push({ path: "steps", message: `At most ${MAX_STEPS} steps` });
  }
  let previous = 0;
  p.steps.forEach((s, i) => {
    const weight = stepWeight(s.percent);
    if (weight === null) {
      problems.push({
        path: `steps[${i}].percent`,
        message: "Enter a percentage above 0 and below 100",
      });
    } else if (weight <= previous) {
      problems.push({
        path: `steps[${i}].percent`,
        message: "Each step must be higher than the one before",
      });
    } else {
      previous = weight;
    }
    if (!/^\d+$/.test(s.wait.trim())) {
      problems.push({
        path: `steps[${i}].wait`,
        message: "Enter a whole number",
      });
    } else {
      const minutes = waitMinutes(s);
      if (minutes < 1 || minutes > MAX_WAIT_MINUTES) {
        problems.push({
          path: `steps[${i}].wait`,
          message: "Wait between 1 minute and 30 days",
        });
      }
    }
  });
  return problems;
}

/** The wait after a step, in minutes. */
export function waitMinutes(s: StepDraft): number {
  return Number(s.wait) * UNIT_MINUTES[s.unit];
}

/**
 * The plan as the API takes it, ending at 100 %. The API ignores the last step's wait,
 * so it carries the API's default.
 */
export function toRolloutIn(p: RolloutPlan): S["ProgressiveRolloutIn"] {
  return {
    from_variation: p.from,
    to_variation: p.to,
    steps: [
      ...p.steps.map((s) => ({
        percentage: fromPercent(Number(s.percent)),
        wait_minutes: waitMinutes(s),
      })),
      { percentage: WEIGHT_TOTAL, wait_minutes: 60 },
    ],
  };
}

/** How long the plan takes from its first step to 100 %, in minutes. */
export function totalMinutes(p: RolloutPlan): number {
  return p.steps.reduce((sum, s) => sum + waitMinutes(s), 0);
}

function count(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

/** A duration in minutes as days, hours and minutes: 1500 is "1 day 1 hour". */
export function durationText(minutes: number): string {
  if (minutes <= 0) return "now";
  const days = Math.floor(minutes / UNIT_MINUTES.days);
  const hours = Math.floor((minutes % UNIT_MINUTES.days) / UNIT_MINUTES.hours);
  const rest = minutes % UNIT_MINUTES.hours;
  return [
    days > 0 ? count(days, "day") : null,
    hours > 0 ? count(hours, "hour") : null,
    rest > 0 ? count(rest, "minute") : null,
  ]
    .filter((part) => part !== null)
    .join(" ");
}

/** The percent the rollout serves `to_variation` now. */
export function currentPercent(r: S["ProgressiveRolloutOut"]): number {
  if (r.status === "completed") return 100;
  return toPercent(r.steps[r.current_step]?.percentage ?? 0);
}

export type StepState = {
  percent: number;
  state: "done" | "current" | "next";
  waitMinutes: number;
};

/** Each step of a rollout with whether it is done, current or still to come. */
export function stepStates(r: S["ProgressiveRolloutOut"]): StepState[] {
  return r.steps.map((s, i) => ({
    percent: toPercent(s.percentage),
    state:
      r.status === "completed" || i < r.current_step
        ? "done"
        : i === r.current_step
          ? "current"
          : "next",
    waitMinutes: s.wait_minutes,
  }));
}

const PILLS: Record<S["RolloutStatus"], { status: Status; label: string }> = {
  active: { status: "pending", label: "Running" },
  paused: { status: "stale", label: "Paused" },
  completed: { status: "on", label: "Completed" },
  aborted: { status: "off", label: "Aborted" },
  failed: { status: "error", label: "Failed" },
  cancelled: { status: "off", label: "Cancelled" },
};

export function rolloutPill(status: S["RolloutStatus"]): {
  status: Status;
  label: string;
} {
  return PILLS[status];
}

/** A rollout's variation, which is null once the variation is deleted. */
export function variationText(key: string | null): string {
  return key ?? "deleted variation";
}
