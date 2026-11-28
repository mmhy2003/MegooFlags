import type { components } from "@/lib/api/client";
import type { Status } from "@/components/patterns/status-pill";

type S = components["schemas"];

export type NodeStatus = S["NodeStatus"];

const PILLS: Record<NodeStatus, { status: Status; label: string }> = {
  healthy: { status: "on", label: "Healthy" },
  stale: { status: "stale", label: "Stale" },
  pending: { status: "pending", label: "Pending" },
  draining: { status: "off", label: "Draining" },
  incompatible: { status: "error", label: "Incompatible" },
  revoked: { status: "killed", label: "Revoked" },
};

export function nodePill(s: NodeStatus): { status: Status; label: string } {
  return PILLS[s];
}

export type NodeActions = {
  drain: boolean;
  resume: boolean;
  rotate: boolean;
  revoke: boolean;
  remove: boolean;
};

/** A revoked node can only be removed; the server refuses everything else. */
export function nodeActions(node: S["NodeOut"]): NodeActions {
  const revoked = node.status === "revoked";
  const draining = node.status === "draining";
  return {
    drain: !draining && !revoked,
    resume: draining,
    rotate: !revoked,
    revoke: !revoked,
    remove: revoked,
  };
}

const ATTENTION: NodeStatus[] = ["stale", "incompatible", "revoked"];

/** The counts on the summary cards. */
export function summary(nodes: S["NodeOut"][]): {
  workers: number;
  healthy: number;
  attention: number;
} {
  return {
    workers: nodes.length,
    healthy: nodes.filter((n) => n.status === "healthy").length,
    attention: nodes.filter((n) => ATTENTION.includes(n.status)).length,
  };
}
