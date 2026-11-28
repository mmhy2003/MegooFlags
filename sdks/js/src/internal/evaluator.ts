// Remote evaluation: posts the request context to /sdk/v1/evaluate and
// returns the evaluated results, or notModified when the ETag still matches.
import type { JsonValue } from "@openfeature/web-sdk";
import { type EvaluateResult, parseResults } from "./options.js";
import { timerDelay } from "./backoff.js";
import {
  buildHeaders,
  HttpError,
  sdkUrl,
  statusError,
  TimeoutError,
} from "./transport.js";

/** The longest one evaluate may take, body included, before it is aborted. */
export const EVALUATE_TIMEOUT_MS = 30_000;

export type EvaluateOutcome =
  | { kind: "ok"; results: EvaluateResult[]; etag: string | null }
  | { kind: "notModified"; etag: string | null };

export class Evaluator {
  readonly #url: string;
  readonly #sdkKey: string;
  readonly #fetch: typeof fetch;
  readonly #timeoutMs: number;

  constructor(o: {
    baseUrl: string;
    sdkKey: string;
    fetch: typeof fetch;
    timeoutMs?: number;
  }) {
    this.#url = sdkUrl(o.baseUrl, "evaluate");
    this.#sdkKey = o.sdkKey;
    this.#fetch = o.fetch;
    this.#timeoutMs = o.timeoutMs ?? EVALUATE_TIMEOUT_MS;
  }

  /**
   * evaluate sends body as the context, with If-None-Match when etag is
   * set. It throws the statusError of an unexpected status, HttpError(0)
   * for an unreadable 200 body, a TimeoutError after 30 s without the whole
   * answer, and the fetch error on a network failure or an abort.
   */
  async evaluate(
    body: Record<string, JsonValue>,
    etag: string | null,
    signal?: AbortSignal,
  ): Promise<EvaluateOutcome> {
    const ctl = new AbortController();
    const abort = () => ctl.abort(signal?.reason);
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      ctl.abort();
    }, timerDelay(this.#timeoutMs));
    try {
      return await this.#evaluate(body, etag, ctl.signal);
    } catch (e) {
      if (timedOut) throw new TimeoutError("evaluate", this.#timeoutMs);
      throw e;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }

  async #evaluate(
    body: Record<string, JsonValue>,
    etag: string | null,
    signal: AbortSignal,
  ): Promise<EvaluateOutcome> {
    const res = await this.#fetch(this.#url, {
      method: "POST",
      headers: buildHeaders({
        sdkKey: this.#sdkKey,
        accept: "application/json",
        json: true,
        etag,
      }),
      body: JSON.stringify({ context: body }),
      credentials: "omit",
      cache: "no-store",
      signal,
    });
    const header = (name: string) => res.headers.get(name);
    const err = statusError(res.status, header);
    if (err !== undefined) {
      throw err;
    }
    if (res.status === 304) {
      return { kind: "notModified", etag: header("ETag") };
    }
    const text = await res.text();
    let results: EvaluateResult[];
    try {
      results = parseResults(
        JSON.parse(text),
        "megooflags: invalid evaluate response",
      );
    } catch (e) {
      const why =
        e instanceof SyntaxError
          ? "megooflags: invalid evaluate response: not JSON"
          : (e as Error).message;
      throw new HttpError(0, why);
    }
    return { kind: "ok", results, etag: header("ETag") };
  }
}
