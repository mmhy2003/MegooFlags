import type { Status } from "@/components/patterns/status-pill";
import type { components } from "@/lib/api/schema";

type User = components["schemas"]["UserOut"];

export type MemberState = {
  kind: "active" | "invited" | "invite-expired" | "disabled";
  label: string;
};

/** Disabled wins; then an invitation, pending or expired; else active. */
export function memberState(u: User, now: Date): MemberState {
  if (u.status === "disabled") return { kind: "disabled", label: "Disabled" };
  const expires = u.invitation_expires_at;
  if (expires !== null && expires !== undefined) {
    return new Date(expires).getTime() > now.getTime()
      ? { kind: "invited", label: "Invited" }
      : { kind: "invite-expired", label: "Invitation expired" };
  }
  return { kind: "active", label: "Active" };
}

const PILLS: Record<MemberState["kind"], Status> = {
  active: "on",
  invited: "pending",
  "invite-expired": "stale",
  disabled: "off",
};

export function memberPill(s: MemberState): { status: Status; label: string } {
  return { status: PILLS[s.kind], label: s.label };
}

/** Someone who never signed in and has no password gets an invite link. */
export function linkKind(u: User): "invite" | "reset" {
  return !u.has_password && u.last_login_at === null ? "invite" : "reset";
}
