import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));

function read(path: string): string {
  return readFileSync(`${ROOT}${path}`, "utf8");
}

// Visual baselines only match when every runner uses the same browser build.
describe("Playwright version is pinned everywhere", () => {
  const version: string = JSON.parse(read("package.json")).devDependencies[
    "@playwright/test"
  ];

  it("is an exact version", () => {
    expect(version).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("matches the CI container image", () => {
    expect(read("../.github/workflows/ci.yml")).toContain(
      `mcr.microsoft.com/playwright:v${version}-noble`,
    );
  });

  it("matches e2e/docker.sh", () => {
    expect(read("e2e/docker.sh")).toContain(`PW_VERSION="${version}"`);
  });
});
