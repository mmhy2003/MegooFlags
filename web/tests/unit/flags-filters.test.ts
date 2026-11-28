import { describe, expect, it } from "vitest";

import {
  activeFilterCount,
  EMPTY_FILTERS,
  filtersFromParams,
  filtersToParams,
  filtersToQuery,
  PAGE_SIZE,
  withFilter,
} from "@/lib/flags/filters";

describe("filtersFromParams", () => {
  it("gives the empty filters for empty params", () => {
    expect(filtersFromParams(new URLSearchParams())).toEqual(EMPTY_FILTERS);
  });

  it("turns invalid lifecycle and enabled values into empty ones", () => {
    const f = filtersFromParams(
      new URLSearchParams("lifecycle=zombie&enabled=maybe"),
    );
    expect(f.lifecycle).toBe("");
    expect(f.enabled).toBe("");
  });
});

describe("filtersToParams", () => {
  it("adds no query string for the defaults", () => {
    expect(filtersToParams(EMPTY_FILTERS).toString()).toBe("");
  });

  it("round-trips every filter", () => {
    const f = {
      q: "check",
      tags: ["web", "beta"],
      owner: "ana",
      lifecycle: "stale" as const,
      env: "prod",
      enabled: "on" as const,
      cursor: "abc",
    };
    const params = filtersToParams(f);
    expect(params.getAll("tag")).toEqual(["web", "beta"]);
    expect(filtersFromParams(new URLSearchParams(params.toString()))).toEqual(
      f,
    );
  });
});

describe("withFilter", () => {
  it("resets the cursor", () => {
    const f = { ...EMPTY_FILTERS, cursor: "abc" };
    expect(withFilter(f, { owner: "ana" })).toEqual({
      ...EMPTY_FILTERS,
      owner: "ana",
    });
  });

  it("keeps a cursor the patch sets", () => {
    expect(withFilter(EMPTY_FILTERS, { cursor: "next" }).cursor).toBe("next");
  });
});

describe("filtersToQuery", () => {
  it("omits empties and asks for a page", () => {
    expect(filtersToQuery(EMPTY_FILTERS)).toEqual({ limit: PAGE_SIZE });
    expect(PAGE_SIZE).toBe(50);
  });

  it("sends env and enabled only together", () => {
    expect(filtersToQuery({ ...EMPTY_FILTERS, env: "prod" })).toEqual({
      limit: 50,
    });
    expect(filtersToQuery({ ...EMPTY_FILTERS, enabled: "on" })).toEqual({
      limit: 50,
    });
    expect(
      filtersToQuery({ ...EMPTY_FILTERS, env: "prod", enabled: "on" }),
    ).toEqual({ env: "prod", enabled: true, limit: 50 });
    expect(
      filtersToQuery({ ...EMPTY_FILTERS, env: "prod", enabled: "off" }),
    ).toEqual({ env: "prod", enabled: false, limit: 50 });
  });

  it("sends the other filters", () => {
    expect(
      filtersToQuery({
        q: "x",
        tags: ["web"],
        owner: "ana",
        lifecycle: "new",
        env: "",
        enabled: "",
        cursor: "c",
      }),
    ).toEqual({
      q: "x",
      tag: ["web"],
      owner: "ana",
      lifecycle: "new",
      cursor: "c",
      limit: 50,
    });
  });
});

describe("activeFilterCount", () => {
  it("counts filters but not the search text", () => {
    expect(activeFilterCount({ ...EMPTY_FILTERS, q: "x" })).toBe(0);
    expect(
      activeFilterCount({
        ...EMPTY_FILTERS,
        tags: ["a", "b"],
        owner: "ana",
        lifecycle: "stale",
        env: "prod",
        enabled: "off",
      }),
    ).toBe(5);
  });
});
