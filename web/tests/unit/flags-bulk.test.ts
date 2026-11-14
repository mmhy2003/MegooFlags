import { describe, expect, it } from "vitest";

import { runBulk, tagsAfter } from "@/lib/flags/bulk";
import { ApiError } from "@/lib/flags/errors";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("runBulk", () => {
  it("keeps at most 4 actions in flight and returns results in input order", async () => {
    const keys = Array.from({ length: 10 }, (_, i) => `f-${i}`);
    const pending = new Map<string, () => void>();
    let inFlight = 0;
    let maxInFlight = 0;
    const run = runBulk(keys, (key) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      const d = deferred();
      pending.set(key, d.resolve);
      return d.promise.then(() => {
        inFlight -= 1;
      });
    });
    // Finish them in reverse order of starting, as they start.
    while (pending.size || inFlight) {
      await flush();
      const started = [...pending.keys()].reverse();
      for (const key of started) {
        pending.get(key)!();
        pending.delete(key);
      }
    }
    const results = await run;
    expect(maxInFlight).toBe(4);
    expect(results).toEqual(keys.map((key) => ({ key, ok: true })));
  });

  it("gives a reason per failure and keeps going", async () => {
    const approval = new ApiError({
      status: 409,
      code: "approval_required",
      message: "environment 'prod' requires approval",
      path: null,
    });
    const results = await runBulk(["a", "b", "c"], async (key) => {
      if (key === "a") throw approval;
      if (key === "b") throw new Error("boom");
    });
    expect(results).toEqual([
      { key: "a", ok: false, reason: "Needs approval" },
      { key: "b", ok: false, reason: "Error: boom" },
      { key: "c", ok: true },
    ]);
  });

  it("calls onDone once per key", async () => {
    const done: string[] = [];
    await runBulk(
      ["a", "b", "c"],
      async (key) => {
        if (key === "b") throw new Error("boom");
      },
      2,
      (r) => done.push(r.key),
    );
    expect(done.sort()).toEqual(["a", "b", "c"]);
  });
});

describe("tagsAfter", () => {
  it("is null when adding a tag that is already there", () => {
    expect(tagsAfter(["web"], "web", "add")).toBeNull();
  });

  it("appends a new tag", () => {
    expect(tagsAfter(["web"], "checkout", "add")).toEqual(["web", "checkout"]);
  });

  it("removes a tag", () => {
    expect(tagsAfter(["web"], "web", "remove")).toEqual([]);
  });

  it("is null when removing a tag that isn't there", () => {
    expect(tagsAfter(["web"], "search", "remove")).toBeNull();
  });
});
