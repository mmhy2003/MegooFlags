import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import {
  addStep,
  currentPercent,
  defaultFrom,
  defaultPlan,
  durationText,
  MAX_STEPS,
  MAX_WAIT_MINUTES,
  planProblems,
  removeStep,
  rolloutPill,
  type RolloutPlan,
  stepStates,
  toRolloutIn,
  totalMinutes,
  UNIT_MINUTES,
  updateStep,
  variationText,
  WAIT_UNITS,
  waitMinutes,
} from "@/lib/flags/rollout-plan";

type Rollout = components["schemas"]["ProgressiveRolloutOut"];

function rollout(patch: Partial<Rollout> = {}): Rollout {
  return {
    id: "r1",
    environment: "development",
    from_variation: "false",
    to_variation: "true",
    status: "active",
    current_step: 1,
    steps: [
      { percentage: 10_000, wait_minutes: 60 },
      { percentage: 25_000, wait_minutes: 120 },
      { percentage: 100_000, wait_minutes: 60 },
    ],
    created_at: "2026-11-24T10:00:00Z",
    created_by: "ada@example.com",
    updated_at: "2026-11-24T11:00:00Z",
    error: null,
    finished_at: null,
    last_applied_weights: null,
    next_step_at: "2026-11-24T13:00:00Z",
    remaining_seconds: null,
    ...patch,
  };
}

function plan(
  steps: [string, string, "minutes" | "hours" | "days"][],
): RolloutPlan {
  return {
    from: "false",
    to: "true",
    steps: steps.map(([percent, wait, unit], i) => ({
      id: `s${i}`,
      percent,
      wait,
      unit,
    })),
  };
}

describe("constants", () => {
  it("has the units and limits", () => {
    expect(UNIT_MINUTES).toEqual({ minutes: 1, hours: 60, days: 1440 });
    expect(WAIT_UNITS.map((u) => u.label)).toEqual([
      "Minutes",
      "Hours",
      "Days",
    ]);
    expect(MAX_STEPS).toBe(20);
    expect(MAX_WAIT_MINUTES).toBe(43_200);
  });
});

describe("defaultFrom", () => {
  const variations = ["true", "false"];

  it("uses the fallthrough's variation", () => {
    expect(defaultFrom({ variation: "false" }, variations)).toBe("false");
  });

  it("uses the largest weight of a rollout", () => {
    expect(
      defaultFrom(
        {
          rollout: {
            bucket_by: "key",
            weights: [
              { variation: "true", weight: 30_000 },
              { variation: "false", weight: 70_000 },
            ],
          },
        },
        variations,
      ),
    ).toBe("false");
  });

  it("falls back to the first variation", () => {
    expect(defaultFrom({}, variations)).toBe("true");
  });
});

describe("defaultPlan", () => {
  it("moves to the first other variation in 10, 25 and 50 % steps", () => {
    const p = defaultPlan("false", ["true", "false"]);
    expect(p.from).toBe("false");
    expect(p.to).toBe("true");
    expect(
      p.steps.map(({ percent, wait, unit }) => ({ percent, wait, unit })),
    ).toEqual([
      { percent: "10", wait: "1", unit: "hours" },
      { percent: "25", wait: "1", unit: "hours" },
      { percent: "50", wait: "1", unit: "hours" },
    ]);
    expect(new Set(p.steps.map((s) => s.id)).size).toBe(3);
    expect(planProblems(p)).toEqual([]);
  });
});

describe("addStep, removeStep and updateStep", () => {
  it("edits the steps by id", () => {
    const p = defaultPlan("false", ["true", "false"]);
    const added = addStep(p);
    expect(added.steps).toHaveLength(4);
    expect(added.steps[3]).toMatchObject({
      percent: "",
      wait: "1",
      unit: "hours",
    });

    const [first, second] = p.steps;
    const updated = updateStep(p, second.id, { percent: "30", unit: "days" });
    expect(updated.steps[1]).toMatchObject({
      percent: "30",
      wait: "1",
      unit: "days",
    });
    expect(updated.steps[0]).toBe(first);

    expect(removeStep(p, first.id).steps.map((s) => s.percent)).toEqual([
      "25",
      "50",
    ]);
    expect(p.steps).toHaveLength(3);
  });
});

describe("toRolloutIn", () => {
  it("converts percents and waits and ends at 100 %", () => {
    expect(toRolloutIn(defaultPlan("false", ["true", "false"]))).toEqual({
      from_variation: "false",
      to_variation: "true",
      steps: [
        { percentage: 10_000, wait_minutes: 60 },
        { percentage: 25_000, wait_minutes: 60 },
        { percentage: 50_000, wait_minutes: 60 },
        { percentage: 100_000, wait_minutes: 60 },
      ],
    });
  });

  it("keeps fractions of a percent", () => {
    expect(toRolloutIn(plan([["12.5", "30", "minutes"]])).steps[0]).toEqual({
      percentage: 12_500,
      wait_minutes: 30,
    });
  });
});

