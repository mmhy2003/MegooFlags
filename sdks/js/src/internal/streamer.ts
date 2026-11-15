// The SSE client for GET /sdk/v1/stream: follows `changed` events and
// reconnects with backoff until it is paused, stopped or the key is refused.
import type { StreamOpener } from "../platform.js";
import { Backoff, sleep } from "./backoff.js";
import { readSse, type SseEvent } from "./sse.js";
import {
  buildHeaders,
  FatalError,
  HttpError,
  type Logger,
  RetryAfterError,
  sdkUrl,
  statusError,
} from "./transport.js";

/** The longest silence before the connection counts as dead. */
const DEAD_TIMEOUT_MS = 45_000;
/** A connection that lasted this long resets the backoff. */
const STABLE_AFTER_MS = 60_000;

/** DeadConnectionError reports a stream silent for 45 s. */
export class DeadConnectionError extends Error {
  override readonly name = "DeadConnectionError";

  constructor() {
    super(
      `megooflags: stream sent nothing for ${DEAD_TIMEOUT_MS / 1000}s (dead connection)`,
    );
  }
}

/** StreamEndedError reports a stream the server closed. */
export class StreamEndedError extends Error {
  override readonly name = "StreamEndedError";

  constructor() {
    super("megooflags: stream ended");
  }
}

export interface StreamerHooks {
  /** onConnected runs when the server answers 200. */
  onConnected(): void;
  /** onChanged receives the id of each `changed` event. */
  onChanged(id: string): void;
  /** onFatal runs once when the key is refused; the streamer is done. */
  onFatal(reason: number | "revoked"): void;
  /** onFailure receives the failures since the last 200 and the last error. */
  onFailure(consecutive: number, error: unknown): void;
}

type State = "idle" | "running" | "paused" | "done";

const noop = () => {};

export class Streamer {
  readonly #url: string;
  readonly #sdkKey: string;
  readonly #open: StreamOpener;
  readonly #logger: Logger;
  readonly #hooks: StreamerHooks;
  readonly #now: () => number;
  readonly #backoff: Backoff;
  #state: State = "idle";
  #session: AbortController | null = null;
  #lastEventId: string | null = null;
  #failures = 0;

  constructor(o: {
    baseUrl: string;
    sdkKey: string;
    open: StreamOpener;
    logger: Logger;
    hooks: StreamerHooks;
    now?: () => number;
    rand?: () => number;
  }) {
    this.#url = sdkUrl(o.baseUrl, "stream");
    this.#sdkKey = o.sdkKey;
    this.#open = o.open;
    this.#logger = o.logger;
    this.#hooks = o.hooks;
    this.#now = o.now ?? Date.now;
    this.#backoff = new Backoff(3_000, 60_000, o.rand);
  }

  /** lastEventId is the id of the last `changed` event, sent on reconnect. */
  get lastEventId(): string | null {
    return this.#lastEventId;
  }

  /** start opens the stream; only the first call does anything. */
  start(): void {
    if (this.#state === "idle") this.#run();
  }

  /** pause closes the connection and stops reconnecting until resume. */
  pause(): void {
    if (this.#state !== "running") return;
    this.#state = "paused";
    this.#cancel();
  }

  /** resume reconnects at once after pause, with Last-Event-ID. */
  resume(): void {
    if (this.#state === "paused") this.#run();
  }

  /** stop closes the connection for good. */
  stop(): void {
    this.#state = "done";
    this.#cancel();
  }

  #run(): void {
    this.#state = "running";
    const session = new AbortController();
    this.#session = session;
    void this.#loop(session.signal);
  }

  #cancel(): void {
    this.#session?.abort();
    this.#session = null;
  }

  async #loop(signal: AbortSignal): Promise<void> {
    for (;;) {
      const { connectedAt, err } = await this.#connect(signal);
      if (signal.aborted) return;
      if (err instanceof FatalError) {
        this.#state = "done";
        this.#cancel();
        this.#call(() => this.#hooks.onFatal(err.status));
        return;
      }
      if (
        connectedAt !== undefined &&
        this.#now() - connectedAt >= STABLE_AFTER_MS
      ) {
        this.#backoff.reset();
      }
      this.#failures++;
      let wait = this.#backoff.next();
      if (err instanceof RetryAfterError) {
        wait = Math.max(err.seconds * 1000, wait);
      }
      this.#logger.debug(`megooflags: stream failed: ${errorName(err)}`);
      const failures = this.#failures;
      this.#call(() => this.#hooks.onFailure(failures, err));
      if (!(await sleep(wait, signal))) return;
    }
  }

