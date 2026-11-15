// What the SDK uses from a browser.
import type { StorageLike, StreamOpener } from "./platform.js";

const PROBE = "megooflags-probe";

/**
 * localStorageOrNull returns localStorage when it can be written, else null:
 * server rendering has none, and private modes or blocked storage throw on
 * access or on write.
 */
export function localStorageOrNull(): StorageLike | null {
  try {
    const storage = (globalThis as { localStorage?: Storage }).localStorage;
    if (!storage) return null;
    storage.setItem(PROBE, PROBE);
    storage.removeItem(PROBE);
    return storage;
  } catch {
    return null;
  }
}

/**
 * webStreamOpener opens the stream with fetchFn, without cookies or the HTTP
 * cache. The body yields the response chunks as they arrive; returning from
 * it cancels the response.
 */
export function webStreamOpener(fetchFn: typeof fetch): StreamOpener {
  return async (url, headers, signal) => {
    const res = await fetchFn(url, {
      headers,
      signal,
      credentials: "omit",
      cache: "no-store",
    });
    return {
      status: res.status,
      header: (name) => res.headers.get(name),
      body: chunksOf(res.body),
    };
  };
}

function chunksOf(
  body: ReadableStream<Uint8Array> | null,
): AsyncIterable<Uint8Array> {
  return {
    [Symbol.asyncIterator]() {
      const reader = body?.getReader();
      let released = false;
      const release = () => {
        if (released || reader === undefined) return;
        released = true;
        reader.releaseLock();
      };
      return {
        async next() {
          const r = reader === undefined ? undefined : await reader.read();
          if (r === undefined || r.done) {
            release();
            return { done: true, value: undefined };
          }
          return { done: false, value: r.value };
        },
        async return() {
          if (reader !== undefined && !released) {
            const cancelled = reader.cancel().catch(() => {});
            release();
            await cancelled;
          }
          return { done: true, value: undefined };
        },
      };
    },
  };
}
