// HTTP helpers shared by the ruleset download, the stream and the event
// sender: request headers, status mapping and Retry-After parsing.

import { VERSION } from "../../version.js";

export const USER_AGENT = `megooflags-node/${VERSION}`;

/**
 * FatalError reports that the server refused the SDK key (401, 403, or the
 * stream's revoked event). Retrying with the same key cannot succeed.
 */
export class FatalError extends Error {
  override readonly name = "FatalError";

  constructor(
    readonly status: number | undefined,
    readonly reason?: string,
  ) {
    super(`megooflags: SDK key refused (${status ?? reason})`);
  }
}

/** RetryAfterError reports a 429. afterMs is the server's Retry-After, or 0. */
export class RetryAfterError extends Error {
  override readonly name = "RetryAfterError";

  constructor(readonly afterMs: number) {
    super(`megooflags: rate limited, retry after ${afterMs / 1000}s`);
  }
}

/** HttpError reports any other unexpected status. */
export class HttpError extends Error {
  override readonly name = "HttpError";

  constructor(readonly status: number) {
    super(`megooflags: HTTP ${status}`);
  }
}

export interface HeaderOptions {
  /** body adds Content-Type: application/json. */
  body?: boolean;
  /** stream asks for text/event-stream without caching. */
  stream?: boolean;
}

/**
 * buildHeaders returns the headers of an SDK request. The key travels in the
 * Authorization header only, never in the URL.
 */
export function buildHeaders(
  sdkKey: string,
  opts: HeaderOptions = {},
): Record<string, string> {
  const h: Record<string, string> = {
    Authorization: `Bearer ${sdkKey}`,
    "User-Agent": USER_AGENT,
    Accept: opts.stream ? "text/event-stream" : "application/json",
  };
  if (opts.stream) {
    h["Cache-Control"] = "no-cache";
  }
  if (opts.body) {
    h["Content-Type"] = "application/json";
  }
  return h;
}

/** sdkUrl joins the base URL, without its trailing slashes, and an endpoint. */
export function sdkUrl(
  baseUrl: string,
  endpoint: "ruleset" | "stream" | "events",
): string {
  return `${baseUrl.replace(/\/+$/, "")}/sdk/v1/${endpoint}`;
}

/** The part of a fetch Headers object statusError reads. */
export interface HeaderReader {
  get(name: string): string | null;
}

/**
 * statusError maps a response status to an error: undefined for 2xx and 304.
 * now is the current time in epoch milliseconds, for an HTTP-date
 * Retry-After.
 */
export function statusError(
  status: number,
  headers: HeaderReader,
  now: number,
): Error | undefined {
  if ((status >= 200 && status < 300) || status === 304) {
    return undefined;
  }
  if (status === 401 || status === 403) {
    return new FatalError(status);
  }
  if (status === 429) {
    return new RetryAfterError(
      parseRetryAfter(headers.get("Retry-After"), now) ?? 0,
    );
  }
  return new HttpError(status);
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

// The three HTTP-date forms of RFC 9110 §5.6.7, captured as day, month,
// year, hour, minute, second.
const IMF_FIXDATE =
  /^[A-Z][a-z]{2}, (\d{2}) ([A-Z][a-z]{2}) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT$/;
const RFC850 =
  /^[A-Z][a-z]+, (\d{2})-([A-Z][a-z]{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) GMT$/;
const ASCTIME =
  /^[A-Z][a-z]{2} ([A-Z][a-z]{2}) ([ \d]\d) (\d{2}):(\d{2}):(\d{2}) (\d{4})$/;

/**
 * parseRetryAfter reads a Retry-After value as delta-seconds or an
 * HTTP-date and returns milliseconds. A negative number or a date in the
 * past is 0; a missing or invalid value is undefined.
 */
export function parseRetryAfter(
  value: string | null | undefined,
  now: number,
): number | undefined {
  const v = (value ?? "").trim();
  if (/^[+-]?\d+$/.test(v)) {
    return Math.max(Number(v), 0) * 1000;
  }
  const at = parseHttpDate(v);
  return at === undefined ? undefined : Math.max(at - now, 0);
}

function parseHttpDate(v: string): number | undefined {
  let m = IMF_FIXDATE.exec(v);
  if (m) {
    return utc(m[3], m[2], m[1], m[4], m[5], m[6]);
  }
  m = RFC850.exec(v);
  if (m) {
    // Two-digit years as Go's time.Parse reads them: 69 to 99 are 19xx.
    const yy = Number(m[3]);
    return utc(
      String(yy >= 69 ? 1900 + yy : 2000 + yy),
      m[2],
      m[1],
      m[4],
      m[5],
      m[6],
    );
  }
  m = ASCTIME.exec(v);
  if (m) {
    return utc(m[6], m[1], m[2], m[3], m[4], m[5]);
  }
  return undefined;
}

function utc(
  year = "",
  month = "",
  day = "",
  hour = "",
  minute = "",
  second = "",
): number | undefined {
  const mon = MONTHS.indexOf(month);
  const [y, d, h, mi, s] = [year, day, hour, minute, second].map((x) =>
    Number(x.trim()),
  ) as [number, number, number, number, number];
  if (mon < 0 || h > 23 || mi > 59 || s > 59) {
    return undefined;
  }
  const t = new Date(0);
  t.setUTCFullYear(y, mon, d);
  t.setUTCHours(h, mi, s, 0);
  // A day the month does not have rolls over; refuse it.
  if (t.getUTCMonth() !== mon || t.getUTCDate() !== d) {
    return undefined;
  }
  return t.getTime();
}
