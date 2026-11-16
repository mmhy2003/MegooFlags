import type { components } from "@/lib/api/client";
import type { Status } from "@/components/patterns/status-pill";

type S = components["schemas"];

export type IntegrationStatus = "active" | "failing" | "paused";

/** The server sends each event at most this many times. */
const MAX_ATTEMPTS = 6;

/** Paused wins over failing: a paused integration sends nothing. */
export function integrationStatus(i: S["IntegrationOut"]): IntegrationStatus {
  if (!i.enabled) return "paused";
  if (i.last_delivery_ok === false) return "failing";
  return "active";
}

const PILLS: Record<IntegrationStatus, { status: Status; label: string }> = {
  active: { status: "on", label: "Active" },
  failing: { status: "error", label: "Failing" },
  paused: { status: "off", label: "Paused" },
};

export function statusPill(s: IntegrationStatus): {
  status: Status;
  label: string;
} {
  return PILLS[s];
}

/** A pending delivery shows the attempt it is waiting to make. */
export function deliveryLabel(d: S["DeliveryOut"]): string {
  if (d.status === "delivered") return "Delivered";
  if (d.status === "failed") return "Failed";
  const attempt = Math.min(d.attempts + 1, MAX_ATTEMPTS);
  return `Retrying (attempt ${attempt} of ${MAX_ATTEMPTS})`;
}

export function canSendAgain(d: S["DeliveryOut"]): boolean {
  return d.status === "failed";
}

/** What the receiver answered, or why there was no answer. */
export function deliveryOutcome(d: S["DeliveryOut"]): string | null {
  if (d.last_status_code !== null) return `HTTP ${d.last_status_code}`;
  return d.last_error;
}

/** A test that the server will retry still failed its one immediate attempt. */
export function testSendResult(d: S["DeliveryOut"]): {
  ok: boolean;
  message: string;
} {
  if (d.status === "delivered") return { ok: true, message: "Test delivered" };
  const reason = d.last_status_code ?? d.last_error;
  return {
    ok: false,
    message: reason === null ? "Test failed" : `Test failed: ${reason}`,
  };
}
