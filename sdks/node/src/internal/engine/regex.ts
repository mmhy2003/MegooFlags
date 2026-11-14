// Portable regex translator: the subset common to RE2 and Python re, with
// Python re.ASCII semantics, rewritten into explicit JS constructs so that no
// host default matters. Three stages: parse (recursive descent over code
// points), an AST whose sets are sorted merged code point ranges, and the
// emitter. The RegExp uses the single flag u; i, m and s are never used.

/** A compiled pattern. test is an unanchored search. */
export interface PortableRegex {
  readonly source: string;
  readonly flags: string;
  test(subject: string): boolean;
}

/** A pattern outside the portable subset, or one the host refused. */
export interface CompileError {
  readonly reason: string;
}

export function isCompileError(
  r: PortableRegex | CompileError,
): r is CompileError {
  return "reason" in r;
}

// ---- Sets: sorted, merged, inclusive code point ranges ----

type Range = readonly [lo: number, hi: number];

const MAX_CP = 0x10ffff;

function normalize(ranges: Range[]): Range[] {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0]);
  const out: [number, number][] = [];
  for (const [lo, hi] of sorted) {
    const last = out[out.length - 1];
    if (last !== undefined && lo <= last[1] + 1) {
      if (hi > last[1]) last[1] = hi;
    } else {
      out.push([lo, hi]);
    }
  }
  return out;
}

function complement(ranges: Range[]): Range[] {
  const out: Range[] = [];
  let next = 0;
  for (const [lo, hi] of ranges) {
    if (lo > next) out.push([next, lo - 1]);
    next = hi + 1;
  }
  if (next <= MAX_CP) out.push([next, MAX_CP]);
  return out;
}

/** foldAscii adds the other case of every ASCII letter in the set. */
function foldAscii(ranges: Range[]): Range[] {
  const extra: Range[] = [];
  for (const [lo, hi] of ranges) {
    for (const [a, z, shift] of [
      [0x41, 0x5a, 0x20],
      [0x61, 0x7a, -0x20],
    ] as const) {
      const l = Math.max(lo, a);
      const h = Math.min(hi, z);
      if (l <= h) extra.push([l + shift, h + shift]);
    }
  }
  return normalize([...ranges, ...extra]);
}

const DIGIT: Range[] = [[0x30, 0x39]];
const WORD: Range[] = [
  [0x30, 0x39],
  [0x41, 0x5a],
  [0x5f, 0x5f],
  [0x61, 0x7a],
];
const SPACE: Range[] = [
  [0x09, 0x0d],
  [0x20, 0x20],
];

const SHORTHANDS: Record<string, Range[]> = {
  d: DIGIT,
  D: complement(DIGIT),
  w: WORD,
  W: complement(WORD),
  s: SPACE,
  S: complement(SPACE),
};

const CONTROL_ESCAPES: Record<string, number> = {
  t: 0x09,
  n: 0x0a,
  r: 0x0d,
  f: 0x0c,
  v: 0x0b,
  a: 0x07,
};

// ---- AST ----

type AssertKind =
  "START" | "END_DOLLAR" | "LINE_START" | "LINE_END" | "WORD_B" | "NOT_WORD_B";

type Node =
  | { kind: "set"; ranges: Range[] }
  | { kind: "concat"; items: Node[] }
  | { kind: "alt"; alternatives: Node[] }
  | { kind: "group"; child: Node }
  | {
      kind: "repeat";
      child: Node;
      min: number;
      max: number | null;
      lazy: boolean;
    }
  | { kind: "assert"; assert: AssertKind };

interface Flags {
  readonly i: boolean;
  readonly m: boolean;
  readonly s: boolean;
}

// ---- Stage 1: parse ----

class ParseError extends Error {}

function isAsciiAlnum(c: number): boolean {
  return (
    (c >= 0x30 && c <= 0x39) ||
    (c >= 0x41 && c <= 0x5a) ||
    (c >= 0x61 && c <= 0x7a)
  );
}

function isDigit(c: number | undefined): c is number {
  return c !== undefined && c >= 0x30 && c <= 0x39;
}

function hexValue(c: number | undefined): number {
  if (c === undefined) return -1;
  if (c >= 0x30 && c <= 0x39) return c - 0x30;
  if (c >= 0x41 && c <= 0x46) return c - 0x41 + 10;
  if (c >= 0x61 && c <= 0x66) return c - 0x61 + 10;
  return -1;
}

const MAX_REPEAT = 1000;

