export type ExpiryChoice = "never" | "30d" | "90d" | "1y" | "date";
/** `date` is "YYYY-MM-DD" and only counts for the "date" choice. */
export type ExpiryValue = { choice: ExpiryChoice; date: string };

export const EXPIRY_CHOICES: { value: ExpiryChoice; label: string }[] = [
  { value: "never", label: "Never" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "1y", label: "1 year" },
  { value: "date", label: "On a date" },
];

export const PERSONAL_EXPIRY_DEFAULT: ExpiryValue = { choice: "90d", date: "" };
export const SERVICE_EXPIRY_DEFAULT: ExpiryValue = {
  choice: "never",
  date: "",
};

const DAY = 86_400_000;

const PERIOD_DAYS: Record<"30d" | "90d" | "1y", number> = {
  "30d": 30,
  "90d": 90,
  "1y": 365,
};

/** The last moment (23:59:59.999 local) of a "YYYY-MM-DD" day. */
function endOfDay(date: string): Date {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(y, m - 1, d, 23, 59, 59, 999);
}

/** The ISO expiry to send, or null for a token that never expires. */
export function expiresAt(v: ExpiryValue, now: Date): string | null {
  if (v.choice === "never") return null;
  if (v.choice === "date") return endOfDay(v.date).toISOString();
  return new Date(now.getTime() + PERIOD_DAYS[v.choice] * DAY).toISOString();
}

export function expiryError(v: ExpiryValue, now: Date): string | undefined {
  if (v.choice !== "date") return undefined;
  if (v.date === "") return "Pick a date";
  if (endOfDay(v.date).getTime() <= now.getTime()) {
    return "Pick a date in the future";
  }
  return undefined;
}

/** How long the old SDK key keeps working after a rotation. */
export const OVERLAP_CHOICES: { seconds: number; label: string }[] = [
  { seconds: 0, label: "Revoke the old one now" },
  { seconds: 3600, label: "1 hour" },
  { seconds: 86400, label: "24 hours" },
  { seconds: 604800, label: "7 days" },
  { seconds: 2592000, label: "30 days" },
];

export const DEFAULT_OVERLAP = 86400;
