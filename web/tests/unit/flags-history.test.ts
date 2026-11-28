import { describe, expect, it } from "vitest";

import { actionLabel, entryText } from "@/lib/flags/history";

describe("actionLabel", () => {
  it.each([
    ["flag.create", "Created"],
    ["flag.update", "Details edited"],
    ["flag.config.update", "Targeting changed"],
    ["flag.kill", "Kill switch used"],
    ["flag.copy", "Config copied here"],
    ["flag.archive", "Archived"],
    ["flag.restore", "Restored"],
    ["flag.lifecycle.update", "Lifecycle mark changed"],
    ["flag.schedule.create", "Scheduled change: create"],
    ["flag.rollout.pause", "Progressive rollout: pause"],
    ["segment.weird", "segment.weird"],
  ])("labels %s as %s", (action, label) => {
    expect(actionLabel({ action, after: null })).toBe(label);
  });

  it("reads a toggle's direction from after.enabled", () => {
    expect(
      actionLabel({ action: "flag.toggle", after: { enabled: true } }),
    ).toBe("Turned on");
    expect(
      actionLabel({ action: "flag.toggle", after: { enabled: false } }),
    ).toBe("Turned off");
  });
});

describe("entryText", () => {
  it("is empty for null", () => {
    expect(entryText(null)).toBe("");
  });

  it("sorts keys at every depth", () => {
    expect(entryText({ b: 1, a: { d: [{ z: 1, y: 2 }], c: null } })).toBe(
      JSON.stringify({ a: { c: null, d: [{ y: 2, z: 1 }] }, b: 1 }, null, 2),
    );
  });
});
