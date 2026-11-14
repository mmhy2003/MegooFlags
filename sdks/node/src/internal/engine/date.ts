// RFC 3339 timestamps as integer epoch milliseconds, computed by hand rather
// than with Date so every SDK accepts and refuses the same strings.

const DATE_TIME =
  /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:([Zz])|([+-])(\d{2}):(\d{2}))$/;

/**
 * parseRfc3339Millis parses YYYY-MM-DD, a T, t or space, hh:mm:ss, an
 * optional fraction (truncated to milliseconds) and a Z, z or ±hh:mm zone,
 * ASCII digits only. Returns undefined for anything else or an out-of-range
 * field. The offset's minutes are not range-checked on their own; only the
 * whole offset must be under 24 h.
 */
export function parseRfc3339Millis(s: string): number | undefined {
  // Without the u flag \d is ASCII-only and $ does not match before "\n".
  const m = DATE_TIME.exec(s);
  if (m === null) return undefined;
  const [year, month, day, hour, minute, second] = m
    .slice(1, 7)
    .map(Number) as [number, number, number, number, number, number];
  const millis =
    m[7] === undefined ? 0 : Number(m[7].slice(0, 3).padEnd(3, "0"));
  let offset = 0; // minutes east of UTC
  if (m[8] === undefined) {
    offset = Number(m[10]) * 60 + Number(m[11]);
    if (offset >= 24 * 60) return undefined;
    if (m[9] === "-") offset = -offset;
  }
  if (
    year < 1 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysIn(year, month) ||
    hour > 23 ||
    minute > 59 ||
    second > 59
  ) {
    return undefined;
  }
  const secs =
    daysFromCivil(year, month, day) * 86400 +
    hour * 3600 +
    minute * 60 +
    second -
    offset * 60;
  return secs * 1000 + millis;
}

/**
 * toMillis is v as integer epoch milliseconds: a number is floored, a string
 * is parsed as RFC 3339. Anything else, and a non-finite number, is not a date.
 */
export function toMillis(v: unknown): number | undefined {
  if (typeof v === "string") return parseRfc3339Millis(v);
  if (typeof v !== "number" || !Number.isFinite(v)) return undefined;
  return Math.floor(v);
}

/** daysIn is the number of days in month of the proleptic Gregorian year. */
function daysIn(year: number, month: number): number {
  switch (month) {
    case 2:
      return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28;
    case 4:
    case 6:
    case 9:
    case 11:
      return 30;
  }
  return 31;
}

/** daysFromCivil is the days since 1970-01-01 of a proleptic Gregorian date. */
function daysFromCivil(year: number, month: number, day: number): number {
  const y = month <= 2 ? year - 1 : year;
  const era = Math.floor(y / 400);
  const yoe = y - era * 400;
  const doy =
    Math.floor((153 * (month + (month > 2 ? -3 : 9)) + 2) / 5) + day - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}
