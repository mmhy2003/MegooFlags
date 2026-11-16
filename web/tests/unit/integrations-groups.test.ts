import { describe, expect, it } from "vitest";

import {
  EVENT_GROUPS,
  groupLabel,
  KIND_LABELS,
} from "@/lib/integrations/groups";

describe("EVENT_GROUPS", () => {
  it("lists the four groups in order", () => {
    expect(EVENT_GROUPS.map((g) => [g.id, g.label])).toEqual([
      ["flags", "Flags"],
      ["targeting", "Targeting and state"],
      ["segments", "Segments"],
      ["change_requests", "Change requests"],
    ]);
  });

  it("describes every group", () => {
    for (const g of EVENT_GROUPS) expect(g.description).not.toBe("");
  });
});

describe("KIND_LABELS", () => {
  it.each([
    ["webhook", "Webhook"],
    ["slack", "Slack"],
    ["teams", "Microsoft Teams"],
  ] as const)("labels %s as %s", (kind, label) => {
    expect(KIND_LABELS[kind]).toBe(label);
  });
});

describe("groupLabel", () => {
  it.each([
    ["flags", "Flags"],
    ["targeting", "Targeting and state"],
    ["segments", "Segments"],
    ["change_requests", "Change requests"],
  ] as const)("labels %s as %s", (id, label) => {
    expect(groupLabel(id)).toBe(label);
  });
});
