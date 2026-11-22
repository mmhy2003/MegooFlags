import { describe, expect, it } from "vitest";

import {
  KIND_LABELS,
  approvalsText,
  crPill,
  isLastApproval,
  isStale,
} from "@/lib/change-requests/status";

describe("crPill", () => {
  it.each([
    ["open", "pending", "Open"],
    ["applied", "on", "Applied"],
    ["rejected", "killed", "Rejected"],
    ["cancelled", "off", "Cancelled"],
    ["failed", "error", "Failed"],
  ] as const)("shows %s as %s", (status, pill, label) => {
    expect(crPill(status)).toEqual({ status: pill, label });
  });
});

describe("KIND_LABELS", () => {
  it("names every kind", () => {
    expect(KIND_LABELS).toEqual({
      config: "Targeting",
      toggle: "On/off",
      schedule: "Schedule",
      rollout: "Rollout",
    });
  });
});

describe("approvalsText", () => {
  it("counts approvals against the requirement", () => {
    expect(approvalsText({ approvals: 1, approvals_required: 2 })).toBe(
      "1 of 2 approvals",
    );
  });
});

describe("isLastApproval", () => {
  it("is true when one more approval meets the requirement", () => {
    expect(isLastApproval({ approvals: 1, approvals_required: 2 })).toBe(true);
  });

  it("is false when more approvals are still needed", () => {
    expect(isLastApproval({ approvals: 0, approvals_required: 2 })).toBe(false);
  });
});

describe("isStale", () => {
  it("is true for an open request when the flag moved on", () => {
    expect(isStale({ status: "open", base_version: 4 }, 5)).toBe(true);
  });

  it("is false when the versions match", () => {
    expect(isStale({ status: "open", base_version: 4 }, 4)).toBe(false);
  });

  it("is false once the request is closed", () => {
    expect(isStale({ status: "applied", base_version: 4 }, 5)).toBe(false);
  });

  it("is false without a current version", () => {
    expect(isStale({ status: "open", base_version: 4 }, null)).toBe(false);
  });
});
