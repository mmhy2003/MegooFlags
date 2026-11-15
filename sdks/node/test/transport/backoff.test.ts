import { describe, expect, it } from "vitest";
import {
  Backoff,
  MAX_TIMER_MS,
  sleep,
  timerDelay,
} from "../../src/internal/transport/backoff.js";

describe("Backoff", () => {
  it("doubles from base up to max", () => {
    const b = new Backoff(3000, 60_000, () => 1);
    const waits = Array.from({ length: 7 }, () => b.next());
    expect(waits).toEqual([3, 6, 12, 24, 48, 60, 60].map((s) => s * 1000));
  });

  it("reset starts over", () => {
    const b = new Backoff(3000, 60_000, () => 1);
    b.next();
    b.next();
    b.reset();
    expect(b.next()).toBe(3000);
  });

  it("jitters from the second wait", () => {
    const b = new Backoff(3000, 60_000, () => 0);
    expect(b.next()).toBe(3000);
    expect(b.next()).toBe(4800);
    expect(b.next()).toBe(9600);
  });

  it("first wait is never above max", () => {
    expect(new Backoff(90_000, 60_000).next()).toBe(60_000);
  });

  it("never goes above max", () => {
    const b = new Backoff(3000, 60_000);
    for (let i = 0; i < 200; i++) {
      const w = b.next();
      expect(w).toBeGreaterThan(0);
      expect(w).toBeLessThanOrEqual(60_000);
    }
  });

  it("base can be replaced", () => {
    const b = new Backoff(3000, 60_000, () => 1);
    b.base = 1000;
    expect(b.next()).toBe(1000);
    expect(b.next()).toBe(2000);
  });
});

describe("sleep", () => {
  it("resolves true after the wait", async () => {
    expect(await sleep(1)).toBe(true);
  });

  it("ends early with false when cancelled", async () => {
    const ac = new AbortController();
    const start = Date.now();
    setTimeout(() => ac.abort(), 10);
    expect(await sleep(3_600_000, ac.signal)).toBe(false);
    expect(Date.now() - start).toBeLessThan(5000);
  });

  it("returns false at once when already cancelled", async () => {
    const ac = new AbortController();
    ac.abort();
    expect(await sleep(3_600_000, ac.signal)).toBe(false);
  });
});

describe("timerDelay", () => {
  it("keeps delays a timer can wait", () => {
    expect(MAX_TIMER_MS).toBe(2_147_483_647);
    expect(timerDelay(1)).toBe(1);
    expect(timerDelay(30_000)).toBe(30_000);
    expect(timerDelay(MAX_TIMER_MS)).toBe(MAX_TIMER_MS);
  });

  it("clamps longer delays instead of letting them wrap to 1 ms", () => {
    expect(timerDelay(MAX_TIMER_MS + 1)).toBe(MAX_TIMER_MS);
    expect(timerDelay(1e15)).toBe(MAX_TIMER_MS);
    expect(timerDelay(Infinity)).toBe(MAX_TIMER_MS);
  });

  it("makes negative and NaN delays 0", () => {
    expect(timerDelay(-5)).toBe(0);
    expect(timerDelay(NaN)).toBe(0);
  });
});

describe("sleep with a huge wait", () => {
  it("does not end early", async () => {
    const ac = new AbortController();
    let ended: boolean | undefined;
    const p = sleep(2 ** 40, ac.signal).then((v) => (ended = v));
    await new Promise((r) => setTimeout(r, 50));
    expect(ended).toBeUndefined();
    ac.abort();
    expect(await p).toBe(false);
  });
});
