import { createHash } from "node:crypto";
import {
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Cache } from "../../src/internal/delivery/cache.js";

const KEY = "mf_server_secret-key-123";
const RAW = '{"project":"p","environment":"e","flags":{},"segments":{}}';
const posix = process.platform !== "win32";

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "megooflags-cache-"));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

const fileName = (key: string) =>
  `megooflags-${createHash("sha256").update(key, "utf8").digest("hex").slice(0, 16)}.json`;

describe("Cache", () => {
  it("round-trips the ruleset and the ETag", () => {
    const c = new Cache(dir, KEY);
    c.save(RAW, '"v1"');
    expect(c.load()).toEqual({ ruleset: JSON.parse(RAW), etag: '"v1"' });
  });

  it("writes {etag, ruleset} with the ruleset embedded as received", () => {
    new Cache(dir, KEY).save(RAW, '"v1"');
    const text = readFileSync(join(dir, fileName(KEY)), "utf8");
    expect(text).toContain(`"ruleset":${RAW}`);
    expect(JSON.parse(text)).toEqual({
      etag: '"v1"',
      ruleset: JSON.parse(RAW),
    });
  });

  it("reads a file written by the Go SDK", () => {
    writeFileSync(
      join(dir, fileName(KEY)),
      `{"etag":"\\"g\\"","ruleset":${RAW}}\n`,
    );
    expect(new Cache(dir, KEY).load()).toEqual({
      ruleset: JSON.parse(RAW),
      etag: '"g"',
    });
  });

  it("names the file after 16 hex characters of the key hash", () => {
    new Cache(dir, KEY).save("{}", "x");
    const entries = readdirSync(dir);
    expect(entries).toEqual([fileName(KEY)]);
    expect(entries[0]).toMatch(/^megooflags-[0-9a-f]{16}\.json$/);
    expect(entries[0]).not.toContain(KEY);
    expect(readFileSync(join(dir, entries[0] ?? ""), "utf8")).not.toContain(
      KEY,
    );
    expect(fileName("another-key")).not.toBe(fileName(KEY));
  });

  it("hashes the key as UTF-8", () => {
    new Cache(dir, "mf_server_é").save("{}", "x");
    expect(readdirSync(dir)).toEqual([fileName("mf_server_é")]);
  });

  it.runIf(posix)("writes the file with mode 0600", () => {
    new Cache(dir, KEY).save("{}", "x");
    expect(statSync(join(dir, fileName(KEY))).mode & 0o777).toBe(0o600);
  });

  it("replaces the file atomically and leaves no temp file", () => {
    const c = new Cache(dir, KEY);
    c.save('{"n":1}', "1");
    c.save('{"n":2}', "2");
    expect(c.load()).toEqual({ ruleset: { n: 2 }, etag: "2" });
    expect(readdirSync(dir)).toEqual([fileName(KEY)]);
  });

  it("creates the directory", () => {
    const nested = join(dir, "a", "b");
    const c = new Cache(nested, KEY);
    c.save("{}", "x");
    expect(c.load()).toEqual({ ruleset: {}, etag: "x" });
    if (posix) {
      expect(statSync(nested).mode & 0o777).toBe(0o700);
    }
  });

  it("refuses invalid JSON and writes nothing", () => {
    const c = new Cache(dir, KEY);
    expect(() => c.save("{not json", "x")).toThrow();
    expect(readdirSync(dir)).toEqual([]);
  });

  it("load of a missing file is not found", () => {
    expect(new Cache(dir, KEY).load()).toBeUndefined();
  });

  it.each([
    '{"etag": "x", "ruleset": ',
    "[]",
    '{"etag": 1, "ruleset": {}}',
    '{"etag": "x"}',
  ])("load of corrupt content %j throws", (content) => {
    writeFileSync(join(dir, fileName(KEY)), content);
    expect(() => new Cache(dir, KEY).load()).toThrow(/corrupt cache file/);
  });

  it("load of an unreadable path throws", () => {
    const c = new Cache(join(dir, fileName(KEY)), KEY);
    writeFileSync(join(dir, fileName(KEY)), "x");
    expect(() => c.load()).toThrow();
  });

  it("without a directory, load is not found and save does nothing", () => {
    const c = new Cache(undefined, KEY);
    expect(c.load()).toBeUndefined();
    expect(() => c.save("{}", "x")).not.toThrow();
  });
});
