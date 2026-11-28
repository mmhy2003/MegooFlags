import { describe, expect, it } from "vitest";

import {
  type FlagForm,
  formatVariationValue,
  formErrors,
  KEY_PATTERN,
  parseTags,
  parseVariationValue,
  slugifyKey,
  toFlagCreate,
} from "@/lib/flags/flag-form";

const form: FlagForm = {
  key: "checkout",
  keyEdited: false,
  name: "Checkout",
  description: "",
  type: "string",
  tags: [],
  owner: "",
  variations: [
    { key: "a", name: "A", value: "blue" },
    { key: "b", name: "B", value: "green" },
  ],
  offVariation: "a",
};

describe("slugifyKey", () => {
  it("makes a valid key from a name", () => {
    expect(slugifyKey("New Checkout Flow!")).toBe("new-checkout-flow");
    expect(slugifyKey("--A.b_c")).toBe("a.b_c");
  });

  it("keeps at most 100 characters", () => {
    const key = slugifyKey("x".repeat(150));
    expect(key).toHaveLength(100);
    expect(KEY_PATTERN.test(key)).toBe(true);
  });
});

describe("parseTags", () => {
  it("splits, trims, drops empties and dedupes", () => {
    expect(parseTags(" web, beta,web,,")).toEqual(["web", "beta"]);
  });
});

describe("parseVariationValue", () => {
  it("parses numbers", () => {
    expect(parseVariationValue("number", "1.5")).toEqual({
      ok: true,
      value: 1.5,
    });
    expect(parseVariationValue("number", "x")).toEqual({
      ok: false,
      error: "Enter a number",
    });
  });

  it("parses JSON", () => {
    expect(parseVariationValue("json", '{"a": [1]}')).toEqual({
      ok: true,
      value: { a: [1] },
    });
    expect(parseVariationValue("json", "{")).toEqual({
      ok: false,
      error: "Enter valid JSON",
    });
  });

  it("keeps strings as they are", () => {
    expect(parseVariationValue("string", " a b ")).toEqual({
      ok: true,
      value: " a b ",
    });
  });
});

describe("formatVariationValue", () => {
  it("shows strings raw and the rest as JSON", () => {
    expect(formatVariationValue("string", "a b")).toBe("a b");
    expect(formatVariationValue("number", 1.5)).toBe("1.5");
    expect(formatVariationValue("json", { a: 1 })).toBe('{"a":1}');
  });
});

describe("formErrors", () => {
  it("accepts a valid form", () => {
    expect(formErrors(form)).toEqual({});
  });

  it("reports each problem", () => {
    expect(
      formErrors({
        ...form,
        type: "number",
        key: "Bad Key",
        name: "  ",
        variations: [
          { key: "a", name: "A", value: "1" },
          { key: "a", name: "A2", value: "x" },
        ],
        offVariation: "z",
      }),
    ).toEqual({
      key: expect.any(String),
      name: expect.any(String),
      "variations[1].key": expect.any(String),
      "variations[1].value": "Enter a number",
      offVariation: expect.any(String),
    });
  });
});

describe("toFlagCreate", () => {
  it("sends variations for non-boolean flags", () => {
    expect(
      toFlagCreate({ ...form, tags: ["web"], owner: "ana", name: " Co " }),
    ).toEqual({
      key: "checkout",
      name: "Co",
      description: "",
      type: "string",
      tags: ["web"],
      owner: "ana",
      variations: [
        { key: "a", name: "A", description: "", value: "blue" },
        { key: "b", name: "B", description: "", value: "green" },
      ],
      off_variation: "a",
    });
  });

  it("leaves variations to the API for boolean flags", () => {
    const create = toFlagCreate({ ...form, type: "boolean" });
    expect(create).not.toHaveProperty("variations");
    expect(create).not.toHaveProperty("off_variation");
    expect(create.owner).toBeNull();
  });
});
