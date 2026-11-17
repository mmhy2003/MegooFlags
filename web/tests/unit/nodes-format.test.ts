import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/client";
import { envLines, relativeTime, snapshotText } from "@/lib/nodes/format";

type S = components["schemas"];

const NOW = Date.parse("2026-11-17T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

const node: S["NodeOut"] = {
  id: "00000000-0000-0000-0000-000000000001",
  name: "worker-1",
  status: "healthy",
  syncing: false,
  created_at: "2026-11-01T00:00:00Z",
  last_heartbeat_at: ago(5),
  synced_at: ago(5),
  version: "0.3.0",
  snapshot_version: 42,
  sdk_streams: 3,
  buffered_events: 0,
  address: "10.0.0.2",
  refused_version: null,
  token_prefix: "mf_node_Ab12",
};

describe("relativeTime", () => {
  it.each([
    [0, "just now"],
    [9, "just now"],
    [12, "12 s ago"],
    [59, "59 s ago"],
    [3 * 60 + 20, "3 min ago"],
    [2 * 3600 + 59 * 60, "2 h ago"],
    [4 * 86400 + 3600, "4 d ago"],
  ])("%i s ago is %s", (seconds, text) => {
    expect(relativeTime(ago(seconds), NOW)).toBe(text);
  });

  it("says Never for null", () => {
    expect(relativeTime(null, NOW)).toBe("Never");
  });

  it("says just now for a time slightly in the future", () => {
    expect(relativeTime(ago(-3), NOW)).toBe("just now");
  });
});

describe("snapshotText", () => {
  it("marks the current version", () => {
    expect(snapshotText(node, 42, NOW)).toBe("v42 · current");
  });

  it("marks a healthy worker that is catching up", () => {
    expect(
      snapshotText({ ...node, snapshot_version: 40, syncing: true }, 42, NOW),
    ).toBe("v40 · syncing");
  });

  it("gives the age of a stale worker's snapshot", () => {
    expect(
      snapshotText(
        {
          ...node,
          status: "stale",
          snapshot_version: 40,
          synced_at: ago(3 * 60 + 5),
        },
        42,
        NOW,
      ),
    ).toBe("v40 · 3 min old");
  });

  it("shows a dash for a pending worker", () => {
    expect(
      snapshotText(
        {
          ...node,
          status: "pending",
          snapshot_version: null,
          synced_at: null,
          last_heartbeat_at: null,
        },
        42,
        NOW,
      ),
    ).toBe("—");
  });
});

describe("envLines", () => {
  it("gives the three worker settings", () => {
    expect(envLines("https://flags.example.com", "mf_node_abc")).toBe(
      [
        "MEGOOFLAGS_ROLE=worker",
        "MEGOOFLAGS_CONTROL_PLANE_URL=https://flags.example.com",
        "MEGOOFLAGS_NODE_TOKEN=mf_node_abc",
      ].join("\n"),
    );
  });
});
