import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/client";
import {
  canSendAgain,
  deliveryLabel,
  integrationStatus,
  statusPill,
} from "@/lib/integrations/status";

type S = components["schemas"];

const integration: S["IntegrationOut"] = {
  id: "00000000-0000-0000-0000-000000000001",
  kind: "webhook",
  name: "Deploys",
  url_hint: "https://example.com/…",
  event_groups: ["flags"],
  all_environments: true,
  environments: [],
  enabled: true,
  last_delivery_at: null,
  last_delivery_ok: null,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};

const delivery: S["DeliveryOut"] = {
  id: "00000000-0000-0000-0000-000000000002",
  event_id: "evt_1",
  event_type: "flag.toggle",
  status: "delivered",
  attempts: 1,
  created_at: "2026-11-01T00:00:00Z",
  delivered_at: "2026-11-01T00:00:01Z",
  duration_ms: 12,
  last_error: null,
  last_status_code: 200,
  next_attempt_at: null,
  request_body: {},
  response_body: "ok",
};

describe("integrationStatus", () => {
  it.each([
    [true, null, "active"],
    [true, true, "active"],
    [true, false, "failing"],
    [false, null, "paused"],
    [false, false, "paused"],
  ] as const)("enabled %s, last ok %s is %s", (enabled, ok, status) => {
    expect(
      integrationStatus({
        ...integration,
        enabled,
        last_delivery_ok: ok,
      }),
    ).toBe(status);
  });
});

describe("statusPill", () => {
  it.each([
    ["active", { status: "on", label: "Active" }],
    ["failing", { status: "error", label: "Failing" }],
    ["paused", { status: "off", label: "Paused" }],
  ] as const)("maps %s", (s, pill) => {
    expect(statusPill(s)).toEqual(pill);
  });
});

describe("deliveryLabel", () => {
  it.each([
    ["delivered", 1, "Delivered"],
    ["pending", 0, "Retrying (attempt 1 of 6)"],
    ["pending", 2, "Retrying (attempt 3 of 6)"],
    ["pending", 6, "Retrying (attempt 6 of 6)"],
    ["failed", 6, "Failed"],
  ] as const)("%s after %i attempts is %s", (status, attempts, label) => {
    expect(deliveryLabel({ ...delivery, status, attempts })).toBe(label);
  });
});

describe("canSendAgain", () => {
  it.each([
    ["delivered", false],
    ["pending", false],
    ["failed", true],
  ] as const)("%s is %s", (status, can) => {
    expect(canSendAgain({ ...delivery, status })).toBe(can);
  });
});
