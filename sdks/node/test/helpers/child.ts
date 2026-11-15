// Runs an ESM script in a separate node process against the SDK sources, to
// check what keeps (or fails to keep) the event loop alive.

import { spawn } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SRC = join(ROOT, "src");

/** ChildSdk is a transpiled copy of src/ that a child process can import. */
export interface ChildSdk {
  /** dir holds the copy; dir/src/<module>.js is importable. */
  dir: string;
  remove(): void;
}

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name)) : [join(dir, e.name)],
  );
}

/**
 * transpileSdk writes src/ as JavaScript to a temporary directory, with a
 * node_modules link so the copy resolves its dependencies.
 */
export function transpileSdk(): ChildSdk {
  const dir = mkdtempSync(join(tmpdir(), "megooflags-child-"));
  for (const file of files(SRC).filter((f) => f.endsWith(".ts"))) {
    const out = join(dir, "src", relative(SRC, file)).replace(/\.ts$/, ".js");
    mkdirSync(dirname(out), { recursive: true });
    const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), {
      compilerOptions: {
        target: ts.ScriptTarget.ES2023,
        module: ts.ModuleKind.ESNext,
        verbatimModuleSyntax: true,
      },
      fileName: file,
    });
    writeFileSync(out, outputText);
  }
  writeFileSync(join(dir, "package.json"), '{"type":"module"}\n');
  symlinkSync(join(ROOT, "node_modules"), join(dir, "node_modules"), "dir");
  return { dir, remove: () => rmSync(dir, { recursive: true, force: true }) };
}

export interface ChildResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

/**
 * runChild runs script as an ES module in sdk.dir and resolves with how it
 * ended. It kills the child after timeoutMs.
 */
export function runChild(
  sdk: ChildSdk,
  script: string,
  timeoutMs = 10_000,
): Promise<ChildResult> {
  const file = join(sdk.dir, `script-${process.hrtime.bigint()}.mjs`);
  writeFileSync(file, script);
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [file], {
      cwd: sdk.dir,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => (stdout += d.toString()));
    child.stderr.on("data", (d: Buffer) => (stderr += d.toString()));
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code, stdout, stderr });
    });
  });
}
