import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { sha256Hex } from "../src/internal/sha256.js";

describe("sha256Hex", () => {
  it.each([
    [
      "the empty string",
      "",
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ],
    [
      "abc",
      "abc",
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    ],
    [
      "the 448-bit message",
      "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
      "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    ],
    [
      "one million a",
      "a".repeat(1_000_000),
      "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0",
    ],
  ])("matches the NIST vector for %s", (_, text, hex) => {
    expect(sha256Hex(text)).toBe(hex);
  });

  it.each([55, 56, 63, 64, 65])(
    "matches node:crypto around the padding boundary (%i bytes)",
    (n) => {
      const text = "x".repeat(n);
      expect(sha256Hex(text)).toBe(
        createHash("sha256").update(text, "utf8").digest("hex"),
      );
    },
  );

  it("hashes the UTF-8 bytes of non-ASCII text", () => {
    const text = 'café 日本 😀 "～"\u0001';
    expect(sha256Hex(text)).toBe(
      createHash("sha256").update(text, "utf8").digest("hex"),
    );
  });
});
