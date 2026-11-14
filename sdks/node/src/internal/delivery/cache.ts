// The last-known ruleset on disk, in the Go SDK's file format so a cache
// directory can be shared between SDKs.

import { createHash, randomBytes } from "node:crypto";
import {
  chmodSync,
  closeSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  writeSync,
} from "node:fs";
import { join } from "node:path";

/** A cached ruleset: the decoded ruleset object and its ETag. */
export interface Cached {
  ruleset: unknown;
  etag: string;
}

/**
 * Cache is the file <dir>/megooflags-<first 16 hex of sha256(key)>.json
 * holding {"etag", "ruleset"}. The key itself is never written to disk.
 * Without a directory, load finds nothing and save does nothing.
 */
export class Cache {
  private readonly dir: string | undefined;
  private readonly path: string | undefined;

  constructor(dir: string | undefined, sdkKey: string) {
    if (dir === undefined || dir === "") {
      return;
    }
    const hash = createHash("sha256").update(sdkKey, "utf8").digest("hex");
    this.dir = dir;
    this.path = join(dir, `megooflags-${hash.slice(0, 16)}.json`);
  }

  /**
   * load returns the cached ruleset and ETag, or undefined when there is no
   * file. It throws for a file it cannot read or that is corrupt.
   */
  load(): Cached | undefined {
    if (this.path === undefined) {
      return undefined;
    }
    let text: string;
    try {
      text = readFileSync(this.path, "utf8");
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") {
        return undefined;
      }
      throw err;
    }
    let file: unknown;
    try {
      file = JSON.parse(text);
    } catch (err) {
      throw this.corrupt((err as Error).message);
    }
    if (typeof file !== "object" || file === null || Array.isArray(file)) {
      throw this.corrupt("not a JSON object");
    }
    const { etag = "", ruleset } = file as Record<string, unknown>;
    if (typeof etag !== "string") {
      throw this.corrupt("etag is not a string");
    }
    if (ruleset === undefined) {
      throw this.corrupt("no ruleset");
    }
    return { ruleset, etag };
  }

  /**
   * save writes the ruleset JSON text, embedded as received, and its ETag to
   * a temp file in the cache directory and renames it over the cache file,
   * so a reader never sees a partial write. It throws when raw is not JSON
   * or the write fails, and leaves no temp file behind.
   */
  save(raw: string, etag: string): void {
    if (this.dir === undefined || this.path === undefined) {
      return;
    }
    try {
      JSON.parse(raw);
    } catch (err) {
      throw new Error(
        `megooflags: cannot cache an invalid ruleset: ${(err as Error).message}`,
      );
    }
    const content = `{"etag":${JSON.stringify(etag)},"ruleset":${raw}}\n`;
    mkdirSync(this.dir, { recursive: true, mode: 0o700 });
    const tmp = join(
      this.dir,
      `.megooflags-${randomBytes(8).toString("hex")}.tmp`,
    );
    try {
      const fd = openSync(tmp, "wx", 0o600);
      try {
        writeSync(fd, content);
        fsyncSync(fd);
      } finally {
        closeSync(fd);
      }
      chmodSync(tmp, 0o600);
      renameSync(tmp, this.path);
    } catch (err) {
      rmSync(tmp, { force: true });
      throw err;
    }
  }

  private corrupt(why: string): Error {
    return new Error(`megooflags: corrupt cache file ${this.path}: ${why}`);
  }
}
