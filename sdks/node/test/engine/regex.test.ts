import { describe, expect, it } from "vitest";
import {
  compilePortable,
  isCompileError,
  type PortableRegex,
} from "../../src/internal/engine/regex.js";

const LF = "\n";
const CR = "\r";
const VT = "\v";

function compiled(pattern: string): PortableRegex {
  const r = compilePortable(pattern);
  if (isCompileError(r)) throw new Error(`${pattern}: ${r.reason}`);
  return r;
}

// Python re with re.ASCII semantics.
const vectors: [pattern: string, subject: string, match: boolean][] = [
  ["b", "abc", true],
  ["b", "xyz", false],
  ["^ab$", "ab", true],
  ["^ab$", "abc", false],
  ["^ab$", "xab", false],
  ["^ab$", "ab" + LF, true],
  ["^ab$", "ab" + LF + LF, false],
  ["^ab$", "ab" + CR, false],
  [String.raw`\d`, "a3", true],
  [String.raw`\d`, "٣", false],
  ["(?i)k", "K", true],
  ["(?i)k", "K", false],
  ["(?i)^[s]$", "S", true],
  ["(?i)^[s]$", "ſ", false],
  ["(?i)^[j-l]$", "K", true],
  ["(?i)^[j-l]$", "K", false],
  ["(?i)é", "é", true],
  ["(?i)é", "É", false],
  ["(?i)[^a]", "A", false],
  ["(?i)[^a]", "b", true],
  ["^(?i:ab)c$", "ABc", true],
  ["^(?i:ab)c$", "ABC", false],
  ["(?im)^B$", "a" + LF + "b", true],
  [String.raw`^\s$`, VT, true],
  [String.raw`^\s$`, " ", true],
  [String.raw`^\s$`, " ", false],
  [String.raw`^\s$`, "\u0085", false],
  [String.raw`^\s$`, " ", false],
  [String.raw`^\S$`, VT, false],
  [String.raw`^\S$`, " ", true],
  [String.raw`^[\s]$`, VT, true],
  [String.raw`^[^\d]$`, "x", true],
  [String.raw`^[^\d]$`, "5", false],
  [String.raw`^[\w-]$`, "-", true],
  [String.raw`^[\w-]$`, "a", true],
  [String.raw`^[\w-]$`, "é", false],
  [String.raw`^\w+$`, "a_1", true],
  [String.raw`^\w+$`, "é", false],
  [String.raw`a\bé`, "aé", true],
  [String.raw`a\Bé`, "aé", false],
  [String.raw`\bfoo\b`, "a foo b", true],
  [String.raw`\bfoo\b`, "afoo", false],
  ["^.$", CR, true],
  ["^.$", LF, false],
  ["^.$", " ", true],
  ["^.$", "\u{1F600}", true],
  ["^..$", "\u{1F600}", false],
  ["^[😀a]$", "\u{1F600}", true],
  ["^[😀a]$", "a", true],
  ["^a.b$", "a" + LF + "b", false],
  ["(?s)^a.b$", "a" + LF + "b", true],
  ["^b$", "a" + LF + "b" + LF + "c", false],
  ["(?m)^b$", "a" + LF + "b" + LF + "c", true],
  ["(?m)a$", "a" + LF + "b", true],
  ["(?m)a$", "a" + CR + LF + "b", false],
  ["(?m)^$", "a" + LF + LF + "b", true],
  [String.raw`(?m)\Ab`, "b" + LF + "a", true],
  [String.raw`(?m)\Ab`, "a" + LF + "b", false],
  ["^$", "", true],
  ["^$", LF, true],
  ["", "", true],
  ["", "x", true],
  ["^(?P<x>ab)+c$", "ababc", true],
  ["^(?:a|b|)$", "", true],
  ["^(?:a|b|)$", "c", false],
  ["^a{2,3}$", "aa", true],
  ["^a{2,3}$", "aaaa", false],
  ["^a{2,}$", "aaaaa", true],
  ["^a*?$", "aaa", true],
  ["x{", "x{", true],
  ["x{a}", "x{a}", true],
  [String.raw`^\x41\t\.$`, "A\t.", true],
  ["^[a[]$", "[", true],
  ["^[a[]$", "a", true],
  ["^[a[]$", "b", false],
  ["^[a&&b]$", "&", true],
  [String.raw`^[a\-z]$`, "-", true],
  [String.raw`^[a\-z]$`, "b", false],
  ["^[a-]$", "-", true],
  ["^[]a]$", "]", true],
  ["^[^]a]$", "b", true],
  ["^[^]a]$", "]", false],
  ["^[a-c-e]$", "-", true],
  ["^[a-c-e]$", "d", false],
  [String.raw`^a\|b$`, "a|b", true],
];