  /**
   * connect opens one stream and reads it until it ends. connectedAt is set
   * when the server answered 200. It never rejects.
   */
  async #connect(
    signal: AbortSignal,
  ): Promise<{ connectedAt: number | undefined; err: unknown }> {
    const conn = new AbortController();
    const abort = () => conn.abort();
    signal.addEventListener("abort", abort, { once: true });
    let connectedAt: number | undefined;
    let dead = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        dead = true;
        conn.abort();
      }, DEAD_TIMEOUT_MS);
    };
    try {
      const res = await this.#open(
        this.#url,
        buildHeaders({
          sdkKey: this.#sdkKey,
          accept: "text/event-stream",
          lastEventId: this.#lastEventId,
        }),
        conn.signal,
      );
      const bad =
        statusError(res.status, res.header, this.#now()) ??
        (res.status === 200 ? undefined : new HttpError(res.status));
      if (bad !== undefined || signal.aborted) {
        await res.body[Symbol.asyncIterator]().return?.().catch(noop);
        return { connectedAt, err: bad };
      }
      connectedAt = this.#now();
      this.#failures = 0;
      this.#logger.debug("megooflags: stream connected");
      this.#call(() => this.#hooks.onConnected());
      arm();
      await readSse(abortable(res.body, conn.signal), {
        onLine: arm,
        onRetry: (ms) => {
          this.#backoff.base = ms;
        },
        onEvent: (ev) => this.#onEvent(ev),
      });
      return { connectedAt, err: new StreamEndedError() };
    } catch (err) {
      return {
        connectedAt,
        err: dead && !signal.aborted ? new DeadConnectionError() : err,
      };
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      conn.abort();
    }
  }

  #onEvent(ev: SseEvent): void {
    switch (ev.name) {
      case "changed":
        this.#lastEventId = ev.id || this.#lastEventId;
        this.#call(() => this.#hooks.onChanged(ev.id));
        break;
      case "revoked":
        throw new FatalError("revoked");
    }
  }

  /** call runs a hook; an error it throws is logged, never propagated. */
  #call(hook: () => void): void {
    try {
      hook();
    } catch (err) {
      this.#logger.error("megooflags: stream callback failed", err);
    }
  }
}

function errorName(err: unknown): string {
  return err instanceof Error ? err.name : String(err);
}

/**
 * abortable reads body until signal aborts: then the pending read rejects
 * with the abort reason and the body is returned (closed), even when the
 * opener's body ignores the signal.
 */
function abortable(
  body: AsyncIterable<Uint8Array>,
  signal: AbortSignal,
): AsyncIterable<Uint8Array> {
  return {
    [Symbol.asyncIterator]() {
      const it = body[Symbol.asyncIterator]();
      let closed = false;
      const close = () => {
        if (closed) return;
        closed = true;
        void it.return?.().catch(noop);
      };
      const aborted = new Promise<never>((_, reject) => {
        const fail = () => {
          close();
          reject(signal.reason);
        };
        if (signal.aborted) fail();
        else signal.addEventListener("abort", fail, { once: true });
      });
      aborted.catch(noop);
      return {
        next: () => Promise.race([it.next(), aborted]),
        return: async () => {
          close();
          return { done: true, value: undefined };
        },
      };
    },
  };
}
