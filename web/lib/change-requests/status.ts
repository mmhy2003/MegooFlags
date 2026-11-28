import type { Status } from "@/components/patterns/status-pill";
import type { components } from "@/lib/api/schema";

type CR = components["schemas"]["ChangeRequestSummary"];

/** What a change request changes, in words. */
export const KIND_LABELS: Record<CR["kind"], string> = {
  config: "Targeting",
  toggle: "On/off",
  schedule: "Schedule",
  rollout: "Rollout",
};

const PILLS: Record<CR["status"], { status: Status; label: string }> = {
  open: { status: "pending", label: "Open" },
  applied: { status: "on", label: "Applied" },
  rejected: { status: "killed", label: "Rejected" },
  cancelled: { status: "off", label: "Cancelled" },
  failed: { status: "error", label: "Failed" },
};

/** The status pill of a change request. */
export function crPill(status: CR["status"]): {
  status: Status;
  label: string;
} {
  return PILLS[status];
}

/** "1 of 2 approvals". */
export function approvalsText(
  cr: Pick<CR, "approvals" | "approvals_required">,
): string {
  return `${cr.approvals} of ${cr.approvals_required} approvals`;
}

/** Whether one more approval is enough, so approving also applies the change. */
export function isLastApproval(
  cr: Pick<CR, "approvals" | "approvals_required">,
): boolean {
  return cr.approvals + 1 >= cr.approvals_required;
}

/** Whether an open request was written against an older version of the flag's config. */
export function isStale(
  cr: { status: CR["status"]; base_version: number },
  currentVersion: number | null,
): boolean {
  return (
    cr.status === "open" &&
    currentVersion !== null &&
    currentVersion !== cr.base_version
  );
}