const refused: string[] = [
  "(?=a)",
  "(?!a)",
  "(?<=a)b",
  "(?<!a)b",
  String.raw`(a)\1`,
  "(?P<n>a)(?P=n)",
  "(?P<n>a)(?P<n>b)",
  "(?>a)",
  "(?(1)a|b)",
  "(?<n>a)",
  "(?#c)a",
  "a*+",
  "a++",
  "a?+",
  "a**",
  "a{2}{3}",
  "*a",
  "(*a)",
  "a|*",
  "^*",
  String.raw`\b+`,
  "a{,2}",
  "a{3,2}",
  "a{1001}",
  String.raw`\z`,
  String.raw`\Z`,
  String.raw`\pL`,
  String.raw`\x{41}`,
  String.raw`\N{LATIN CAPITAL LETTER A}`,
  String.raw`\e`,
  String.raw`\q`,
  String.raw`\0`,
  "\\",
  "(?x)a",
  "(?U)a",
  "(?a)a",
  "a(?i)b",
  "(?-i:a)",
  "[[:alpha:]]",
  String.raw`[a-\d]`,
  String.raw`[\w-a]`,
  "[z-a]",
  String.raw`[\b]`,
  "[]",
  "[a",
  "(",
  ")",
  "(a",
];

describe("compilePortable", () => {
  it.each(vectors)("%j against %j is %j", (pattern, subject, match) => {
    expect(compiled(pattern).test(subject)).toBe(match);
  });

  it.each(refused)("refuses %j", (pattern) => {
    const r = compilePortable(pattern);
    expect(isCompileError(r)).toBe(true);
    if (isCompileError(r)) expect(r.reason).not.toBe("");
  });

  it("builds the RegExp with the single flag u", () => {
    for (const [pattern] of vectors) {
      expect(compiled(pattern).flags).toBe("u");
    }
  });

  it.each([
    ["^a$", String.raw`(?<![^])\u{61}(?=\u{A}?(?![^]))`],
    ["(?m)^a$", String.raw`(?:(?<![^])|(?<=\u{A}))\u{61}(?=\u{A}|(?![^]))`],
    [
      String.raw`\bx`,
      String.raw`(?:(?<=[\u{30}-\u{39}\u{41}-\u{5A}\u{5F}\u{61}-\u{7A}])(?![\u{30}-\u{39}\u{41}-\u{5A}\u{5F}\u{61}-\u{7A}])|(?<![\u{30}-\u{39}\u{41}-\u{5A}\u{5F}\u{61}-\u{7A}])(?=[\u{30}-\u{39}\u{41}-\u{5A}\u{5F}\u{61}-\u{7A}]))\u{78}`,
    ],
    ["(?i)[a-c]", String.raw`[\u{41}-\u{43}\u{61}-\u{63}]`],
  ])("emits %j as %s", (pattern, source) => {
    expect(compiled(pattern).source).toBe(source);
  });

  it("repeats an empty set without a host error", () => {
    const r = compiled(String.raw`^[^\s\S]*$`);
    expect(r.test("")).toBe(true);
    expect(r.test("a")).toBe(false);
  });
});
