import { describe, expect, it } from "vitest";

import {
  actionLabel,
  AUDIT_ACTIONS,
  RESOURCE_TYPES,
  resourceHref,
  snapshotText,
} from "@/lib/audit/actions";
import type { components } from "@/lib/api/schema";
import { changeRequestPath } from "@/lib/routes";

type Entry = components["schemas"]["AuditEntryOut"];

function entry(e: Partial<Entry>): Entry {
  return {
    id: 1,
    action: "flag.toggle",
    actor: {
      kind: "user",
      id: null,
      label: "ana@example.com",
      auth_method: null,
    },
    before: null,
    after: null,
    environment: null,
    project: "shop",
    ip: null,
    user_agent: null,
    request_id: null,
    occurred_at: "2026-11-01T10:00:00Z",
    resource_type: "flag",
    resource_key: "f",
    ...e,
  } as Entry;
}

describe("AUDIT_ACTIONS", () => {
  it("is sorted without duplicates", () => {
    expect([...AUDIT_ACTIONS].sort()).toEqual(AUDIT_ACTIONS);
    expect(new Set(AUDIT_ACTIONS).size).toBe(AUDIT_ACTIONS.length);
  });

  it("holds the actions the API records", () => {
    expect(AUDIT_ACTIONS).toEqual(
      expect.arrayContaining([
        "flag.config.update",
        "auth.sign_in.failed",
        "change_request.apply",
      ]),
    );
  });
});

describe("RESOURCE_TYPES", () => {
  it("is sorted without duplicates", () => {
    expect([...RESOURCE_TYPES].sort()).toEqual(RESOURCE_TYPES);
    expect(new Set(RESOURCE_TYPES).size).toBe(RESOURCE_TYPES.length);
    expect(RESOURCE_TYPES).toEqual(
      expect.arrayContaining(["flag", "change_request", "user", "variation"]),
    );
  });
});

describe("actionLabel", () => {
  it("names known actions", () => {
    expect(actionLabel("flag.config.update")).toBe("Targeting changed");
    expect(actionLabel("auth.sign_in.failed")).toBe("Sign-in failed");
    expect(actionLabel("change_request.approve")).toBe(
      "Change request approved",
    );
  });

  it("has a label for every recorded action", () => {
    const humanize = (a: string) => {
      const words = a.replace(/[._]/g, " ");
      return words.charAt(0).toUpperCase() + words.slice(1);
    };
    expect(AUDIT_ACTIONS.filter((a) => actionLabel(a) === humanize(a))).toEqual(
      [],
    );
  });

  it("humanizes unknown actions", () => {
    expect(actionLabel("widget.member.add")).toBe("Widget member add");
    expect(actionLabel("widget.rotate_secret")).toBe("Widget rotate secret");
  });
});

describe("resourceHref", () => {
  it("links a flag in its environment", () => {
    expect(resourceHref(entry({ environment: "production" }))).toBe(
      "/projects/shop/flags/f?env=production",
    );
  });

  it("links a flag without an environment", () => {
    expect(resourceHref(entry({}))).toBe("/projects/shop/flags/f");
  });

  it("links a change request", () => {
    expect(
      resourceHref(
        entry({ resource_type: "change_request", resource_key: "cr-1" }),
      ),
    ).toBe(changeRequestPath("shop", "cr-1"));
  });

  it("does not link other resources or entries without a project", () => {
    expect(
      resourceHref(
        entry({ resource_type: "user", resource_key: "ana@example.com" }),
      ),
    ).toBeNull();
    expect(resourceHref(entry({ project: null }))).toBeNull();
  });
});

describe("snapshotText", () => {
  it("is empty without a snapshot", () => {
    expect(snapshotText(null)).toBe("");
  });

  it("is indented JSON", () => {
    expect(snapshotText({ on: true })).toBe('{\n  "on": true\n}');
  });
});
