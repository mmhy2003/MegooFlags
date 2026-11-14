import { describe, expect, it } from "vitest";

import { ApiError, failureText, must, toFailure } from "@/lib/flags/errors";

describe("toFailure", () => {
  it("reads an error body without a path", () => {
    expect(
      toFailure(409, {
        code: "approval_required",
        message: "environment 'prod' requires approval; open a change request",
        details: { environment: "prod" },
      }),
    ).toEqual({
      status: 409,
      code: "approval_required",
      message: "environment 'prod' requires approval; open a change request",
      path: null,
    });
  });

  it("keeps details.path", () => {
    expect(
      toFailure(422, {
        code: "invalid_reference",
        message: "unknown segment 'beta'",
        details: { path: "rules[0].clauses[0].values[0]" },
      }).path,
    ).toBe("rules[0].clauses[0].values[0]");
  });

  it("turns a request validation location into a path", () => {
    expect(
      toFailure(422, {
        code: "validation_error",
        message: "request validation failed",
        details: [
          {
            loc: ["body", "rules", 0, "clauses", 1, "values", 0],
            msg: "Input should be a valid string",
            type: "string_type",
          },
        ],
      }),
    ).toEqual({
      status: 422,
      code: "validation_error",
      message: "Input should be a valid string",
      path: "rules[0].clauses[1].values[0]",
    });
  });

  it("drops the query part of a location", () => {
    expect(
      toFailure(422, {
        code: "validation_error",
        message: "request validation failed",
        details: [{ loc: ["query", "limit"], msg: "too big", type: "x" }],
      }).path,
    ).toBe("limit");
  });

  it("falls back for a body that is not an error body", () => {
    expect(toFailure(500, "Internal Server Error")).toEqual({
      status: 500,
      code: "unknown",
      message: "HTTP 500",
      path: null,
    });
    expect(toFailure(502, null).message).toBe("HTTP 502");
  });
});

describe("failureText", () => {
  const base = { status: 409, code: "x", message: "raw", path: null };

  it("explains the common failures", () => {
    expect(failureText({ ...base, code: "approval_required" })).toBe(
      "Needs approval",
    );
    expect(failureText({ ...base, status: 403, code: "forbidden" })).toBe(
      "You don't have permission",
    );
    expect(failureText({ ...base, code: "version_conflict" })).toBe(
      "Someone saved a newer version",
    );
    expect(failureText({ ...base, status: 404, code: "not_found" })).toBe(
      "Not found",
    );
  });

  it("uses the message otherwise", () => {
    expect(failureText({ ...base, code: "key_conflict" })).toBe("raw");
  });
});

describe("must", () => {
  it("returns the data", async () => {
    await expect(
      must(Promise.resolve({ data: 1, response: new Response(null) })),
    ).resolves.toBe(1);
  });

  it("throws an ApiError when the call failed", async () => {
    const call = must(
      Promise.resolve({
        error: { code: "version_conflict", message: "changed" },
        response: new Response(null, { status: 409 }),
      }),
    );
    await expect(call).rejects.toBeInstanceOf(ApiError);
    await expect(call).rejects.toMatchObject({
      message: "changed",
      failure: {
        status: 409,
        code: "version_conflict",
        message: "changed",
        path: null,
      },
    });
  });
});
