import { describe, expect, it } from "vitest";

import { updateLabel, updateTooltip, versionLabel } from "@/lib/version";

describe("version labels", () => {
  it("prefixes the running version with v", () => {
    expect(versionLabel("1.0.0")).toBe("v1.0.0");
  });

  it("names the newer release on the pill", () => {
    expect(updateLabel({ version: "1.1.0" })).toBe("Update: 1.1.0");
  });

  it("explains the collapsed pill in its tooltip", () => {
    expect(updateTooltip({ version: "1.1.0" })).toBe("Update available: 1.1.0");
  });
});
