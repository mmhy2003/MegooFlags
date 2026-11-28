import { describe, expect, it } from "vitest";

import {
  type Permission,
  type PermissionInfo,
  PERMISSION_LABELS,
  grantableAt,
  groupByLevel,
  heldAt,
  worksAt,
} from "@/lib/access/permissions";
import type { MePermissions } from "@/lib/nav";

const ALL: Permission[] = [
  "org.manage",
  "member.manage",
  "role.manage",
  "token.manage",
  "project.create",
  "audit.read",
  "project.read",
  "project.manage",
  "environment.manage",
  "flag.create",
  "flag.update",
  "flag.archive",
  "segment.write",
  "flag.config.write",
  "flag.toggle",
  "change.approve",
  "evaluate",
  "sdk_key.read",
  "sdk_key.manage",
];

const catalogue: PermissionInfo[] = [
  { permission: "org.manage", level: "org" },
  { permission: "flag.toggle", level: "environment" },
  { permission: "audit.read", level: "project" },
  { permission: "member.manage", level: "org" },
  { permission: "project.read", level: "project" },
];

describe("PERMISSION_LABELS", () => {
  it("has a label for every permission", () => {
    for (const p of ALL) expect(PERMISSION_LABELS[p]).toBeTruthy();
    expect(Object.keys(PERMISSION_LABELS).sort()).toEqual([...ALL].sort());
  });
});

describe("worksAt", () => {
  it("lets a permission work at its level and wider", () => {
    expect(worksAt("environment", "org")).toBe(true);
    expect(worksAt("project", "project")).toBe(true);
  });

  it("refuses scopes narrower than the level", () => {
    expect(worksAt("org", "project")).toBe(false);
    expect(worksAt("project", "environment")).toBe(false);
  });
});

describe("groupByLevel", () => {
  it("groups in level order, keeps catalogue order and drops empty groups", () => {
    expect(groupByLevel(catalogue)).toEqual([
      { level: "org", permissions: ["org.manage", "member.manage"] },
      { level: "project", permissions: ["audit.read", "project.read"] },
      { level: "environment", permissions: ["flag.toggle"] },
    ]);
    expect(
      groupByLevel([{ permission: "flag.toggle", level: "environment" }]),
    ).toEqual([{ level: "environment", permissions: ["flag.toggle"] }]);
  });
});

describe("heldAt", () => {
  const perms: MePermissions = {
    org: ["org.manage"],
    project: ["audit.read"],
    environments: { production: ["flag.toggle"] },
  };

  it("reads the org, project and environment lists", () => {
    expect(heldAt(perms, "org")).toEqual(["org.manage"]);
    expect(heldAt(perms, "project")).toEqual(["audit.read"]);
    expect(heldAt(perms, "environment", "production")).toEqual(["flag.toggle"]);
  });

  it("gives nothing without a project or for an unknown environment", () => {
    expect(heldAt({ ...perms, project: null }, "project")).toEqual([]);
    expect(heldAt(perms, "environment", "staging")).toEqual([]);
    expect(heldAt(perms, "environment")).toEqual([]);
  });
});

describe("grantableAt", () => {
  const ordered: PermissionInfo[] = [
    { permission: "org.manage", level: "org" },
    { permission: "member.manage", level: "org" },
    { permission: "audit.read", level: "project" },
    { permission: "project.read", level: "project" },
    { permission: "flag.toggle", level: "environment" },
  ];
  const held: Permission[] = ["org.manage", "audit.read", "flag.toggle"];

  it("keeps the held permissions that work at the scope", () => {
    expect(grantableAt(ordered, held, "project")).toEqual([
      "audit.read",
      "flag.toggle",
    ]);
    expect(grantableAt(ordered, held, "environment")).toEqual(["flag.toggle"]);
    expect(grantableAt(ordered, held, "org")).toEqual(held);
  });

  it("follows the catalogue order, not the held order", () => {
    expect(grantableAt(ordered, ["flag.toggle", "org.manage"], "org")).toEqual([
      "org.manage",
      "flag.toggle",
    ]);
  });
});
