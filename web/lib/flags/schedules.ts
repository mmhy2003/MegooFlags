import type { Status } from "@/components/patterns/status-pill";
import type { components } from "@/lib/api/schema";
import { toPercent } from "@/lib/flags/weights";

type S = components["schemas"];
type Schedule = S["ScheduleOut"];

/** One environment's rollout and schedules, each null until it has loaded. */
export type AutomationSnapshot = {
  rollout: S["RolloutView"] | null;
  schedules: Schedule[] | null;
};

export const SCHEDULE_ACTIONS: {
  value: S["ScheduledAction"];
  label: string;
}[] = [
  { value: "enable", label: "Turn on" },
  { value: "disable", label: "Turn off" },
  { value: "set_rollout", label: "Set the default rule to a rollout" },
];

/** "Turn on", or "Set the default rule to true 25 %, false 75 %" for a rollout. */
export function scheduleText(s: Pick<Schedule, "action" | "weights">): string {
  if (s.action === "set_rollout") {
    const weights = (s.weights ?? [])
      .map((w) => `${w.variation} ${toPercent(w.weight)} %`)
      .join(", ");
    return `Set the default rule to ${weights}`;
  }
  return SCHEDULE_ACTIONS.find((a) => a.value === s.action)?.label ?? s.action;
}

/** The permission needed to schedule, or cancel, a change of this kind. */
export function schedulePermission(
  action: S["ScheduledAction"],
): S["Permission"] {
  return action === "set_rollout" ? "flag.config.write" : "flag.toggle";
}

function time(iso: string): number {
  return new Date(iso).getTime();
}

/** Pending schedules, soonest first. */
export function upcoming(list: Schedule[]): Schedule[] {
  return list
    .filter((s) => s.status === "pending")
    .sort((a, b) => time(a.run_at) - time(b.run_at));
}

/** Applied, failed and cancelled schedules, newest first. */
export function past(list: Schedule[]): Schedule[] {
  const when = (s: Schedule) => time(s.executed_at ?? s.run_at);
  return list
    .filter((s) => s.status !== "pending")
    .sort((a, b) => when(b) - when(a));
}

const PILLS: Record<S["ScheduleStatus"], { status: Status; label: string }> = {
  pending: { status: "pending", label: "Pending" },
  done: { status: "on", label: "Applied" },
  failed: { status: "error", label: "Failed" },
  cancelled: { status: "off", label: "Cancelled" },
};

export function schedulePill(status: S["ScheduleStatus"]): {
  status: Status;
  label: string;
} {
  return PILLS[status];
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const MAX_AHEAD = 365 * DAY;
const STEP = 5 * MINUTE;

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** A `datetime-local` value an hour from `now`, rounded up to 5 minutes. */
export function defaultRunAt(now: Date): string {
  const at = new Date(Math.ceil((now.getTime() + HOUR) / STEP) * STEP);
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
    `T${pad(at.getHours())}:${pad(at.getMinutes())}`
  );
}

/** A `datetime-local` value as an ISO time; null when it is empty or invalid. */
export function localToIso(local: string): string | null {
  if (local === "") return null;
  const at = new Date(local);
  return Number.isNaN(at.getTime()) ? null : at.toISOString();
}

/** What is wrong with a run time, as the API checks it; undefined when it is fine. */
export function runAtProblem(local: string, now: Date): string | undefined {
  const iso = localToIso(local);
  if (iso === null) return "Pick a date and time";
  const ahead = time(iso) - now.getTime();
  if (ahead <= 0) return "Pick a time in the future";
  if (ahead > MAX_AHEAD) return "Pick a time within 365 days";
  return undefined;
}

/** "2026-12-01 08:30 UTC". */
export function utcText(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

const relative = new Intl.RelativeTimeFormat("en", { numeric: "always" });

/** "in 3 hours" or "2 days ago": the largest unit of days, hours or minutes that fits. */
export function relativeText(iso: string, now: Date): string {
  const ms = time(iso) - now.getTime();
  const sign = Math.sign(ms) || 1;
  const abs = Math.abs(ms);
  if (abs >= DAY) return relative.format(sign * Math.round(abs / DAY), "day");
  if (abs >= HOUR)
    return relative.format(sign * Math.round(abs / HOUR), "hour");
  return relative.format(
    sign * Math.max(1, Math.round(abs / MINUTE)),
    "minute",
  );
}

function isLive(r: S["ProgressiveRolloutOut"] | null | undefined): boolean {
  return r?.status === "active" || r?.status === "paused";
}

function pendingCount(a: AutomationSnapshot): number {
  return (a.schedules ?? []).filter((s) => s.status === "pending").length;
}

/** The Automation tab's count: pending schedules plus one for a live rollout. */
export function automationCount(a: AutomationSnapshot): number {
  return pendingCount(a) + (isLive(a.rollout?.current) ? 1 : 0);
}

/** Whether to reload: a rollout is running or a schedule is pending. */
export function needsPolling(a: AutomationSnapshot): boolean {
  return a.rollout?.current?.status === "active" || pendingCount(a) > 0;
}

/** True when something changed the flag between two loads: a schedule ran, a rollout step ran or a rollout ended. */
export function wasApplied(
  before: AutomationSnapshot,
  after: AutomationSnapshot,
): boolean {
  if (before.schedules !== null && after.schedules !== null) {
    const done = new Set(
      after.schedules.filter((s) => s.status === "done").map((s) => s.id),
    );
    if (before.schedules.some((s) => s.status === "pending" && done.has(s.id)))
      return true;
  }
  const was = before.rollout?.current;
  if (was && after.rollout !== null) {
    const now = after.rollout.current;
    if (now?.id === was.id && now.current_step > was.current_step) return true;
    if (isLive(was) && !(now?.id === was.id && isLive(now))) return true;
  }
  return false;
}
