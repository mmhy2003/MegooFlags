import { describe, expect, it } from "vitest";
import { Backoff, sleep } from "../../src/internal/transport/backoff.js";

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
