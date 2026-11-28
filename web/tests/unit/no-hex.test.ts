import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const DIRS = ["app", "components", "lib", "hooks"];
const SKIP = new Set(["app/globals.css", "lib/api"]);
const EXTENSIONS = [".ts", ".tsx", ".css"];

const HEX = /(?<![\w&])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])/i;
const RGB = /\brgba?\(/;

function hasRawColour(line: string): boolean {
  return HEX.test(line) || RGB.test(line);
}

function sourceFiles(dir: string): string[] {
  const rel = relative(ROOT, dir).split("\\").join("/");
  if (SKIP.has(rel) || !existsSync(dir)) return [];
  if (statSync(dir).isFile())
    return EXTENSIONS.some((ext) => dir.endsWith(ext)) ? [dir] : [];
  return readdirSync(dir).flatMap((name) => sourceFiles(join(dir, name)));
}

describe("colours come from tokens", () => {
  it("matches raw colours but not fragment links", () => {
    expect(hasRawColour("color: #ff00aa")).toBe(true);
    expect(hasRawColour("background: rgba(0, 0, 0, 0.5)")).toBe(true);
    expect(hasRawColour("href='#main'")).toBe(false);
    expect(hasRawColour("&#039;")).toBe(false);
  });

  it("has no hex or rgb colours outside globals.css", () => {
    const hits = DIRS.flatMap((dir) => sourceFiles(join(ROOT, dir))).flatMap(
      (file) =>
        readFileSync(file, "utf8")
          .split("\n")
          .flatMap((line, index) =>
            hasRawColour(line)
              ? [`${relative(ROOT, file)}:${index + 1}: ${line.trim()}`]
              : [],
          ),
    );
    expect(hits).toEqual([]);
  });
});
