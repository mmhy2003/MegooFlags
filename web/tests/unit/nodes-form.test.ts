import { describe, expect, it } from "vitest";

import {
  DEFAULT_OVERLAP,
  nameError,
  OVERLAPS,
  submitError,
} from "@/lib/nodes/form";
import { ApiError } from "@/lib/flags/errors";

const HINT = "Use letters, digits, dots, dashes or underscores";

describe("nameError", () => {
  it.each(["worker-1", "Edge.eu_2", "a", "9", "a".repeat(100)])(
    "accepts %j",
    (name) => {
      expect(nameError(name)).toBeUndefined();
    },
  );

  it("asks for a name when it is empty", () => {
    expect(nameError("")).toBe("Enter a name");
    expect(nameError("   ")).toBe("Enter a name");
  });

  it.each(["-worker", ".a", "_a", "has space", "wörker", "a/b"])(
    "refuses %j",
    (name) => {
      expect(nameError(name)).toBe(HINT);
    },
  );

  it("refuses names over 100 characters", () => {
    expect(nameError("a".repeat(101))).toBe("Use at most 100 characters");
  });
});

describe("submitError", () => {
  const failure = (status: number, code: string) =>
    new ApiError({ status, code, message: "nope", path: null });

  it("puts a name conflict on the field", () => {
    expect(submitError(failure(409, "key_conflict"))).toEqual({
      field: "A node with this name already exists",
    });
  });

  it("puts a 422 on the field with its message", () => {
    expect(submitError(failure(422, "validation_error"))).toEqual({
      field: "nope",
    });
  });

  it("toasts anything else", () => {
    expect(submitError(failure(500, "internal"))).toEqual({
      toast: expect.any(String),
    });
    expect(submitError(new Error("offline"))).toEqual({
      toast: "Couldn't reach the server",
    });
  });
});

describe("OVERLAPS", () => {
  it("offers the overlaps in order with one day as the default", () => {
    expect(OVERLAPS).toEqual([
      { seconds: 0, label: "Revoke now" },
      { seconds: 3600, label: "1 hour" },
      { seconds: 86400, label: "1 day" },
      { seconds: 604800, label: "7 days" },
    ]);
    expect(DEFAULT_OVERLAP).toBe(86400);
  });
});
