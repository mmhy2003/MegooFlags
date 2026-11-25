import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import {
  type AutomationSnapshot,
  automationCount,
  defaultRunAt,
  localToIso,
  needsPolling,
  past,
  relativeText,
  runAtProblem,
  SCHEDULE_ACTIONS,
  schedulePermission,
  schedulePill,
  scheduleText,
  upcoming,
  utcText,
  wasApplied,
} from "@/lib/flags/schedules";

type Schedule = components["schemas"]["ScheduleOut"];
type Rollout = components["schemas"]["ProgressiveRolloutOut"];

const NOW = new Date(2026, 10, 24, 10, 2);

function schedule(patch: Partial<Schedule> = {}): Schedule {
  return {
    id: "s1",
    environment: "development",
    action: "enable",
    weights: null,
    run_at: "2026-12-01T08:30:00Z",
    status: "pending",
    error: null,
    executed_at: null,
    change_request_id: null,
    created_by: "alice",
    created_at: "2026-11-20T08:00:00Z",
    ...patch,
  };
}

function rollout(patch: Partial<Rollout> = {}): Rollout {
  return {
    id: "r1",
    environment: "development",
    from_variation: "false",
    to_variation: "true",
    status: "active",
    current_step: 0,
    steps: [
      { percentage: 10_000, wait_minutes: 60 },
      { percentage: 100_000, wait_minutes: 60 },
    ],
    last_applied_weights: null,
    next_step_at: null,
    remaining_seconds: null,
    error: null,
    finished_at: null,
    created_by: "alice",
    created_at: "2026-11-20T08:00:00Z",
    updated_at: "2026-11-20T08:00:00Z",
    ...patch,
  };
}

function snapshot(
  current: Rollout | null,
  schedules: Schedule[] | null = [],
): AutomationSnapshot {
  return { rollout: { current, recent: [] }, schedules };
}

describe("SCHEDULE_ACTIONS", () => {
  it("lists each action with its label", () => {
    expect(SCHEDULE_ACTIONS).toEqual([
      { value: "enable", label: "Turn on" },
      { value: "disable", label: "Turn off" },
      { value: "set_rollout", label: "Set the default rule to a rollout" },
    ]);
  });
});

describe("scheduleText", () => {
  it("names a toggle", () => {
    expect(scheduleText({ action: "enable", weights: null })).toBe("Turn on");
    expect(scheduleText({ action: "disable", weights: null })).toBe("Turn off");
  });

  it("lists a rollout's weights in the order given", () => {
    expect(
      scheduleText({
        action: "set_rollout",
        weights: [
          { variation: "true", weight: 25_000 },
          { variation: "false", weight: 75_000 },
        ],
      }),
    ).toBe("Set the default rule to true 25 %, false 75 %");
  });
});

describe("schedulePermission", () => {
  it("needs config write for a rollout and toggle otherwise", () => {
    expect(schedulePermission("set_rollout")).toBe("flag.config.write");
    expect(schedulePermission("enable")).toBe("flag.toggle");
    expect(schedulePermission("disable")).toBe("flag.toggle");
  });
});

describe("upcoming and past", () => {
  const list = [
    schedule({ id: "late", run_at: "2026-12-03T00:00:00Z" }),
    schedule({
      id: "done",
      status: "done",
      run_at: "2026-11-01T00:00:00Z",
      executed_at: "2026-11-01T00:00:05Z",
    }),
    schedule({ id: "soon", run_at: "2026-12-01T00:00:00Z" }),
    schedule({
      id: "cancelled",
      status: "cancelled",
      run_at: "2026-11-10T00:00:00Z",
    }),
    schedule({
      id: "failed",
      status: "failed",
      run_at: "2026-11-05T00:00:00Z",
      executed_at: "2026-11-05T00:00:01Z",
    }),
  ];

  it("keeps pending schedules, soonest first", () => {
    expect(upcoming(list).map((s) => s.id)).toEqual(["soon", "late"]);
  });

  it("keeps the others, newest first", () => {
    expect(past(list).map((s) => s.id)).toEqual([
      "cancelled",
      "failed",
      "done",
    ]);
  });
});

describe("schedulePill", () => {
  it("maps every status", () => {
    expect(schedulePill("pending")).toEqual({
      status: "pending",
      label: "Pending",
    });
    expect(schedulePill("done")).toEqual({ status: "on", label: "Applied" });
    expect(schedulePill("failed")).toEqual({
      status: "error",
      label: "Failed",
    });
    expect(schedulePill("cancelled")).toEqual({
      status: "off",
      label: "Cancelled",
    });
  });
});

describe("defaultRunAt", () => {
  it("is an hour ahead, rounded up to 5 minutes, in local time", () => {
    expect(defaultRunAt(NOW)).toBe("2026-11-24T11:05");
  });

  it("keeps a time already on 5 minutes", () => {
    expect(defaultRunAt(new Date(2026, 10, 24, 10, 5))).toBe(
      "2026-11-24T11:05",
    );
  });

  it("rolls over to the next day", () => {
    expect(defaultRunAt(new Date(2026, 11, 31, 23, 58))).toBe(
      "2027-01-01T01:00",
    );
  });
});