class Parser {
  private readonly cps: number[];
  private pos = 0;
  private readonly names = new Set<string>();

  constructor(pattern: string) {
    this.cps = Array.from(pattern, (ch) => ch.codePointAt(0) ?? 0);
  }

  parse(): Node {
    let flags: Flags = { i: false, m: false, s: false };
    for (;;) {
      const global = this.flagGroupAt(this.pos);
      if (global === undefined || global.terminator !== ")") break;
      flags = { ...flags, ...global.flags };
      this.pos = global.end;
    }
    const node = this.alternation(flags);
    if (this.pos < this.cps.length) {
      // Only an unmatched ")" stops the top-level alternation.
      this.fail("unbalanced parenthesis");
    }
    return node;
  }

  private fail(reason: string): never {
    throw new ParseError(`${reason} at offset ${this.pos}`);
  }

  private peek(offset = 0): number | undefined {
    return this.cps[this.pos + offset];
  }

  private peekChar(offset = 0): string | undefined {
    const c = this.peek(offset);
    return c === undefined ? undefined : String.fromCodePoint(c);
  }

  /**
   * flagGroupAt reads "(?" [ims]+ followed by ")" or ":" at index at, without
   * consuming it.
   */
  private flagGroupAt(
    at: number,
  ): { flags: Partial<Flags>; terminator: string; end: number } | undefined {
    if (this.cps[at] !== 0x28 || this.cps[at + 1] !== 0x3f) return undefined;
    const flags: { i?: boolean; m?: boolean; s?: boolean } = {};
    let j = at + 2;
    for (;;) {
      const c = this.cps[j];
      if (c === 0x69) flags.i = true;
      else if (c === 0x6d) flags.m = true;
      else if (c === 0x73) flags.s = true;
      else break;
      j++;
    }
    if (j === at + 2) return undefined;
    const t = this.cps[j];
    if (t !== 0x29 && t !== 0x3a) return undefined;
    return { flags, terminator: String.fromCodePoint(t), end: j + 1 };
  }

  private alternation(flags: Flags): Node {
    const alternatives = [this.concat(flags)];
    while (this.peekChar() === "|") {
      this.pos++;
      alternatives.push(this.concat(flags));
    }
    return alternatives.length === 1
      ? (alternatives[0] as Node)
      : { kind: "alt", alternatives };
  }

  private concat(flags: Flags): Node {
    const items: Node[] = [];
    for (;;) {
      const ch = this.peekChar();
      if (ch === undefined || ch === "|" || ch === ")") break;
      if (this.quantifierAt() !== undefined) {
        this.fail("nothing to repeat");
      }
      const atom = this.atom(flags);
      const q = this.quantifierAt();
      if (q === undefined) {
        items.push(atom);
        continue;
      }
      if (atom.kind === "assert") this.fail("nothing to repeat");
      this.pos = q.end;
      let lazy = false;
      if (this.peekChar() === "?") {
        lazy = true;
        this.pos++;
      }
      if (this.quantifierAt() !== undefined) this.fail("multiple repeat");
      items.push({ kind: "repeat", child: atom, min: q.min, max: q.max, lazy });
    }
    return items.length === 1 ? (items[0] as Node) : { kind: "concat", items };
  }

  /**
   * quantifierAt reads a quantifier at the current position without consuming
   * it. A "{" that is not {n}, {n,} or {n,m} is a literal, except {,…} which
   * is refused.
   */
  private quantifierAt():
    { min: number; max: number | null; end: number } | undefined {
    const ch = this.peekChar();
    if (ch === "*") return { min: 0, max: null, end: this.pos + 1 };
    if (ch === "+") return { min: 1, max: null, end: this.pos + 1 };
    if (ch === "?") return { min: 0, max: 1, end: this.pos + 1 };
    if (ch !== "{") return undefined;
    let j = this.pos + 1;
    const digits = (): string => {
      let s = "";
      while (isDigit(this.cps[j]))
        s += String.fromCodePoint(this.cps[j++] as number);
      return s;
    };
    const n = digits();
    if (n === "") {
      if (this.cps[j] === 0x2c) {
        j++;
        digits();
        if (this.cps[j] === 0x7d) this.fail("missing repeat minimum");
      }
      return undefined;
    }
    let m: string | null = n;
    if (this.cps[j] === 0x2c) {
      j++;
      const d = digits();
      m = d === "" ? null : d;
    }
    if (this.cps[j] !== 0x7d) return undefined;
    const min = Number(n);
    const max = m === null ? null : Number(m);
    if (min > MAX_REPEAT || (max !== null && max > MAX_REPEAT)) {
      this.fail("repeat count over 1000");
    }
    if (max !== null && min > max) this.fail("repeat minimum over maximum");
    return { min, max, end: j + 1 };
  }

