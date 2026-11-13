import { describe, expect, it } from "vitest";

import {
  flagPath,
  homeTarget,
  newFlagPath,
  projectFromPath,
  projectPath,
  projectTrail,
  switchProjectPath,
} from "@/lib/routes";

describe("projectPath", () => {
  it("defaults to flags and encodes the key", () => {
    expect(projectPath("a b")).toBe("/projects/a%20b/flags");
    expect(projectPath("demo", "audit")).toBe("/projects/demo/audit");
  });
});

describe("flagPath", () => {
  it("points at a flag inside the project", () => {
    expect(flagPath("demo", "new-checkout")).toBe(
      "/projects/demo/flags/new-checkout",
    );
  });
});

describe("newFlagPath", () => {
  it("points at the New flag page and encodes the key", () => {
    expect(newFlagPath("a b")).toBe("/projects/a%20b/flags/new");
  });
});

describe("projectFromPath", () => {
  it("reads the project key from the URL", () => {
    expect(projectFromPath("/projects/demo")).toBe("demo");
    expect(projectFromPath("/projects/demo/keys")).toBe("demo");
    expect(projectFromPath("/projects/a%20b/flags")).toBe("a b");
    expect(projectFromPath("/settings")).toBeNull();
  });
});

describe("switchProjectPath", () => {
  it("keeps a known section", () => {
    expect(switchProjectPath("/projects/a/audit", "b")).toBe(
      "/projects/b/audit",
    );
  });

  it("falls back to flags outside a project", () => {
    expect(switchProjectPath("/settings", "b")).toBe("/projects/b/flags");
  });

  it("drops deeper paths that belong to the old project", () => {
    expect(switchProjectPath("/projects/a/flags/x", "b")).toBe(
      "/projects/b/flags",
    );
  });
});

describe("homeTarget", () => {
  it("opens the last project when it is still listed", () => {
    expect(homeTarget(["a", "b"], "b")).toBe("/projects/b/flags");
  });

  it("opens the first project otherwise", () => {
    expect(homeTarget(["a", "b"], "gone")).toBe("/projects/a/flags");
    expect(homeTarget(["a", "b"], null)).toBe("/projects/a/flags");
  });

  it("has nowhere to go without projects", () => {
    expect(homeTarget([], "b")).toBeNull();
  });
});

describe("projectTrail", () => {
  it("leads from the projects home to the project", () => {
    expect(projectTrail("demo")).toEqual([
      { label: "Projects", href: "/" },
      { label: "demo", href: "/projects/demo/flags" },
    ]);
  });
});
