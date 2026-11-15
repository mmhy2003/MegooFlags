// The SSE stream on React Native, whose fetch cannot stream a response body.
import type { StreamOpener, StreamResponse } from "@megooflags/js/platform";

const DEFAULT_MAX_BUFFER = 4 * 1024 * 1024;
const HEADERS_RECEIVED = 2;

/**
 * xhrStreamOpener opens the stream with XMLHttpRequest. The response resolves
 * when the headers arrive; its body yields the text received since the last
 * progress event, encoded as UTF-8, and ends on load. responseText keeps the
 * whole response, so once it is longer than maxBuffer characters the body ends
 * after that chunk and the request is aborted: the streamer reconnects with
 * Last-Event-ID. A status other than 200 has an empty body.
 */
export function xhrStreamOpener(
  Xhr?: typeof XMLHttpRequest,
  maxBuffer: number = DEFAULT_MAX_BUFFER,
): StreamOpener {
  return (url, headers, signal) =>
    new Promise<StreamResponse>((resolve, reject) => {
      if (signal.aborted) {
        reject(signal.reason);
        return;
      }
      const XhrClass = Xhr ?? globalThis.XMLHttpRequest;
      const xhr = new XhrClass();
      const encoder = new TextEncoder();
      const chunks: Uint8Array[] = [];
      let wake: (() => void) | null = null;
      let seen = 0;
      let resolved = false;
      let streaming = false;
      let ended = false;
      let failure: { error: unknown } | null = null;

      const notify = () => {
        const w = wake;
        wake = null;
        w?.();
      };
      const finish = (error?: { error: unknown }) => {
        if (ended) return;
        ended = true;
        failure = error ?? null;
        signal.removeEventListener("abort", onAbort);
        notify();
      };
      const fail = (error: Error) => {
        if (!resolved) {
          resolved = true;
          reject(error);
        }
        finish({ error });
      };
      // Reads the text that arrived since the last call. A trailing high
      // surrogate waits for its pair unless the response is complete.
      const pull = (complete: boolean) => {
        if (!streaming || ended) return;
        const text = xhr.responseText;
        let end = text.length;
        if (
          !complete &&
          end > seen &&
          isHighSurrogate(text.charCodeAt(end - 1))
        ) {
          end--;
        }
        if (end > seen) {
          chunks.push(encoder.encode(text.slice(seen, end)));
          seen = end;
        }
        if (text.length > maxBuffer) {
          xhr.abort();
          finish();
        } else if (complete) {
          finish();
        } else {
          notify();
        }
      };
      const onAbort = () => {
        xhr.abort();
        fail(signal.reason as Error);
      };

      xhr.onreadystatechange = () => {
        // Status 0 is no response at all: onerror or ontimeout follows.
        if (
          !resolved &&
          xhr.readyState >= HEADERS_RECEIVED &&
          xhr.status !== 0
        ) {
          resolved = true;
          streaming = xhr.status === 200;
          if (!streaming) finish();
          resolve({
            status: xhr.status,
            header: (name) => xhr.getResponseHeader(name),
            body: { [Symbol.asyncIterator]: iterator },
          });
        }
        if (xhr.readyState > HEADERS_RECEIVED) pull(false);
      };
      xhr.onprogress = () => pull(false);
      xhr.onload = () => pull(true);
      xhr.onerror = () => fail(new Error("megooflags: stream request failed"));
      xhr.ontimeout = () =>
        fail(new Error("megooflags: stream request timed out"));

      const iterator = (): AsyncIterator<Uint8Array> => ({
        async next() {
          for (;;) {
            const chunk = chunks.shift();
            if (chunk !== undefined) return { done: false, value: chunk };
            if (failure !== null) throw failure.error;
            if (ended) return { done: true, value: undefined };
            await new Promise<void>((r) => (wake = r));
          }
        },
        async return() {
          if (!ended) {
            xhr.abort();
            finish();
          }
          chunks.length = 0;
          failure = null;
          return { done: true, value: undefined };
        },
      });

      signal.addEventListener("abort", onAbort, { once: true });
      xhr.open("GET", url);
      for (const [name, value] of Object.entries(headers)) {
        xhr.setRequestHeader(name, value);
      }
      xhr.send();
    });
}

function isHighSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}
