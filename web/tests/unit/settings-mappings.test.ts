import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { mappingText, sortMappings } from "@/lib/settings/mappings";

type Mapping = components["schemas"]["SsoMappingOut"];

function mapping(id: string, over: Partial<Mapping> = {}): Mapping {
  return {
    id,
    group: "devs",
    team: null,
    role: null,
    scope: null,
    project: null,
    env: null,
    ...over,
  };
}

describe("mappingText", () => {
  it("names the team, or the role and its scope", () => {
    expect(mappingText(mapping("1", { team: "web" }))).toBe("devs → team web");
    expect(
      mappingText(
        mapping("2", {
          group: "ops",
          role: "viewer",
          scope: "project",
          project: "shop",
        }),
      ),
    ).toBe("ops → viewer · Project shop");
  });
});

describe("sortMappings", () => {
  it("orders by group, then by target, then by id, whatever the API order", () => {
    const opsRole = mapping("a", {
      group: "ops",
      role: "viewer",
      scope: "org",
    });
    const devsWeb = mapping("f", { team: "web" });
    const devsApi = mapping("c", { team: "api" });
    const devsApiAgain = mapping("b", { team: "api" });
    const sorted = sortMappings([opsRole, devsWeb, devsApi, devsApiAgain]);
    expect(sorted.map((m) => m.id)).toEqual(["b", "c", "f", "a"]);
  });

  it("leaves its argument alone", () => {
    const list = [mapping("2", { group: "ops", team: "x" }), mapping("1")];
    sortMappings(list);
    expect(list.map((m) => m.id)).toEqual(["2", "1"]);
  });
});
