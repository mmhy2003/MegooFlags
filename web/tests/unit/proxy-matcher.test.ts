import { describe, expect, it } from "vitest";

import { config } from "@/proxy";

const matcher = new RegExp("^" + config.matcher[0] + "$");

describe("proxy matcher", () => {
  it("skips the favicon and the API", () => {
    expect(matcher.test("/icon.svg")).toBe(false);
    expect(matcher.test("/api/x")).toBe(false);
  });

  it("runs on pages", () => {
    expect(matcher.test("/sign-in")).toBe(true);
    expect(matcher.test("/projects/demo/flags")).toBe(true);
  });
});
