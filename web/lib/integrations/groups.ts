import type { components } from "@/lib/api/client";

type S = components["schemas"];

/** The event groups an integration can subscribe to, in the order the form shows them. */
export const EVENT_GROUPS: {
  id: S["EventGroup"];
  label: string;
  description: string;
}[] = [
  {
    id: "flags",
    label: "Flags",
    description:
      "Flags created, updated, archived, restored or deleted, variation and lifecycle changes",
  },
  {
    id: "targeting",
    label: "Targeting and state",
    description:
      "Flags turned on or off, killed or copied, targeting changes, scheduled changes and rollouts",
  },
  {
    id: "segments",
    label: "Segments",
    description: "Segments created, updated or deleted",
  },
  {
    id: "change_requests",
    label: "Change requests",
    description:
      "Change requests opened, approved, rejected, applied, failed or cancelled",
  },
];

export const KIND_LABELS: Record<S["IntegrationKind"], string> = {
  webhook: "Webhook",
  slack: "Slack",
  teams: "Microsoft Teams",
};

export function groupLabel(id: S["EventGroup"]): string {
  return EVENT_GROUPS.find((g) => g.id === id)?.label ?? id;
}
