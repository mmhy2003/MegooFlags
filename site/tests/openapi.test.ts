import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Checks the built site, so it needs `pnpm build` first.
const operations = fileURLToPath(
  new URL("../dist/reference/api/operations/", import.meta.url),
);

function pages(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return pages(path);
    return entry.name === "index.html" ? [path] : [];
  });
}

describe("REST API reference", () => {
  it("has a page for GET /api/system/version", () => {
    expect(
      existsSync(operations),
      `${operations} is missing: run \`pnpm build\` before \`pnpm test\``,
    ).toBe(true);
    const found = pages(operations).filter((page) => {
      const html = readFileSync(page, "utf8");
      return html.includes("/api/system/version") && />\s*GET\s*</i.test(html);
    });
    expect(found).not.toHaveLength(0);
  });
});
