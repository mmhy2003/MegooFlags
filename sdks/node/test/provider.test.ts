import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ErrorCode,
  OpenFeature,
  StandardResolutionReasons,
  type Client,
} from "@openfeature/server-sdk";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import type { MegooFlagsConfig } from "../src/config.js";
import { MegooFlagsProvider } from "../src/provider.js";
import {
  cl,
  providerRuleset,
  ruleFlag,
  rulesetJson,
} from "./helpers/ruleset.js";

const KEY = "mf_server_provider-secret-value";

/** A logger that keeps what it is given. */
function recorder() {
  const lines: { level: string; message: string; args: unknown[] }[] = [];
  const at =
    (level: string) =>
    (message: string, ...args: unknown[]) =>
      lines.push({ level, message, args });
  return {
    lines,
    logger: {
      debug: at("debug"),
      info: at("info"),
      warn: at("warn"),
      error: at("error"),
    },
  };
}

let dir: string;
let bootstrapFile: string;
let domains = 0;

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "megooflags-provider-"));
  bootstrapFile = join(dir, "ruleset.json");
  writeFileSync(bootstrapFile, providerRuleset());
});

afterAll(() => rmSync(dir, { recursive: true, force: true }));

afterEach(async () => {
  await OpenFeature.close();
});

/** client registers provider under a new domain and waits for it. */
async function client(provider: MegooFlagsProvider): Promise<Client> {
  const domain = `provider-${++domains}`;
  await OpenFeature.setProviderAndWait(domain, provider);
  return OpenFeature.getClient(domain);
}

/** offline is an offline provider, by default with the bootstrap file. */
function offline(cfg: Partial<MegooFlagsConfig> = {}): MegooFlagsProvider {
  return new MegooFlagsProvider({
    sdkKey: KEY,
    offline: true,
    bootstrapFile,
    logger: recorder().logger,
    ...cfg,
  });
}

