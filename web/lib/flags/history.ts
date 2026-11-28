const LABELS: Record<string, string> = {
  "flag.create": "Created",
  "flag.update": "Details edited",
  "flag.config.update": "Targeting changed",
  "flag.kill": "Kill switch used",
  "flag.copy": "Config copied here",
  "flag.archive": "Archived",
  "flag.restore": "Restored",
  "flag.lifecycle.update": "Lifecycle mark changed",
};

/** A readable label for an audit action; unknown actions are shown as they are. */
export function actionLabel(entry: {
  action: string;
  after: Record<string, unknown> | null;
}): string {
  const { action, after } = entry;
  if (action === "flag.toggle") {
    return after?.enabled ? "Turned on" : "Turned off";
  }
  if (action.startsWith("flag.schedule.")) {
    return `Scheduled change: ${action.slice("flag.schedule.".length)}`;
  }
  if (action.startsWith("flag.rollout.")) {
    return `Progressive rollout: ${action.slice("flag.rollout.".length)}`;
  }
  return LABELS[action] ?? action;
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, sortKeys((value as Record<string, unknown>)[k])]),
    );
  }
  return value;
}

/** An audit before/after value as stable JSON for a diff; "" for none. */
export function entryText(value: Record<string, unknown> | null): string {
  if (value === null) return "";
  return JSON.stringify(sortKeys(value), null, 2);
}
