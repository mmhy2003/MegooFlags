import { describe, expect, it } from "vitest";

import {
  SDK_KIND_TEXT,
  credentialPill,
  credentialState,
  isInactive,
  visibleCredentials,
} from "@/lib/keys/status";

const now = new Date("2026-11-22T12:00:00Z");
const hours = (h: number) =>
  new Date(now.getTime() + h * 3_600_000).toISOString();

describe("credentialState", () => {
  it("puts revoked before an expiry", () => {
    expect(
      credentialState({ expires_at: hours(-1), revoked_at: hours(-2) }, now),
    ).toEqual({ kind: "revoked", label: "Revoked" });
  });

  it("marks a past expiry as expired", () => {
    expect(
      credentialState({ expires_at: hours(-1), revoked_at: null }, now),
    ).toEqual({ kind: "expired", label: "Expired" });
  });

  it("counts down a future expiry in the largest whole unit", () => {
    expect(
      credentialState({ expires_at: hours(3), revoked_at: null }, now),
    ).toEqual({ kind: "expiring", label: "Expires in 3 hours" });
    expect(
      credentialState({ expires_at: hours(0.5), revoked_at: null }, now),
    ).toEqual({ kind: "expiring", label: "Expires in 30 minutes" });
    expect(
      credentialState({ expires_at: hours(72), revoked_at: null }, now),
    ).toEqual({ kind: "expiring", label: "Expires in 3 days" });
  });

  it("is active with neither", () => {
    expect(
      credentialState({ expires_at: null, revoked_at: null }, now),
    ).toEqual({ kind: "active", label: "Active" });
  });
});

describe("credentialPill", () => {
  it("maps each kind to a status", () => {
    expect(credentialPill({ kind: "active", label: "Active" })).toEqual({
      status: "on",
      label: "Active",
    });
    expect(
      credentialPill({ kind: "expiring", label: "Expires in 3 hours" }),
    ).toEqual({ status: "pending", label: "Expires in 3 hours" });
    expect(credentialPill({ kind: "expired", label: "Expired" })).toEqual({
      status: "off",
      label: "Expired",
    });
    expect(credentialPill({ kind: "revoked", label: "Revoked" })).toEqual({
      status: "error",
      label: "Revoked",
    });
  });
});

describe("visibleCredentials", () => {
  const active = { id: "a", expires_at: null, revoked_at: null };
  const expiring = { id: "b", expires_at: hours(3), revoked_at: null };
  const expired = { id: "c", expires_at: hours(-1), revoked_at: null };
  const revoked = { id: "d", expires_at: null, revoked_at: hours(-1) };
  const rows = [active, expiring, expired, revoked];

  it("hides revoked and expired ones", () => {
    expect(visibleCredentials(rows, false, now)).toEqual([active, expiring]);
    expect(isInactive(expired, now)).toBe(true);
    expect(isInactive(expiring, now)).toBe(false);
  });

  it("shows everything when asked", () => {
    expect(visibleCredentials(rows, true, now)).toEqual(rows);
  });
});

describe("SDK_KIND_TEXT", () => {
  it("names both kinds", () => {
    expect(SDK_KIND_TEXT.server.label).toBe("Server");
    expect(SDK_KIND_TEXT.client.label).toBe("Client");
  });
});
