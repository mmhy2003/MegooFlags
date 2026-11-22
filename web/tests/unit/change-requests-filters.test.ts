import { describe, expect, it } from "vitest";

import {
  canComment,
  crFiltersFromParams,
  crFiltersToParams,
  crListQuery,
} from "@/lib/change-requests/filters";
import type { MePermissions } from "@/lib/nav";

function perms(env: MePermissions["org"]): MePermissions {
  return {
    org: [],
    project: ["project.read"],
    environments: { production: env },
  };
}

describe("crFiltersFromParams", () => {
  it("defaults a missing status to open", () => {
    expect(crFiltersFromParams(new URLSearchParams())).toEqual({
      status: "open",
      env: "",
      flag: "",
    });
  });

  it("keeps all", () => {
    expect(
      crFiltersFromParams(new URLSearchParams("status=all&env=production"))
        .status,
    ).toBe("all");
  });

  it("turns an unknown status into open", () => {
    expect(
      crFiltersFromParams(new URLSearchParams("status=bogus&flag=checkout")),
    ).toEqual({ status: "open", env: "", flag: "checkout" });
  });
});

describe("crFiltersToParams", () => {
  it("omits the defaults", () => {
    expect(
      crFiltersToParams({ status: "open", env: "", flag: "" }).toString(),
    ).toBe("");
  });

  it("keeps the rest", () => {
    expect(
      crFiltersToParams({
        status: "applied",
        env: "production",
        flag: "checkout",
      }).toString(),
    ).toBe("status=applied&env=production&flag=checkout");
  });
});

describe("crListQuery", () => {
  it("drops the status for all and sets the page size", () => {
    expect(crListQuery({ status: "all", env: "", flag: "" })).toEqual({
      limit: 50,
    });
  });

  it("passes the filters and the cursor", () => {
    expect(
      crListQuery(
        { status: "open", env: "production", flag: "checkout" },
        "c1",
      ),
    ).toEqual({
      status: "open",
      env: "production",
      flag: "checkout",
      cursor: "c1",
      limit: 50,
    });
  });
});

describe("canComment", () => {
  it("lets the author comment", () => {
    expect(canComment(perms([]), "production", true)).toBe(true);
  });

  it("lets someone who can toggle in the environment comment", () => {
    expect(canComment(perms(["flag.toggle"]), "production", false)).toBe(true);
  });

  it("does not let a viewer comment", () => {
    expect(canComment(perms(["project.read"]), "production", false)).toBe(
      false,
    );
  });

  it("does not let anyone comment without permissions unless they wrote it", () => {
    expect(canComment(null, "production", false)).toBe(false);
  });
});