  private atom(flags: Flags): Node {
    const c = this.peek() as number;
    const ch = String.fromCodePoint(c);
    switch (ch) {
      case "(":
        return this.group(flags);
      case "[":
        return this.charClass(flags);
      case ".":
        this.pos++;
        return {
          kind: "set",
          ranges: flags.s ? [[0, MAX_CP]] : complement([[0x0a, 0x0a]]),
        };
      case "^":
        this.pos++;
        return { kind: "assert", assert: flags.m ? "LINE_START" : "START" };
      case "$":
        this.pos++;
        return { kind: "assert", assert: flags.m ? "LINE_END" : "END_DOLLAR" };
      case "\\":
        return this.escape(flags);
      default:
        this.pos++;
        return literal(c, flags);
    }
  }

  private group(flags: Flags): Node {
    if (this.peekChar(1) !== "?") {
      this.pos++;
      return this.groupBody(flags);
    }
    const scoped = this.flagGroupAt(this.pos);
    if (scoped !== undefined) {
      if (scoped.terminator === ")") this.fail("global flags not at the start");
      this.pos = scoped.end;
      return this.groupBody({ ...flags, ...scoped.flags });
    }
    if (this.peekChar(2) === ":") {
      this.pos += 3;
      return this.groupBody(flags);
    }
    if (this.peekChar(2) === "P" && this.peekChar(3) === "<") {
      this.pos += 4;
      let name = "";
      for (;;) {
        const n = this.peekChar();
        if (n === undefined) this.fail("unterminated group name");
        if (n === ">") break;
        name += n;
        this.pos++;
      }
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) this.fail("bad group name");
      if (this.names.has(name)) this.fail("duplicate group name");
      this.names.add(name);
      this.pos++;
      return this.groupBody(flags);
    }
    this.fail("unsupported group syntax");
  }

  private groupBody(flags: Flags): Node {
    const child = this.alternation(flags);
    if (this.peekChar() !== ")") this.fail("missing )");
    this.pos++;
    return { kind: "group", child };
  }

  private escape(flags: Flags): Node {
    const e = this.peek(1);
    if (e === undefined) this.fail("trailing backslash");
    const ch = String.fromCodePoint(e);
    const shorthand = SHORTHANDS[ch];
    if (shorthand !== undefined) {
      this.pos += 2;
      return { kind: "set", ranges: shorthand };
    }
    if (ch === "A" || ch === "b" || ch === "B") {
      this.pos += 2;
      const kinds = { A: "START", b: "WORD_B", B: "NOT_WORD_B" } as const;
      return { kind: "assert", assert: kinds[ch] };
    }
    return literal(this.singleEscape(), flags);
  }

  /**
   * singleEscape reads an escape that stands for one code point:
   * \t \n \r \f \v \a, \xHH, or a backslash before ASCII punctuation.
   */
  private singleEscape(): number {
    const e = this.peek(1);
    if (e === undefined) this.fail("trailing backslash");
    const ch = String.fromCodePoint(e);
    const control = CONTROL_ESCAPES[ch];
    if (control !== undefined) {
      this.pos += 2;
      return control;
    }
    if (ch === "x") {
      const h = hexValue(this.peek(2));
      const l = hexValue(this.peek(3));
      if (h < 0 || l < 0) this.fail("\\x needs two hex digits");
      this.pos += 4;
      return h * 16 + l;
    }
    if (e < 0x80 && !isAsciiAlnum(e)) {
      this.pos += 2;
      return e;
    }
    this.fail(`unsupported escape \\${ch}`);
  }

  private charClass(flags: Flags): Node {
    this.pos++;
    let negated = false;
    if (this.peekChar() === "^") {
      negated = true;
      this.pos++;
    }
    const members: Range[] = [];
    let first = true;
    for (;;) {
      const ch = this.peekChar();
      if (ch === undefined) this.fail("unterminated character class");
      if (ch === "]" && !first) break;
      first = false;
      const item = this.classItem();
      if (item.kind === "shorthand") {
        if (this.peekChar() === "-" && this.isRangeDash()) {
          this.fail("bad character range");
        }
        members.push(...item.ranges);
        continue;
      }
      if (this.peekChar() === "-" && this.isRangeDash()) {
        this.pos++;
        const end = this.classItem();
        if (end.kind === "shorthand") this.fail("bad character range");
        if (item.cp > end.cp) this.fail("bad character range");
        members.push([item.cp, end.cp]);
        continue;
      }
      members.push([item.cp, item.cp]);
    }
    this.pos++;
    let ranges = normalize(members);
    if (flags.i) ranges = foldAscii(ranges);
    if (negated) ranges = complement(ranges);
    return { kind: "set", ranges };
  }

  /** isRangeDash reports whether the "-" at pos is followed by a range end. */
  private isRangeDash(): boolean {
    const next = this.peekChar(1);
    return next !== undefined && next !== "]";
  }

  private classItem():
    { kind: "cp"; cp: number } | { kind: "shorthand"; ranges: Range[] } {
    const c = this.peek() as number;
    if (c === 0x5b && this.peek(1) === 0x3a) this.fail("POSIX class");
    if (c !== 0x5c) {
      this.pos++;
      return { kind: "cp", cp: c };
    }
    const e = this.peek(1);
    const shorthand =
      e === undefined ? undefined : SHORTHANDS[String.fromCodePoint(e)];
    if (shorthand !== undefined) {
      this.pos += 2;
      return { kind: "shorthand", ranges: shorthand };
    }
    return { kind: "cp", cp: this.singleEscape() };
  }
}

