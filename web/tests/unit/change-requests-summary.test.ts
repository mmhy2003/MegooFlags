import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { currentSummary, proposedSummary } from "@/lib/change-requests/summary";

const formatDate = (iso: string) => iso.slice(0, 10);
const runAt = "2026-12-01T09:00:00Z";
const half = [
  { variation: "a", weight: 50000 },
  { variation: "b", weight: 50000 },
];

function config(
  enabled: boolean,
  fallthrough: components["schemas"]["Serve"],
): components["schemas"]["FlagConfigOut"] {
  return {
    enabled,
    environment: "production",
    fallthrough,
    prerequisites: [],
    rules: [],
    targets: [],
    updated_at: "2026-11-22T12:00:00Z",
    version: 3,
  };
}

describe("proposedSummary", () => {
  it("describes a toggle", () => {
    expect(proposedSummary("toggle", { enabled: true })).toBe(
      "Turn the flag on",
    );
    expect(proposedSummary("toggle", { enabled: false })).toBe(
      "Turn the flag off",
    );
  });

  it("describes scheduled on and off", () => {
    expect(
      proposedSummary(
        "schedule",
        { action: "enable", run_at: runAt },
        formatDate,
      ),
    ).toBe("Turn on at 2026-12-01");
    expect(
      proposedSummary(
        "schedule",
        { action: "disable", run_at: runAt },
        formatDate,
      ),
    ).toBe("Turn off at 2026-12-01");
  });

  it("describes a scheduled rollout with its weights", () => {
    expect(
      proposedSummary(
        "schedule",
        { action: "set_rollout", run_at: runAt, weights: half },
        formatDate,
      ),
    ).toBe("Change the rollout at 2026-12-01 to a 50%, b 50%");
  });

  it("describes a progressive rollout with its steps", () => {
    expect(
      proposedSummary("rollout", {
        from_variation: "a",
        to_variation: "b",
        steps: [
          { percentage: 10000, wait_minutes: 60 },
          { percentage: 50000, wait_minutes: 60 },
          { percentage: 100000, wait_minutes: 60 },
        ],
      }),
    ).toBe("Roll out from a to b in 3 steps: 10%, 50%, 100%");
  });
});

describe("currentSummary", () => {
  it("says whether the flag is on for a toggle", () => {
    expect(currentSummary("toggle", config(true, { variation: "b" }))).toBe(
      "Currently on",
    );
    expect(currentSummary("toggle", config(false, { variation: "b" }))).toBe(
      "Currently off",
    );
  });

  it("adds the fallthrough variation for a rollout", () => {
    expect(currentSummary("rollout", config(true, { variation: "b" }))).toBe(
      "Currently on, serving b",
    );
  });

  it("adds the fallthrough rollout for a schedule", () => {
    expect(
      currentSummary(
        "schedule",
        config(false, { rollout: { bucket_by: "key", weights: half } }),
      ),
    ).toBe("Currently off, serving a 50%, b 50%");
  });
});