describe("localToIso", () => {
  it("turns a local time into an ISO string", () => {
    expect(localToIso("2026-11-24T11:05")).toBe(
      new Date(2026, 10, 24, 11, 5).toISOString(),
    );
  });

  it("is null for an empty or invalid value", () => {
    expect(localToIso("")).toBeNull();
    expect(localToIso("garbage")).toBeNull();
  });
});

describe("runAtProblem", () => {
  it("asks for a value", () => {
    expect(runAtProblem("", NOW)).toBe("Pick a date and time");
    expect(runAtProblem("garbage", NOW)).toBe("Pick a date and time");
  });

  it("rejects a time in the past", () => {
    expect(runAtProblem("2026-11-24T10:02", NOW)).toBe(
      "Pick a time in the future",
    );
    expect(runAtProblem("2026-11-23T12:00", NOW)).toBe(
      "Pick a time in the future",
    );
  });

  it("rejects a time more than 365 days ahead", () => {
    expect(runAtProblem("2027-11-25T10:02", NOW)).toBe(
      "Pick a time within 365 days",
    );
  });

  it("accepts a time in range", () => {
    expect(runAtProblem("2026-11-24T11:05", NOW)).toBeUndefined();
    expect(runAtProblem("2027-11-24T10:02", NOW)).toBeUndefined();
  });
});

describe("utcText", () => {
  it("shows the time in UTC", () => {
    expect(utcText("2026-12-01T08:30:00Z")).toBe("2026-12-01 08:30 UTC");
    expect(utcText("2026-12-01T10:30:00+02:00")).toBe("2026-12-01 08:30 UTC");
  });
});

describe("relativeText", () => {
  const now = new Date("2026-11-24T10:00:00Z");

  it("says how far ahead", () => {
    expect(relativeText("2026-11-24T13:00:00Z", now)).toBe("in 3 hours");
    expect(relativeText("2026-11-24T10:20:00Z", now)).toBe("in 20 minutes");
    expect(relativeText("2026-11-27T10:00:00Z", now)).toBe("in 3 days");
  });

  it("says how long ago", () => {
    expect(relativeText("2026-11-22T10:00:00Z", now)).toBe("2 days ago");
    expect(relativeText("2026-11-24T09:59:50Z", now)).toBe("1 minute ago");
  });
});

describe("automationCount and needsPolling", () => {
  it("counts pending schedules and a live rollout", () => {
    const schedules = [
      schedule({ id: "a" }),
      schedule({ id: "b", status: "done" }),
      schedule({ id: "c" }),
    ];
    expect(automationCount(snapshot(rollout(), schedules))).toBe(3);
    expect(
      automationCount(snapshot(rollout({ status: "paused" }), schedules)),
    ).toBe(3);
    expect(
      automationCount(snapshot(rollout({ status: "completed" }), schedules)),
    ).toBe(2);
    expect(automationCount({ rollout: null, schedules: null })).toBe(0);
  });

  it("polls while a rollout runs or a schedule is pending", () => {
    expect(needsPolling(snapshot(rollout()))).toBe(true);
    expect(needsPolling(snapshot(rollout({ status: "paused" })))).toBe(false);
    expect(needsPolling(snapshot(null, [schedule()]))).toBe(true);
    expect(needsPolling(snapshot(null, [schedule({ status: "done" })]))).toBe(
      false,
    );
    expect(needsPolling({ rollout: null, schedules: null })).toBe(false);
  });
});

describe("wasApplied", () => {
  it("sees a pending schedule that is done", () => {
    expect(
      wasApplied(
        snapshot(null, [schedule()]),
        snapshot(null, [schedule({ status: "done" })]),
      ),
    ).toBe(true);
  });

  it("ignores a schedule that was cancelled or failed", () => {
    expect(
      wasApplied(
        snapshot(null, [schedule()]),
        snapshot(null, [schedule({ status: "cancelled" })]),
      ),
    ).toBe(false);
  });

  it("sees the same rollout's step grow", () => {
    expect(
      wasApplied(
        snapshot(rollout({ current_step: 0 })),
        snapshot(rollout({ current_step: 1 })),
      ),
    ).toBe(true);
  });

  it("sees a live rollout that is no longer live", () => {
    expect(wasApplied(snapshot(rollout()), snapshot(null))).toBe(true);
    expect(
      wasApplied(
        snapshot(rollout({ status: "paused" })),
        snapshot(rollout({ status: "aborted" })),
      ),
    ).toBe(true);
  });

  it("is false when nothing changed", () => {
    const same = snapshot(rollout(), [schedule()]);
    expect(wasApplied(same, snapshot(rollout(), [schedule()]))).toBe(false);
    expect(
      wasApplied(
        { rollout: null, schedules: null },
        snapshot(rollout(), [schedule()]),
      ),
    ).toBe(false);
  });
});