function literal(c: number, flags: Flags): Node {
  const ranges: Range[] = [[c, c]];
  return { kind: "set", ranges: flags.i ? foldAscii(ranges) : ranges };
}

// ---- Stage 3: emit (Node.js column) ----

function cp(c: number): string {
  return `\\u{${c.toString(16).toUpperCase()}}`;
}

function emitSet(ranges: Range[]): string {
  if (ranges.length === 0) return "(?!)";
  const only = ranges[0] as Range;
  if (ranges.length === 1 && only[0] === only[1]) return cp(only[0]);
  let out = "[";
  for (const [lo, hi] of ranges)
    out += lo === hi ? cp(lo) : `${cp(lo)}-${cp(hi)}`;
  return out + "]";
}

const START = "(?<![^])";
const END = "(?![^])";
const LF = cp(0x0a);
const W = emitSet(WORD);

const ASSERTS: Record<AssertKind, string> = {
  START,
  END_DOLLAR: `(?=${LF}?${END})`,
  LINE_START: `(?:${START}|(?<=${LF}))`,
  LINE_END: `(?=${LF}|${END})`,
  WORD_B: `(?:(?<=${W})(?!${W})|(?<!${W})(?=${W}))`,
  NOT_WORD_B: `(?:(?<=${W})(?=${W})|(?<!${W})(?!${W}))`,
};

function emit(node: Node): string {
  switch (node.kind) {
    case "set":
      return emitSet(node.ranges);
    case "concat":
      return node.items.map(emit).join("");
    case "alt":
      return node.alternatives.map(emit).join("|");
    case "group":
      return `(?:${emit(node.child)})`;
    case "assert":
      return ASSERTS[node.assert];
    case "repeat": {
      let operand = emit(node.child);
      // A lookahead cannot be quantified under the u flag.
      if (node.child.kind === "set" && node.child.ranges.length === 0) {
        operand = `(?:${operand})`;
      }
      let q: string;
      if (node.min === 0 && node.max === null) q = "*";
      else if (node.min === 1 && node.max === null) q = "+";
      else if (node.min === 0 && node.max === 1) q = "?";
      else if (node.max === null) q = `{${node.min},}`;
      else if (node.min === node.max) q = `{${node.min}}`;
      else q = `{${node.min},${node.max}}`;
      return operand + q + (node.lazy ? "?" : "");
    }
  }
}

/**
 * compilePortable compiles pattern with the portable semantics of
 * evaluation.md (Python re with re.ASCII). A pattern outside the portable
 * subset returns a CompileError.
 */
export function compilePortable(pattern: string): PortableRegex | CompileError {
  let source: string;
  try {
    source = emit(new Parser(pattern).parse());
  } catch (err) {
    if (err instanceof ParseError) return { reason: err.message };
    throw err;
  }
  let re: RegExp;
  try {
    re = new RegExp(source, "u");
  } catch (err) {
    return { reason: `host regex error: ${(err as Error).message}` };
  }
  return { source, flags: re.flags, test: (subject) => re.test(subject) };
}
