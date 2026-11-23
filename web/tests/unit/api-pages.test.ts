import { describe, expect, it } from "vitest";

import { allPages } from "@/lib/api/pages";

describe("allPages", () => {
  it("follows next_cursor until the last page", async () => {
    const pages: Record<
      string,
      { items: number[]; next_cursor: string | null }
    > = {
      "": { items: [1, 2], next_cursor: "b" },
      b: { items: [3], next_cursor: "c" },
      c: { items: [4], next_cursor: null },
    };
    const asked: (string | undefined)[] = [];
    const items = await allPages(async (cursor) => {
      asked.push(cursor);
      return pages[cursor ?? ""];
    });
    expect(items).toEqual([1, 2, 3, 4]);
    expect(asked).toEqual([undefined, "b", "c"]);
  });

  it("passes a failed page's error on", async () => {
    await expect(
      allPages(async (cursor) => {
        if (cursor) throw new Error("boom");
        return { items: [1], next_cursor: "b" };
      }),
    ).rejects.toThrow("boom");
  });
});
