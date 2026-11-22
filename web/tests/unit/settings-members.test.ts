import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { linkKind, memberPill, memberState } from "@/lib/settings/members";

type User = components["schemas"]["UserOut"];

const now = new Date("2026-11-22T12:00:00Z");

function user(over: Partial<User> = {}): User {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    email: "ada@example.com",
    name: "Ada",
    status: "active",
    has_password: true,
    last_login_at: "2026-11-01T00:00:00Z",
    created_at: "2026-10-01T00:00:00Z",
    invitation_expires_at: null,
    ...over,
  };
}

describe("memberState", () => {
  it("puts disabled first", () => {
    expect(
      memberState(
        user({
          status: "disabled",
          invitation_expires_at: "2026-11-30T00:00:00Z",
        }),
        now,
      ),
    ).toEqual({ kind: "disabled", label: "Disabled" });
  });

  it("shows a pending invitation", () => {
    expect(
      memberState(user({ invitation_expires_at: "2026-11-30T00:00:00Z" }), now)
        .kind,
    ).toBe("invited");
  });

  it("shows an expired invitation", () => {
    expect(
      memberState(user({ invitation_expires_at: "2026-11-01T00:00:00Z" }), now)
        .kind,
    ).toBe("invite-expired");
  });

  it("is active otherwise", () => {
    expect(memberState(user(), now)).toEqual({
      kind: "active",
      label: "Active",
    });
  });
});

describe("memberPill", () => {
  it("maps each kind to a status", () => {
    expect(memberPill({ kind: "active", label: "Active" }).status).toBe("on");
    expect(memberPill({ kind: "invited", label: "Invited" }).status).toBe(
      "pending",
    );
    expect(
      memberPill({ kind: "invite-expired", label: "Invitation expired" })
        .status,
    ).toBe("stale");
    expect(memberPill({ kind: "disabled", label: "Disabled" }).status).toBe(
      "off",
    );
  });
});

describe("linkKind", () => {
  it("invites someone who never joined", () => {
    expect(linkKind(user({ has_password: false, last_login_at: null }))).toBe(
      "invite",
    );
  });

  it("resets for everyone else", () => {
    expect(linkKind(user())).toBe("reset");
    expect(linkKind(user({ has_password: false }))).toBe("reset");
    expect(linkKind(user({ last_login_at: null }))).toBe("reset");
  });
});
