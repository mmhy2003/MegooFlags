import { describe, expect, it } from "vitest";

import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";

const perms: MePermissions = {
  org: [],
  project: ["project.read", "flag.create"],
  environments: { dev: ["flag.toggle"], prod: [] },
};

describe("can", () => {
  it("allows nothing without permissions", () => {
    expect(can(null, "flag.create")).toBe(false);
    expect(can(null, "flag.toggle", "dev")).toBe(false);
  });

  it("uses project permissions without an environment", () => {
    expect(can(perms, "flag.create")).toBe(true);
    expect(can(perms, "flag.toggle")).toBe(false);
    expect(can({ ...perms, project: null }, "flag.create")).toBe(false);
  });

  it("uses environment permissions with an environment", () => {
    expect(can(perms, "flag.toggle", "dev")).toBe(true);
    expect(can(perms, "flag.toggle", "prod")).toBe(false);
    expect(can(perms, "flag.create", "dev")).toBe(false);
  });

  it("allows nothing in an unknown environment", () => {
    expect(can(perms, "flag.toggle", "gone")).toBe(false);
  });
});
