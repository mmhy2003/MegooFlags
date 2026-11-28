import type { components } from "@/lib/api/schema";
import { toPercent } from "@/lib/flags/weights";

type S = components["schemas"];
type Kind = S["ChangeRequestKind"];

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function defaultFormatDate(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

/** A percent of thousandths: 50000 → "50%". */
function percentText(thousandths: unknown): string {
  return `${toPercent(Number(thousandths))}%`;
}

/** "a 50%, b 50%". */
function weightsText(weights: unknown): string {
  return records(weights)
    .map((w) => `${String(w.variation)} ${percentText(w.weight)}`)
    .join(", ");
}

/** What a change request's payload would do, in one sentence. */
export function proposedSummary(
  kind: Kind,
  payload: Record<string, unknown>,
  formatDate: (iso: string) => string = defaultFormatDate,
): string {
  switch (kind) {
    case "toggle":
      return payload.enabled ? "Turn the flag on" : "Turn the flag off";
    case "schedule": {
      const at = formatDate(String(payload.run_at));
      if (payload.action === "enable") return `Turn on at ${at}`;
      if (payload.action === "disable") return `Turn off at ${at}`;
      return `Change the rollout at ${at} to ${weightsText(payload.weights)}`;
    }
    case "rollout": {
      const steps = records(payload.steps);
      return (
        `Roll out from ${String(payload.from_variation)} to ${String(payload.to_variation)} ` +
        `in ${steps.length} steps: ${steps.map((s) => percentText(s.percentage)).join(", ")}`
      );
    }
    case "config":
      return "Change the targeting";
  }
}

/** The flag's state in the environment that the change request would change. */
export function currentSummary(kind: Kind, config: S["FlagConfigOut"]): string {
  const state = config.enabled ? "Currently on" : "Currently off";
  if (kind !== "rollout" && kind !== "schedule") return state;
  const { variation, rollout } = config.fallthrough;
  if (rollout) return `${state}, serving ${weightsText(rollout.weights)}`;
  if (variation) return `${state}, serving ${variation}`;
  return state;
}
