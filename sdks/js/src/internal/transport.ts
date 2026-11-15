// HTTP helpers shared by the evaluate, stream and events requests: request
// headers, status mapping and Retry-After parsing. The SDK key travels in
// the Authorization header only, and no message ever contains it.

/** Logger is the part of console the SDK logs through (OpenFeature's shape). */
export interface Logger {
  debug(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
  warn(message: string, ...args: unknown[]): void;
  error(message: string, ...args: unknown[]): void;
}

const PREFIX = "megooflags: ";

const prefixed = (message: string) =>
  message.startsWith(PREFIX) ? message : PREFIX + message;

const discard = () => {};

/**
 * defaultLogger writes warnings and errors to the console, prefixed with
 * "megooflags: " when the message lacks it; debug and info are silent.
 */
export const defaultLogger: Logger = {
  debug: discard,
  info: discard,
  warn: (message, ...args) => console.warn(prefixed(message), ...args),
  error: (message, ...args) => console.error(prefixed(message), ...args),
};

/**
 * FatalError reports that the server refused the SDK key (401, 403, or the
 * stream's revoked event). Retrying with the same key cannot succeed.
 */
export class FatalError extends Error {
  override readonly name = "FatalError";

  constructor(readonly status: number | "revoked") {
    super(`megooflags: SDK key refused (${status})`);
  }
}

/** RetryAfterError reports a 429; seconds is the wait the server asked for. */
export class RetryAfterError extends Error {
  override readonly name = "RetryAfterError";

  constructor(readonly seconds: number) {
    super(`megooflags: rate limited, retry after ${seconds}s`);
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
  sdkKey: string;
  accept: "application/json" | "text/event-stream";
  /** etag adds If-None-Match. */
  etag?: string | null;
  /** lastEventId adds Last-Event-ID. */
  lastEventId?: string | null;
  /** json adds Content-Type: application/json. */
  json?: boolean;
}

/**
 * buildHeaders returns the headers of an SDK request. Browsers forbid
 * setting User-Agent, so none is sent.
 */
export function buildHeaders(opts: HeaderOptions): Record<string, string> {
  const h: Record<string, string> = {
    Authorization: `Bearer ${opts.sdkKey}`,
    Accept: opts.accept,
  };
  if (opts.json) {
    h["Content-Type"] = "application/json";
  }
  if (opts.etag) {
    h["If-None-Match"] = opts.etag;
  }
  if (opts.lastEventId) {
    h["Last-Event-ID"] = opts.lastEventId;
  }
  return h;
}

/** sdkUrl joins the base URL, without its trailing slashes, and an endpoint. */
export function sdkUrl(
  baseUrl: string,
  path: "evaluate" | "stream" | "events",
): string {
  return `${baseUrl.replace(/\/+$/, "")}/sdk/v1/${path}`;
}

/**
 * statusError maps a response status to an error: undefined for 2xx and 304.
 * header reads a response header; now is the current time in epoch
 * milliseconds, for an HTTP-date Retry-After.
 */
export function statusError(
  status: number,
  header: (name: string) => string | null,
  now: number = Date.now(),
): Error | undefined {
  if ((status >= 200 && status < 300) || status === 304) {
    return undefined;
  }
  if (status === 401 || status === 403) {
    return new FatalError(status);
  }
  if (status === 429) {
    return new RetryAfterError(parseRetryAfter(header("Retry-After"), now));
  }
  return new HttpError(status);
}

/** The wait, in seconds, when Retry-After is missing or invalid. */
const DEFAULT_RETRY_AFTER_S = 60;

/**
 * parseRetryAfter reads a Retry-After value as delta-seconds or an
 * HTTP-date and returns whole seconds (a partial second rounds up). A
 * negative number or a date in the past is 0; a missing or invalid value
 * is 60.
 */
export function parseRetryAfter(
  value: string | null | undefined,
  now: number,
): number {
  const v = (value ?? "").trim();
  if (/^[+-]?\d+$/.test(v)) {
    return Math.max(Number(v), 0);
  }
  const at = parseHttpDate(v);
  return at === undefined
    ? DEFAULT_RETRY_AFTER_S
    : Math.max(Math.ceil((at - now) / 1000), 0);
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
