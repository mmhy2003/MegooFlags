import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { type MePermissions, navItems } from "@/lib/nav";

type Role = components["schemas"]["InstanceRole"];

type Permission = MePermissions["org"][number];

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

const keys = (
  project: string | null,
  perms: MePermissions | null,
  role: Role | null = "standalone",
) => navItems(project, perms, role).map((item) => item.key);

const admin: MePermissions = {
  org: ALL,
  project: ALL,
  environments: { production: ALL },
};

describe("navItems", () => {
  it("lists all items in order for an admin on a standalone instance", () => {
    expect(keys("demo", admin)).toEqual([
      "flags",
      "segments",
      "environments",
      "change-requests",
      "audit",
      "keys",
      "integrations",
      "settings",
    ]);
  });

  it("shows audit and keys to a viewer but not integrations or settings", () => {
    const viewer: MePermissions = {
      org: ["audit.read", "project.read", "evaluate", "sdk_key.read"],
      project: ["project.read", "audit.read"],
      environments: { production: ["sdk_key.read"] },
    };
    const result = keys("demo", viewer);
    expect(result).toContain("audit");
    expect(result).toContain("keys");
    expect(result).not.toContain("integrations");
    expect(result).not.toContain("settings");
  });

  it("hides audit without audit.read and shows integrations with project.manage", () => {
    const editor: MePermissions = {
      org: ["project.read"],
      project: ["project.read", "project.manage", "flag.update"],
      environments: { production: [] },
    };
    const result = keys("demo", editor);
    expect(result).not.toContain("audit");
    expect(result).toContain("integrations");
  });

  it("shows only the always-visible items without permissions", () => {
    expect(keys("demo", null)).toEqual([
      "flags",
      "segments",
      "environments",
      "change-requests",
    ]);
  });

  it("shows only settings without a project", () => {
    const perms: MePermissions = {
      org: ["org.manage"],
      project: null,
      environments: {},
    };
    expect(keys(null, perms)).toEqual(["settings"]);
  });

  it("shows nodes above settings on a control plane with org.manage", () => {
    expect(keys("demo", admin, "control-plane")).toEqual([
      "flags",
      "segments",
      "environments",
      "change-requests",
      "audit",
      "keys",
      "integrations",
      "nodes",
      "settings",
    ]);
    const perms: MePermissions = {
      org: ["org.manage"],
      project: null,
      environments: {},
    };
    expect(keys(null, perms, "control-plane")).toEqual(["nodes", "settings"]);
  });

  it("hides nodes without org.manage", () => {
    const perms: MePermissions = {
      org: ALL.filter((p) => p !== "org.manage"),
      project: ALL,
      environments: { production: ALL },
    };
    const result = keys("demo", perms, "control-plane");
    expect(result).not.toContain("nodes");
    expect(result).toContain("settings");
    expect(keys("demo", null, "control-plane")).not.toContain("nodes");
  });

  it("hides nodes on standalone and worker", () => {
    expect(keys("demo", admin, "standalone")).not.toContain("nodes");
    expect(keys("demo", admin, "worker")).not.toContain("nodes");
    expect(keys("demo", admin, null)).not.toContain("nodes");
  });

  it("builds project, nodes and settings hrefs", () => {
    const items = navItems("demo", admin, "control-plane");
    expect(items.find((item) => item.key === "audit")?.href).toBe(
      "/projects/demo/audit",
    );
    expect(items.find((item) => item.key === "nodes")?.href).toBe("/nodes");
    expect(items.find((item) => item.key === "settings")?.href).toBe(
      "/settings",
    );
  });
});
