import { describe, expect, it } from "vitest";
import {
  Aggregator,
  Lru,
  type Evaluation,
} from "../../src/internal/events/aggregate.js";

const t0 = Date.UTC(2026, 10, 10, 9, 15, 2);
const HOUR = 3_600_000;

function served(flag: string, variant: string, key: string, at = t0) {
  return {
    flagKey: flag,
    variant,
    served: true,
    reason: "SPLIT",
    contextKey: key,
    context: { key },
    at,
  } satisfies Evaluation;
}

function unserved(flag: string, key = "", at = t0): Evaluation {
  return {
    flagKey: flag,
    variant: "",
    served: false,
    reason: "ERROR",
    contextKey: key,
    at,
  };
}

const agg = (dedup = 100, pending = 100, sendContext = false) =>
  new Aggregator({ dedupSize: dedup, maxPending: pending, sendContext });

describe("Lru", () => {
  it("evicts the least recently seen key", () => {
    const l = new Lru(2);
    expect(l.seen("a")).toBe(false);
    expect(l.seen("b")).toBe(false);
    expect(l.seen("a")).toBe(true);
    // a is now the most recent, so inserting c evicts b.
    expect(l.seen("c")).toBe(false);
    expect(l.seen("a")).toBe(true);
    expect(l.seen("b")).toBe(false);
    expect(l.size).toBe(2);
  });
});

