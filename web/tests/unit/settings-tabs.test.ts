import { describe, expect, it } from "vitest";

import { SETTINGS_TABS, pickTab, visibleTabs } from "@/lib/settings/tabs";

describe("visibleTabs", () => {
  it("always shows teams and roles", () => {
    expect(visibleTabs([])).toEqual(["teams", "roles"]);
  });

  it("adds the audit log for audit.read", () => {
    expect(visibleTabs(["audit.read", "project.read"])).toEqual([
      "teams",
      "roles",
      "audit",
    ]);
  });

  it("shows every tab in order with every permission", () => {
    expect(
      visibleTabs([
        "audit.read",
        "token.manage",
        "org.manage",
        "member.manage",
        "role.manage",
      ]),
    ).toEqual([...SETTINGS_TABS]);
  });
});

describe("pickTab", () => {
  it("falls back to the first visible tab", () => {
    expect(pickTab("sso", ["teams", "roles"])).toBe("teams");
    expect(pickTab(null, ["members", "teams"])).toBe("members");
    expect(pickTab("nope", ["teams"])).toBe("teams");
  });

  it("keeps a visible tab", () => {
    expect(pickTab("roles", ["teams", "roles"])).toBe("roles");
  });
});