describe("MegooFlagsProvider", () => {
  it("names itself and runs on the server", () => {
    const p = offline();
    expect(p.metadata.name).toBe("MegooFlags");
    expect(p.runsOn).toBe("server");
  });

  it("resolves a boolean flag", async () => {
    const c = await client(offline());
    const pro = await c.getBooleanDetails("bool-flag", false, { plan: "pro" });
    expect(pro).toMatchObject({
      value: true,
      variant: "on",
      reason: StandardResolutionReasons.TARGETING_MATCH,
      flagMetadata: { rule_index: 0 },
    });
    expect(pro.errorCode).toBeUndefined();
    const free = await c.getBooleanDetails("bool-flag", true, {});
    expect(free).toMatchObject({
      value: false,
      variant: "off",
      reason: StandardResolutionReasons.DEFAULT,
      flagMetadata: {},
    });
  });

  it("resolves a string flag", async () => {
    const c = await client(offline());
    const d = await c.getStringDetails("string-flag", "x");
    expect(d).toMatchObject({ value: "blue", variant: "blue" });
    expect(d.reason).toBe(StandardResolutionReasons.STATIC);
  });

  it("resolves a number flag", async () => {
    const c = await client(offline());
    const d = await c.getNumberDetails("number-flag", 1);
    expect(d).toMatchObject({ value: 2.5, variant: "half" });
  });

  it("resolves an object flag", async () => {
    const c = await client(offline());
    const d = await c.getObjectDetails("json-flag", {});
    expect(d).toMatchObject({
      value: { limit: 10, tags: ["a", "b"] },
      variant: "cfg",
    });
  });

  it("maps the targeting key to the key attribute", async () => {
    const c = await client(offline());
    const alice = await c.getStringDetails("targeted", "x", {
      targetingKey: "alice",
    });
    expect(alice).toMatchObject({
      value: "hello alice",
      variant: "alice",
      reason: StandardResolutionReasons.TARGETING_MATCH,
    });
    const bob = await c.getStringValue("targeted", "x", {
      targetingKey: "bob",
      key: "alice",
    });
    expect(bob).toBe("hello");
  });

  it("returns the default with FLAG_NOT_FOUND for a missing flag", async () => {
    const c = await client(offline());
    const d = await c.getStringDetails("missing", "fallback");
    expect(d).toMatchObject({
      value: "fallback",
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.FLAG_NOT_FOUND,
    });
    expect(d.errorMessage).toContain("missing");
  });

  it("returns the default with TYPE_MISMATCH for the wrong type", async () => {
    const c = await client(offline());
    const d = await c.getBooleanDetails("string-flag", true);
    expect(d).toMatchObject({
      value: true,
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.TYPE_MISMATCH,
    });
    const n = await c.getNumberDetails("json-flag", 7);
    expect(n).toMatchObject({ value: 7, errorCode: ErrorCode.TYPE_MISMATCH });
  });

  it("passes the PREREQUISITE_FAILED reason and metadata through", async () => {
    const c = await client(offline());
    const d = await c.getBooleanDetails("needs-prereq", true);
    expect(d).toMatchObject({
      value: false,
      variant: "off",
      reason: "PREREQUISITE_FAILED",
      flagMetadata: { prerequisite: "prereq-off" },
    });
    expect(d.errorCode).toBeUndefined();
  });

  it("treats __proto__ and constructor as ordinary attributes", async () => {
    const file = join(dir, "proto.json");
    writeFileSync(
      file,
      JSON.stringify(
        rulesetJson([
          ruleFlag("proto", cl("__proto__", "in", false, "x")),
          ruleFlag("ctor", cl("constructor", "in", false, "y")),
        ]),
      ),
    );
    const c = await client(offline({ bootstrapFile: file }));
    const ctx = JSON.parse('{"__proto__": "x", "constructor": "y"}');
    expect(await c.getStringValue("proto", "d", ctx)).toBe("on");
    expect(await c.getStringValue("ctor", "d", ctx)).toBe("on");
    expect(await c.getStringValue("proto", "d", {})).toBe("off");
  });

  it("matches nothing with a date attribute", async () => {
    const c = await client(offline());
    const d = await c.getBooleanDetails("bool-flag", true, {
      plan: new Date(),
    });
    expect(d).toMatchObject({ value: false, variant: "off" });
  });

  it("is not ready without a ruleset", async () => {
    const p = offline({ bootstrapFile: join(dir, "missing.json") });
    await expect(
      p.resolveStringEvaluation("string-flag", "x", {}),
    ).resolves.toMatchObject({
      value: "x",
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.PROVIDER_NOT_READY,
    });
  });

  it("is not ready after an invalid bootstrap file", async () => {
    const file = join(dir, "invalid.json");
    writeFileSync(file, '{"schema_version": 99}');
    const log = recorder();
    const p = offline({ bootstrapFile: file, logger: log.logger });
    const domain = `provider-${++domains}`;
    await expect(OpenFeature.setProviderAndWait(domain, p)).rejects.toThrow();
    const d = await OpenFeature.getClient(domain).getStringDetails(
      "string-flag",
      "x",
    );
    expect(d).toMatchObject({
      value: "x",
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.PROVIDER_NOT_READY,
    });
    expect(log.lines.some((l) => l.level === "warn")).toBe(true);
  });

  it("evaluates the default with PROVIDER_FATAL for an invalid config", async () => {
    const log = recorder();
    const p = new MegooFlagsProvider({
      sdkKey: "mf_client_provider-secret",
      baseUrl: "http://127.0.0.1:1",
      logger: log.logger,
    });
    const direct = await p.resolveBooleanEvaluation("bool-flag", true, {});
    expect(direct).toMatchObject({
      value: true,
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.PROVIDER_FATAL,
    });
    expect(direct.errorMessage).toContain("client SDK key");

    const domain = `provider-${++domains}`;
    await expect(OpenFeature.setProviderAndWait(domain, p)).rejects.toThrow(
      "client SDK key",
    );
    const d = await OpenFeature.getClient(domain).getBooleanDetails(
      "bool-flag",
      true,
    );
    expect(d).toMatchObject({
      value: true,
      errorCode: ErrorCode.PROVIDER_FATAL,
    });
    expect(log.lines.filter((l) => l.level === "error")).toHaveLength(1);
    expect(JSON.stringify(log.lines)).not.toContain("provider-secret");
  });
});