describe("Aggregator", () => {
  it("queues one exposure per context key, flag, variant and hour", () => {
    const a = agg();
    a.add(served("f", "on", "u1"));
    a.add(served("f", "on", "u1", t0 + 10 * 60_000));
    expect(a.pending().exposures).toBe(1);
    a.add(served("f", "on", "u1", t0 + HOUR));
    expect(a.pending().exposures).toBe(2);

    for (const ev of [
      served("f", "off", "u1"),
      served("f", "on", "u2"),
      served("g", "on", "u1"),
    ]) {
      const b = agg();
      b.add(served("f", "on", "u1"));
      b.add(ev);
      expect(b.pending().exposures).toBe(2);
    }
  });

  it("gives anonymous contexts one shared exposure", () => {
    const a = agg();
    a.add(served("f", "on", ""));
    a.add(served("f", "on", ""));
    expect(a.pending().exposures).toBe(1);
  });

  it("adds counts up per flag, variant and hour", () => {
    const a = agg();
    for (let i = 0; i < 5; i++) {
      a.add(served("f", "on", `u${i}`));
    }
    a.add(served("f", "on", "u1", t0 + HOUR));
    a.add(served("f", "off", "u1"));
    expect(a.pending().counts).toBe(3);
    expect(a.take(1000).counts).toEqual([
      { flag_key: "f", variant: "on", hour: "2026-11-10T09:00:00Z", count: 5 },
      { flag_key: "f", variant: "on", hour: "2026-11-10T10:00:00Z", count: 1 },
      { flag_key: "f", variant: "off", hour: "2026-11-10T09:00:00Z", count: 1 },
    ]);
    expect(a.pending()).toEqual({ exposures: 0, counts: 0 });
  });

  it("counts unserved evaluations with a null variant and no exposure", () => {
    const a = agg();
    a.add(unserved("f", "u1"));
    a.add(unserved("f", "u2"));
    const b = a.take(1000);
    expect(b.exposures).toEqual([]);
    expect(b.counts).toEqual([
      { flag_key: "f", variant: null, hour: "2026-11-10T09:00:00Z", count: 2 },
    ]);
  });

  it("sends the context only with sendContext", () => {
    const off = agg(100, 100, false);
    off.add(served("f", "on", "u1"));
    expect(off.take(1000).exposures[0]).not.toHaveProperty("context");

    const on = agg(100, 100, true);
    on.add(served("f", "on", "u1"));
    expect(on.take(1000).exposures[0]!.context).toEqual({ key: "u1" });
  });

  it("fills the exposure fields with a UTC millisecond timestamp", () => {
    const a = agg();
    a.add(served("f", "on", "u1", t0 + 123));
    expect(a.take(1000).exposures).toEqual([
      {
        flag_key: "f",
        variant: "on",
        reason: "SPLIT",
        timestamp: "2026-11-10T09:15:02.123Z",
      },
    ]);
  });

  it("writes the hour truncated to UTC without fractions", () => {
    const a = agg();
    a.add(unserved("f", "", Date.UTC(2026, 10, 10, 9, 59, 59, 999)));
    expect(a.take(10).counts[0]!.hour).toBe("2026-11-10T09:00:00Z");
  });

  it("forgets the oldest key beyond the dedup size", () => {
    const a = agg(2, 100);
    a.add(served("f", "on", "u1"));
    a.add(served("f", "on", "u2"));
    a.add(served("f", "on", "u3"));
    a.add(served("f", "on", "u1"));
    expect(a.pending().exposures).toBe(4);
  });

  it("drops exposures beyond maxPending but never counts", () => {
    const a = agg(100, 2);
    for (let i = 0; i < 5; i++) {
      a.add(served("f", "on", `u${i}`));
    }
    a.add(served("f", "on", "u0")); // a duplicate is not a drop
    expect(a.pending().exposures).toBe(2);
    expect(a.dropped).toBe(3);
    expect(a.take(1000).counts[0]!.count).toBe(6);
  });

  it("never drops a count, however many buckets are pending", () => {
    const a = new Aggregator();
    for (let i = 0; i < 10_001; i++) {
      a.add(unserved(`f${i}`));
    }
    a.add(served("f10000", "on", "u2"));
    expect(a.dropped).toBe(0);
    expect(a.pending()).toEqual({ exposures: 1, counts: 10_002 });
    let total = 0;
    while (a.pending().counts > 0) {
      total += a.take(1000).counts.reduce((n, c) => n + c.count, 0);
    }
    expect(total).toBe(10_002);
  });

  it("takes at most the limit of each list, oldest first", () => {
    const a = agg(10_000, 10_000);
    for (let i = 0; i < 1500; i++) {
      a.add(served(`f${i}`, "on", "u"));
    }
    let b = a.take(1000);
    expect(b.exposures).toHaveLength(1000);
    expect(b.counts).toHaveLength(1000);
    expect(b.exposures[0]!.flag_key).toBe("f0");
    expect(b.counts[999]!.flag_key).toBe("f999");
    b = a.take(1000);
    expect(b.exposures).toHaveLength(500);
    expect(b.counts).toHaveLength(500);
    expect(b.exposures[0]!.flag_key).toBe("f1000");
    expect(b.counts[0]!.flag_key).toBe("f1000");
    expect(a.pending()).toEqual({ exposures: 0, counts: 0 });
  });

  it("splits a count over 1e9 into several items", () => {
    const a = agg(10, 10);
    a.add(served("f", "on", "u"));
    a.restore({
      exposures: [],
      counts: [
        {
          flag_key: "f",
          variant: "on",
          hour: "2026-11-10T09:00:00Z",
          count: 2_499_999_999,
        },
      ],
    });
    a.take(0); // nothing taken
    let b = a.take(2);
    expect(b.counts.map((c) => c.count)).toEqual([1e9, 1e9]);
    b = a.take(2);
    expect(b.counts.map((c) => c.count)).toEqual([5e8]);
    expect(a.pending().counts).toBe(0);
  });

  it("restores a batch in front and merges its counts", () => {
    const a = agg();
    a.add(served("f", "on", "u1"));
    a.add(served("f", "on", "u2"));
    a.add(unserved("f"));
    const sent = a.take(1000);

    a.add(served("f", "on", "u3", t0 + 5));
    a.add(served("g", "on", "u3", t0 + 6));
    a.restore(sent);

    const b = a.take(1000);
    expect(b.exposures.map((e) => [e.flag_key, e.timestamp])).toEqual([
      ["f", sent.exposures[0]!.timestamp],
      ["f", sent.exposures[1]!.timestamp],
      ["f", "2026-11-10T09:15:02.005Z"],
      ["g", "2026-11-10T09:15:02.006Z"],
    ]);
    expect(b.counts).toEqual([
      { flag_key: "f", variant: "on", hour: "2026-11-10T09:00:00Z", count: 3 },
      { flag_key: "f", variant: null, hour: "2026-11-10T09:00:00Z", count: 1 },
      { flag_key: "g", variant: "on", hour: "2026-11-10T09:00:00Z", count: 1 },
    ]);
  });

  it("joins the items of a split count when restored", () => {
    const a = agg(10, 10);
    a.restore({
      exposures: [],
      counts: [
        {
          flag_key: "f",
          variant: "on",
          hour: "2026-11-10T09:00:00Z",
          count: 1e9,
        },
        {
          flag_key: "f",
          variant: "on",
          hour: "2026-11-10T09:00:00Z",
          count: 5,
        },
      ],
    });
    expect(a.pending().counts).toBe(1);
    expect(a.take(10).counts.map((c) => c.count)).toEqual([1e9, 5]);
  });

  it("serialises to the body of POST /sdk/v1/events", () => {
    const a = agg(10, 10, true);
    a.add(served("new-checkout", "on", "user-1"));
    a.add(unserved("new-checkout"));
    expect(JSON.stringify(a.take(10))).toBe(
      JSON.stringify({
        exposures: [
          {
            flag_key: "new-checkout",
            variant: "on",
            reason: "SPLIT",
            timestamp: "2026-11-10T09:15:02.000Z",
            context: { key: "user-1" },
          },
        ],
        counts: [
          {
            flag_key: "new-checkout",
            variant: "on",
            hour: "2026-11-10T09:00:00Z",
            count: 1,
          },
          {
            flag_key: "new-checkout",
            variant: null,
            hour: "2026-11-10T09:00:00Z",
            count: 1,
          },
        ],
      }),
    );
    expect(JSON.stringify(agg().take(10))).toBe('{"exposures":[],"counts":[]}');
  });
});
