import { describe, expect, it } from "vitest";

import {
  DEFAULT_OVERLAP,
  EXPIRY_CHOICES,
  OVERLAP_CHOICES,
  PERSONAL_EXPIRY_DEFAULT,
  SERVICE_EXPIRY_DEFAULT,
  expiresAt,
  expiryError,
  expiryText,
} from "@/lib/tokens/expiry";

const now = new Date("2026-11-22T12:00:00Z");
const DAY = 86_400_000;

describe("expiresAt", () => {
  it("is null for never", () => {
    expect(expiresAt({ choice: "never", date: "" }, now)).toBeNull();
  });

  it("adds the chosen period", () => {
    expect(expiresAt({ choice: "30d", date: "" }, now)).toBe(
      new Date(now.getTime() + 30 * DAY).toISOString(),
    );
    expect(expiresAt({ choice: "90d", date: "" }, now)).toBe(
      new Date(now.getTime() + 90 * DAY).toISOString(),
    );
    expect(expiresAt({ choice: "1y", date: "" }, now)).toBe(
      new Date(now.getTime() + 365 * DAY).toISOString(),
    );
  });

  it("ends a picked date at the end of that local day", () => {
    expect(expiresAt({ choice: "date", date: "2026-12-01" }, now)).toBe(
      new Date(2026, 11, 1, 23, 59, 59, 999).toISOString(),
    );
  });
});

describe("expiryError", () => {
  it("asks for a date", () => {
    expect(expiryError({ choice: "date", date: "" }, now)).toBe("Pick a date");
  });

  it("refuses a past date", () => {
    expect(expiryError({ choice: "date", date: "2026-11-01" }, now)).toBe(
      "Pick a date in the future",
    );
  });

  it("accepts a future date and the fixed periods", () => {
    expect(
      expiryError({ choice: "date", date: "2026-12-01" }, now),
    ).toBeUndefined();
    expect(expiryError({ choice: "30d", date: "" }, now)).toBeUndefined();
  });
});

describe("choices", () => {
  it("lists the expiry choices and defaults", () => {
    expect(EXPIRY_CHOICES.map((c) => c.value)).toEqual([
      "never",
      "30d",
      "90d",
      "1y",
      "date",
    ]);
    expect(PERSONAL_EXPIRY_DEFAULT).toEqual({ choice: "90d", date: "" });
    expect(SERVICE_EXPIRY_DEFAULT).toEqual({ choice: "never", date: "" });
  });

  it("lists the rotation overlaps", () => {
    expect(OVERLAP_CHOICES.map((c) => c.seconds)).toEqual([
      0, 3600, 86400, 604800, 2592000,
    ]);
    expect(DEFAULT_OVERLAP).toBe(86400);
  });
});

describe("expiryText", () => {
  const at = (ms: number) => new Date(now.getTime() + ms).toISOString();

  it("says Never without an expiry", () => {
    expect(expiryText(null, now)).toBe("Never");
  });

  it("rounds to months and years for long periods", () => {
    expect(expiryText(at(90 * DAY - 60_000), now)).toBe("Expires in 3 months");
    expect(expiryText(at(365 * DAY), now)).toBe("Expires in 1 year");
  });

  it("uses days and hours when it is closer", () => {
    expect(expiryText(at(5 * DAY), now)).toBe("Expires in 5 days");
    expect(expiryText(at(3 * 3_600_000), now)).toBe("Expires in 3 hours");
  });

  it("says how long ago it expired", () => {
    expect(expiryText(at(-2 * DAY), now)).toBe("Expired 2 days ago");
  });
});
