import type { Status } from "@/components/patterns/status-pill";

/** What SDK keys and API tokens share: an optional expiry and revocation. */
export type Credential = {
  expires_at: string | null;
  revoked_at: string | null;
};

export type CredentialState = {
  kind: "active" | "expiring" | "expired" | "revoked";
  label: string;
};

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const relative = new Intl.RelativeTimeFormat("en", { numeric: "always" });

/** "in 3 hours": the largest whole unit of minutes, hours or days. */
function untilText(ms: number): string {
  if (ms >= DAY) return relative.format(Math.floor(ms / DAY), "day");
  if (ms >= HOUR) return relative.format(Math.floor(ms / HOUR), "hour");
  return relative.format(Math.max(1, Math.floor(ms / MINUTE)), "minute");
}

/** Revoked wins over expired, and expired over a coming expiry. */
export function credentialState(c: Credential, now: Date): CredentialState {
  if (c.revoked_at !== null) return { kind: "revoked", label: "Revoked" };
  if (c.expires_at === null) return { kind: "active", label: "Active" };
  const left = new Date(c.expires_at).getTime() - now.getTime();
  if (left <= 0) return { kind: "expired", label: "Expired" };
  return { kind: "expiring", label: `Expires ${untilText(left)}` };
}

const PILLS: Record<CredentialState["kind"], Status> = {
  active: "on",
  expiring: "pending",
  expired: "off",
  revoked: "error",
};

export function credentialPill(s: CredentialState): {
  status: Status;
  label: string;
} {
  return { status: PILLS[s.kind], label: s.label };
}

/** A revoked or expired credential no longer works. */
export function isInactive(c: Credential, now: Date): boolean {
  const { kind } = credentialState(c, now);
  return kind === "revoked" || kind === "expired";
}

/** The rows to list: inactive ones only when the person asks for them. */
export function visibleCredentials<T extends Credential>(
  rows: T[],
  showInactive: boolean,
  now: Date,
): T[] {
  return showInactive ? rows : rows.filter((r) => !isInactive(r, now));
}

/** The name of each SDK key kind and what it is for. */
export const SDK_KIND_TEXT: Record<
  "server" | "client",
  { label: string; help: string }
> = {
  server: {
    label: "Server",
    help: "For backend SDKs. Keep it secret: it downloads every flag's rules.",
  },
  client: {
    label: "Client",
    help: "For browser and mobile apps. It can only evaluate flags, so it can ship in an app.",
  },
};
