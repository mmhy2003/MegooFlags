import { describe, expect, it } from "vitest";
import { Exposures } from "../src/internal/exposures.js";
import type { Logger } from "../src/internal/transport.js";

const USER1 = { key: "user-1", plan: "pro" };

function recordingLogger() {
  const warnings: string[] = [];
  const logger: Logger = {
    debug: () => {},
    info: () => {},
    warn: (m) => warnings.push(m),
    error: () => {},
  };
  return { warnings, logger };
}

/** A clock the test moves; it starts at 2026-11-10T09:15:02Z. */
function clock() {
  let t = Date.UTC(2026, 10, 10, 9, 15, 2);
  return {
    now: () => new Date(t),
    advance: (ms: number) => (t += ms),
  };
}

function make(o: Partial<ConstructorParameters<typeof Exposures>[0]> = {}) {
  const c = clock();
  const logs = recordingLogger();
  const ex = new Exposures({
    sendContext: false,
    now: c.now,
    logger: logs.logger,
    ...o,
  });
  return { ex, clock: c, logs };
}

describe("Exposures", () => {
  it("records the same user, flag and variant once within the hour", () => {
    const { ex, clock } = make();

    ex.record("f", "on", "TARGETING_MATCH", USER1);
    clock.advance(30 * 60_000);
    ex.record("f", "on", "TARGETING_MATCH", USER1);

    expect(ex.size).toBe(1);
  });

  it("records it again in the next UTC hour", () => {
    const { ex, clock } = make();

    ex.record("f", "on", "TARGETING_MATCH", USER1);
    clock.advance(45 * 60_000);
    ex.record("f", "on", "TARGETING_MATCH", USER1);

    expect(ex.size).toBe(2);
  });

  it("records another variant, flag or user again", () => {
    const { ex } = make();

    ex.record("f", "on", "TARGETING_MATCH", USER1);
    ex.record("f", "off", "TARGETING_MATCH", USER1);
    ex.record("g", "on", "TARGETING_MATCH", USER1);
    ex.record("f", "on", "TARGETING_MATCH", { key: "user-2" });

    expect(ex.size).toBe(4);
  });

  it("treats contexts without a targeting key as one user", () => {
    const { ex } = make();

    ex.record("f", "on", "STATIC", {});
    ex.record("f", "on", "STATIC", { plan: "pro" });

    expect(ex.size).toBe(1);
  });

  it("evicts the least recently seen key", () => {
    const { ex } = make({ maxKeys: 2 });

    ex.record("a", "on", "STATIC", USER1);
    ex.record("b", "on", "STATIC", USER1);
    ex.record("a", "on", "STATIC", USER1); // a is now the most recent
    ex.record("c", "on", "STATIC", USER1); // evicts b
    ex.drain(10);
    ex.record("a", "on", "STATIC", USER1);
    ex.record("b", "on", "STATIC", USER1);

    expect(ex.drain(10).map((e) => e.flag_key)).toEqual(["b"]);
  });

  it("builds the exposure with an RFC 3339 UTC timestamp and no context", () => {
    const { ex } = make();

    ex.record("f", "on", "TARGETING_MATCH", USER1);

    expect(ex.drain(10)).toEqual([
      {
        flag_key: "f",
        variant: "on",
        reason: "TARGETING_MATCH",
        timestamp: "2026-11-10T09:15:02.000Z",
      },
    ]);
  });

  it("adds the context only with sendContext", () => {
    const { ex } = make({ sendContext: true });

    ex.record("f", "on", "TARGETING_MATCH", USER1);

    expect(ex.drain(10)[0]?.context).toEqual(USER1);
  });

  it("drains at most max items, oldest first", () => {
    const { ex } = make();
    for (const f of ["a", "b", "c"]) ex.record(f, "on", "STATIC", USER1);

    expect(ex.drain(2).map((e) => e.flag_key)).toEqual(["a", "b"]);
    expect(ex.size).toBe(1);
    expect(ex.drain(2).map((e) => e.flag_key)).toEqual(["c"]);
  });

  it("puts items back in front of the buffer", () => {
    const { ex } = make();
    for (const f of ["a", "b", "c"]) ex.record(f, "on", "STATIC", USER1);
    const batch = ex.drain(2);

    ex.restore(batch);

    expect(ex.drain(10).map((e) => e.flag_key)).toEqual(["a", "b", "c"]);
  });

  it("drops new items over the bound and warns once until the next drain", () => {
    const { ex, logs } = make({ maxBuffer: 2 });

    for (const f of ["a", "b", "c", "d"]) ex.record(f, "on", "STATIC", USER1);

    expect(ex.size).toBe(2);
    expect(logs.warnings).toEqual([
      "megooflags: the exposure buffer is full (2); dropping new exposures",
    ]);
    expect(ex.drain(10).map((e) => e.flag_key)).toEqual(["a", "b"]);

    for (const f of ["e", "f", "g"]) ex.record(f, "on", "STATIC", USER1);
    expect(logs.warnings).toHaveLength(2);
  });

  it("records a dropped item again once there is room", () => {
    const { ex } = make({ maxBuffer: 1 });

    ex.record("a", "on", "STATIC", USER1);
    ex.record("b", "on", "STATIC", USER1);
    ex.drain(10);
    ex.record("b", "on", "STATIC", USER1);

    expect(ex.drain(10).map((e) => e.flag_key)).toEqual(["b"]);
  });

  it("calls onFull in a microtask once 1000 items are buffered", async () => {
    const { ex } = make();
    let calls = 0;
    ex.onFull = () => calls++;

    for (let i = 0; i < 1000; i++) ex.record(`f${i}`, "on", "STATIC", USER1);
    expect(calls).toBe(0);
    ex.record("more", "on", "STATIC", USER1);
    await Promise.resolve();

    expect(calls).toBe(1);
  });
});
