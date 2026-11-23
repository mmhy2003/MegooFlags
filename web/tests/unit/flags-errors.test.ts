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
      details: { environment: "prod" },
    });
  });

  it("keeps object details", () => {
    const details = { used_by: [{ kind: "binding", id: "b1" }] };
    expect(
      toFailure(409, { code: "role_in_use", message: "in use", details })
        .details,
    ).toEqual(details);
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
      details: [
        {
          loc: ["body", "rules", 0, "clauses", 1, "values", 0],
          msg: "Input should be a valid string",
          type: "string_type",
        },
      ],
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
      details: null,
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

  it("explains the governance failures", () => {
    expect(failureText({ ...base, code: "last_admin" })).toBe(
      "This would leave the organization without an admin.",
    );
    expect(failureText({ ...base, code: "key_inactive" })).toBe(
      "This key is no longer active",
    );
    expect(failureText({ ...base, status: 403, code: "self_approval" })).toBe(
      "You can't review your own change request",
    );
    expect(failureText({ ...base, code: "public_url_required" })).toBe(
      "Set MEGOOFLAGS_PUBLIC_URL first",
    );
  });

  it("lists the permissions a token can't hold", () => {
    expect(
      failureText({
        ...base,
        status: 422,
        code: "exceeds_owner_rights",
        message: "a personal token cannot hold permissions you do not hold",
        details: { permissions: ["flag.toggle", "change.approve"] },
      }),
    ).toBe(
      "a personal token cannot hold permissions you do not hold: flag.toggle, change.approve",
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
        details: null,
      },
    });
  });
});