describe("planProblems", () => {
  it("needs two different variations", () => {
    expect(
      planProblems({ ...plan([["10", "1", "hours"]]), to: "false" }),
    ).toEqual([{ path: "to", message: "Choose a variation other than From" }]);
  });

  it("needs percentages above 0, below 100 and rising", () => {
    expect(
      planProblems(
        plan([
          ["0", "1", "hours"],
          ["40", "1", "hours"],
          ["100", "1", "hours"],
          ["30", "1", "hours"],
          ["", "1", "hours"],
        ]),
      ),
    ).toEqual([
      {
        path: "steps[0].percent",
        message: "Enter a percentage above 0 and below 100",
      },
      {
        path: "steps[2].percent",
        message: "Enter a percentage above 0 and below 100",
      },
      {
        path: "steps[3].percent",
        message: "Each step must be higher than the one before",
      },
      {
        path: "steps[4].percent",
        message: "Enter a percentage above 0 and below 100",
      },
    ]);
  });

  it("needs whole waits of 1 minute to 30 days", () => {
    expect(
      planProblems(
        plan([
          ["10", "1.5", "hours"],
          ["20", "31", "days"],
          ["30", "0", "minutes"],
          ["40", "30", "days"],
        ]),
      ),
    ).toEqual([
      { path: "steps[0].wait", message: "Enter a whole number" },
      { path: "steps[1].wait", message: "Wait between 1 minute and 30 days" },
      { path: "steps[2].wait", message: "Wait between 1 minute and 30 days" },
    ]);
  });

  it("allows at most 20 steps with the final 100 %", () => {
    const steps = Array.from(
      { length: 19 },
      (_, i): [string, string, "hours"] => [String(i + 1), "1", "hours"],
    );
    expect(planProblems(plan(steps))).toEqual([]);
    expect(planProblems(plan([...steps, ["50", "1", "hours"]]))).toEqual([
      { path: "steps", message: "At most 20 steps" },
    ]);
  });
});

describe("waitMinutes, totalMinutes and durationText", () => {
  it("adds up the waits", () => {
    expect(
      waitMinutes({ id: "a", percent: "10", wait: "2", unit: "days" }),
    ).toBe(2880);
    expect(
      totalMinutes(
        plan([
          ["10", "1", "hours"],
          ["30", "30", "minutes"],
        ]),
      ),
    ).toBe(90);
  });

  it("describes a duration", () => {
    expect(durationText(0)).toBe("now");
    expect(durationText(60)).toBe("1 hour");
    expect(durationText(90)).toBe("1 hour 30 minutes");
    expect(durationText(1500)).toBe("1 day 1 hour");
    expect(durationText(43_200)).toBe("30 days");
  });
});

describe("currentPercent and stepStates", () => {
  it("reads the current step", () => {
    expect(currentPercent(rollout())).toBe(25);
    expect(
      currentPercent(rollout({ status: "completed", current_step: 2 })),
    ).toBe(100);
  });

  it("marks steps done, current and next", () => {
    expect(stepStates(rollout())).toEqual([
      { percent: 10, state: "done", waitMinutes: 60 },
      { percent: 25, state: "current", waitMinutes: 120 },
      { percent: 100, state: "next", waitMinutes: 60 },
    ]);
    expect(
      stepStates(rollout({ status: "completed", current_step: 2 })).map(
        (s) => s.state,
      ),
    ).toEqual(["done", "done", "done"]);
  });
});

describe("rolloutPill and variationText", () => {
  it("has a pill for every status", () => {
    expect(rolloutPill("active")).toEqual({
      status: "pending",
      label: "Running",
    });
    expect(rolloutPill("paused")).toEqual({ status: "stale", label: "Paused" });
    expect(rolloutPill("completed")).toEqual({
      status: "on",
      label: "Completed",
    });
    expect(rolloutPill("aborted")).toEqual({ status: "off", label: "Aborted" });
    expect(rolloutPill("failed")).toEqual({ status: "error", label: "Failed" });
    expect(rolloutPill("cancelled")).toEqual({
      status: "off",
      label: "Cancelled",
    });
  });

  it("names a deleted variation", () => {
    expect(variationText("true")).toBe("true");
    expect(variationText(null)).toBe("deleted variation");
  });
});
