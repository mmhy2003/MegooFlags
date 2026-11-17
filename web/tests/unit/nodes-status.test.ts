import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/client";
import { nodeActions, nodePill, summary } from "@/lib/nodes/status";

type S = components["schemas"];

const node: S["NodeOut"] = {
  id: "00000000-0000-0000-0000-000000000001",
  name: "worker-1",
  status: "healthy",
  syncing: false,
  created_at: "2026-11-01T00:00:00Z",
  last_heartbeat_at: "2026-11-01T00:00:00Z",
  synced_at: "2026-11-01T00:00:00Z",
  version: "0.3.0",
  snapshot_version: 42,
  sdk_streams: 3,
  buffered_events: 0,
  address: "10.0.0.2",
  refused_version: null,
  token_prefix: "mf_node_Ab12",
};

describe("nodePill", () => {
  it.each([
    ["healthy", { status: "on", label: "Healthy" }],
    ["stale", { status: "stale", label: "Stale" }],
    ["pending", { status: "pending", label: "Pending" }],
    ["draining", { status: "off", label: "Draining" }],
    ["incompatible", { status: "error", label: "Incompatible" }],
    ["revoked", { status: "killed", label: "Revoked" }],
  ] as const)("maps %s", (status, pill) => {
    expect(nodePill(status)).toEqual(pill);
  });
});

describe("nodeActions", () => {
  it.each([
    [
      "healthy",
      { drain: true, resume: false, rotate: true, revoke: true, remove: false },
    ],
    [
      "stale",
      { drain: true, resume: false, rotate: true, revoke: true, remove: false },
    ],
    [
      "pending",
      { drain: true, resume: false, rotate: true, revoke: true, remove: false },
    ],
    [
      "incompatible",
      { drain: true, resume: false, rotate: true, revoke: true, remove: false },
    ],
    [
      "draining",
      { drain: false, resume: true, rotate: true, revoke: true, remove: false },
    ],
    [
      "revoked",
      {
        drain: false,
        resume: false,
        rotate: false,
        revoke: false,
        remove: true,
      },
    ],
  ] as const)("allows the right actions when %s", (status, actions) => {
    expect(nodeActions({ ...node, status })).toEqual(actions);
  });
});

describe("summary", () => {
  it("counts workers, healthy ones and those that need attention", () => {
    const statuses: S["NodeStatus"][] = [
      "healthy",
      "healthy",
      "stale",
      "incompatible",
      "revoked",
      "pending",
      "draining",
    ];
    expect(summary(statuses.map((status) => ({ ...node, status })))).toEqual({
      workers: 7,
      healthy: 2,
      attention: 3,
    });
  });

  it("is all zeros for no workers", () => {
    expect(summary([])).toEqual({ workers: 0, healthy: 0, attention: 0 });
  });
});
