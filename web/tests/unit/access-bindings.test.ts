import { describe, expect, it } from "vitest";

import {
  bindingText,
  formFailureText,
  isFromSso,
  reachesProject,
  scopeBody,
  scopeText,
  usedByText,
} from "@/lib/access/bindings";
import type { ApiFailure } from "@/lib/flags/errors";

const failure = (f: Partial<ApiFailure>): ApiFailure => ({
  status: 409,
  code: "unknown",
  message: "",
  path: null,
  details: null,
  ...f,
});

describe("scopeText", () => {
  it("names each level", () => {
    expect(scopeText({ scope: "org", project: null, env: null })).toBe(
      "Organization",
    );
    expect(scopeText({ scope: "project", project: "shop", env: null })).toBe(
      "Project shop",
    );
    expect(
      scopeText({ scope: "environment", project: "shop", env: "production" }),
    ).toBe("shop / production");
  });
});

describe("bindingText", () => {
  it("joins the role and the scope", () => {
    expect(
      bindingText({ role: "viewer", scope: "org", project: null, env: null }),
    ).toBe("viewer · Organization");
  });
});

describe("scopeBody", () => {
  it("drops nulls", () => {
    expect(scopeBody({ scope: "org", project: null, env: null })).toEqual({
      scope: "org",
    });
    expect(scopeBody({ scope: "project", project: "shop", env: null })).toEqual(
      { scope: "project", project: "shop" },
    );
    expect(
      scopeBody({ scope: "environment", project: "shop", env: "production" }),
    ).toEqual({ scope: "environment", project: "shop", env: "production" });
  });
});

describe("isFromSso", () => {
  it("tells SSO bindings from manual ones", () => {
    expect(isFromSso({ source: "sso:okta" })).toBe(true);
    expect(isFromSso({ source: "manual" })).toBe(false);
  });
});

describe("reachesProject", () => {
  it("is true for org scope and the same project only", () => {
    expect(
      reachesProject({ scope: "org", project: null, env: null }, "shop"),
    ).toBe(true);
    expect(
      reachesProject(
        { scope: "environment", project: "shop", env: "production" },
        "shop",
      ),
    ).toBe(true);
    expect(
      reachesProject({ scope: "project", project: "blog", env: null }, "shop"),
    ).toBe(false);
  });
});

describe("usedByText", () => {
  it("counts bindings and names SSO providers and mappings", () => {
    const f = failure({
      code: "in_use",
      message: "role 'dev' is in use",
      details: {
        used_by: [
          { kind: "binding", subject_kind: "user", subject_id: "u1" },
          { kind: "binding", subject_kind: "team", subject_id: "t1" },
          { kind: "sso_provider", provider: "okta" },
          { kind: "sso_mapping", provider: "okta", group: "devs" },
        ],
      },
    });
    expect(usedByText(f)).toBe(
      "This role is still used by: 2 bindings, SSO provider okta, group devs of okta",
    );
  });

  it("says one binding in the singular", () => {
    const f = failure({
      code: "in_use",
      details: { used_by: [{ kind: "binding" }] },
    });
    expect(usedByText(f)).toBe("This role is still used by: 1 binding");
  });

  it("falls back to the message without a usable list", () => {
    expect(usedByText(failure({ code: "in_use", message: "in use" }))).toBe(
      "in use",
    );
  });
});

describe("formFailureText", () => {
  it("shows the message of a 403", () => {
    expect(
      formFailureText(
        failure({ status: 403, code: "forbidden", message: "cannot grant x" }),
      ),
    ).toBe("cannot grant x");
  });

  it("adds the permissions of exceeds_owner_rights", () => {
    expect(
      formFailureText(
        failure({
          status: 422,
          code: "exceeds_owner_rights",
          message: "token exceeds your rights",
          details: { permissions: ["flag.toggle", "audit.read"] },
        }),
      ),
    ).toBe("token exceeds your rights: flag.toggle, audit.read");
  });

  it("explains in_use", () => {
    expect(
      formFailureText(
        failure({
          code: "in_use",
          details: { used_by: [{ kind: "sso_provider", provider: "okta" }] },
        }),
      ),
    ).toBe("This role is still used by: SSO provider okta");
  });

  it("uses failureText otherwise", () => {
    expect(
      formFailureText(
        failure({ code: "last_admin", message: "would remove last admin" }),
      ),
    ).toBe("This would leave the organization without an admin.");
  });
});
