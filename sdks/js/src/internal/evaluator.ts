// Remote evaluation: posts the request context to /sdk/v1/evaluate and
// returns the evaluated results, or notModified when the ETag still matches.
import type { JsonValue } from "@openfeature/web-sdk";
import { type EvaluateResult, parseResults } from "./options.js";
import { buildHeaders, HttpError, sdkUrl, statusError } from "./transport.js";

export type EvaluateOutcome =
  | { kind: "ok"; results: EvaluateResult[]; etag: string | null }
  | { kind: "notModified"; etag: string | null };

export class Evaluator {
  readonly #url: string;
  readonly #sdkKey: string;
  readonly #fetch: typeof fetch;

  constructor(o: { baseUrl: string; sdkKey: string; fetch: typeof fetch }) {
    this.#url = sdkUrl(o.baseUrl, "evaluate");
    this.#sdkKey = o.sdkKey;
    this.#fetch = o.fetch;
  }

  /**
   * evaluate sends body as the context, with If-None-Match when etag is
   * set. It throws the statusError of an unexpected status, HttpError(0)
   * for an unreadable 200 body, and the fetch error on a network failure or
   * an abort.
   */
  async evaluate(
    body: Record<string, JsonValue>,
    etag: string | null,
    signal?: AbortSignal,
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
      ...(signal === undefined ? {} : { signal }),
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
