/**
 * Semver is a parsed SemVer 2.0.0 version. Build metadata is dropped, as it
 * has no precedence. The core parts are digit strings without leading zeros,
 * so they compare by (length, text) without overflow.
 */
export interface Semver {
  core: [string, string, string];
  /** undefined when the version has no prerelease. */
  pre: PreId[] | undefined;
}

/** PreId is one prerelease identifier. */
export interface PreId {
  numeric: boolean;
  s: string;
}

/**
 * parseSemver parses a strict SemVer 2.0.0 string: ASCII only, no "v"
 * prefix, no surrounding whitespace, no leading zeros in numeric parts.
 */
export function parseSemver(s: string): Semver | undefined {
  let rest = s;
  const plus = rest.indexOf("+");
  if (plus >= 0) {
    if (
      !rest
        .slice(plus + 1)
        .split(".")
        .every(isIdent)
    )
      return undefined;
    rest = rest.slice(0, plus);
  }
  let pre: PreId[] | undefined;
  const dash = rest.indexOf("-");
  if (dash >= 0) {
    pre = [];
    for (const id of rest.slice(dash + 1).split(".")) {
      if (!isIdent(id)) return undefined;
      const numeric = isDigits(id);
      if (numeric && !isNumericPart(id)) return undefined;
      pre.push({ numeric, s: id });
    }
    rest = rest.slice(0, dash);
  }
  const parts = rest.split(".");
  if (parts.length !== 3 || !parts.every(isNumericPart)) return undefined;
  return { core: parts as [string, string, string], pre };
}

/** compareSemver returns -1, 0 or 1 by SemVer 2.0.0 precedence. */
export function compareSemver(a: Semver, b: Semver): -1 | 0 | 1 {
  for (let i = 0; i < 3; i++) {
    const c = compareDigits(a.core[i] as string, b.core[i] as string);
    if (c !== 0) return c;
  }
  if (a.pre === undefined) return b.pre === undefined ? 0 : 1;
  if (b.pre === undefined) return -1;
  const n = Math.min(a.pre.length, b.pre.length);
  for (let i = 0; i < n; i++) {
    const x = a.pre[i] as PreId;
    const y = b.pre[i] as PreId;
    let c: -1 | 0 | 1;
    if (x.numeric && y.numeric) c = compareDigits(x.s, y.s);
    else if (x.numeric) c = -1;
    else if (y.numeric) c = 1;
    else c = compareText(x.s, y.s);
    if (c !== 0) return c;
  }
  return compareText(a.pre.length, b.pre.length);
}

/** compareDigits compares two digit strings without leading zeros numerically. */
function compareDigits(a: string, b: string): -1 | 0 | 1 {
  return a.length !== b.length
    ? compareText(a.length, b.length)
    : compareText(a, b);
}

// Strings here are ASCII, so code unit order is ASCII order.
function compareText<T extends string | number>(a: T, b: T): -1 | 0 | 1 {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** isNumericPart reports whether s is "0" or [1-9][0-9]*. */
function isNumericPart(s: string): boolean {
  return isDigits(s) && (s === "0" || s[0] !== "0");
}

/** isDigits reports whether s is a non-empty run of ASCII digits. */
function isDigits(s: string): boolean {
  return /^[0-9]+$/.test(s);
}

/** isIdent reports whether s is a non-empty run of [0-9A-Za-z-]. */
function isIdent(s: string): boolean {
  return /^[0-9A-Za-z-]+$/.test(s);
}
