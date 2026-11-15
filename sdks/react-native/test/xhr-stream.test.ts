import { afterEach, describe, expect, it } from "vitest";
import { xhrStreamOpener } from "../src/xhr-stream.js";

type Handler = (() => void) | null;

/** A scripted XMLHttpRequest: the test drives the response. */
class FakeXhr {
  static last: FakeXhr | undefined;

  method = "";
  url = "";
  headers: Record<string, string> = {};
  responseHeaders: Record<string, string> = {};
  sent = false;
  aborted = false;
  readyState = 0;
  status = 0;
  responseText = "";
  onreadystatechange: Handler = null;
  onprogress: Handler = null;
  onload: Handler = null;
  onerror: Handler = null;
  ontimeout: Handler = null;

  constructor() {
    FakeXhr.last = this;
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
    this.readyState = 1;
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }

  send() {
    this.sent = true;
  }

  abort() {
    this.aborted = true;
  }

  getResponseHeader(name: string): string | null {
    return this.responseHeaders[name.toLowerCase()] ?? null;
  }

  respond(status: number, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseHeaders = headers;
    this.readyState = 2;
    this.onreadystatechange?.();
  }

  receive(text: string) {
    this.responseText += text;
    this.readyState = 3;
    this.onreadystatechange?.();
    this.onprogress?.();
  }

  finish() {
    this.readyState = 4;
    this.onreadystatechange?.();
    this.onload?.();
  }

  fail() {
    this.readyState = 4;
    this.onreadystatechange?.();
    this.onerror?.();
  }
}

const Xhr = FakeXhr as unknown as typeof XMLHttpRequest;
const URL_ = "http://flags.test/sdk/v1/stream";
const HEADERS = {
  Authorization: "Bearer mf_client_abc",
  Accept: "text/event-stream",
  "Last-Event-ID": "7",
};

function xhr(): FakeXhr {
  if (FakeXhr.last === undefined) throw new Error("no XMLHttpRequest made");
  return FakeXhr.last;
}

function bytes(chunks: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out;
}

async function open(maxBuffer?: number, signal = new AbortController().signal) {
  const pending = xhrStreamOpener(Xhr, maxBuffer)(URL_, HEADERS, signal);
  xhr().respond(200, { "content-type": "text/event-stream" });
  return pending;
}

afterEach(() => {
  FakeXhr.last = undefined;
});

describe("xhrStreamOpener", () => {
  it("opens a GET with the headers and resolves when the headers arrive", async () => {
    let resolved = false;
    const pending = xhrStreamOpener(Xhr)(
      URL_,
      HEADERS,
      new AbortController().signal,
    ).then((res) => {
      resolved = true;
      return res;
    });
    const x = xhr();
    expect(x.method).toBe("GET");
    expect(x.url).toBe(URL_);
    expect(x.headers).toEqual(HEADERS);
    expect(x.headers).not.toHaveProperty("url");
    expect(x.sent).toBe(true);
    await Promise.resolve();
    expect(resolved).toBe(false);

    x.respond(200, { "content-type": "text/event-stream" });
    const res = await pending;
    expect(res.status).toBe(200);
    expect(res.header("Content-Type")).toBe("text/event-stream");
    expect(res.header("Retry-After")).toBeNull();
  });

  it("yields the new text as UTF-8 as progress fires, in order, and ends on load", async () => {
    const res = await open();
    const it = res.body[Symbol.asyncIterator]();
    const decoder = new TextDecoder();

    xhr().receive("event: changed\n");
    const first = await it.next();
    expect(decoder.decode(first.value)).toBe("event: changed\n");

    const next = it.next();
    xhr().receive("id: 1\ndata: caf");
    xhr().receive("é 😀\n\n");
    const chunks = [(await next).value as Uint8Array];
    const second = await it.next();
    chunks.push(second.value as Uint8Array);
    expect(bytes(chunks)).toEqual(
      new TextEncoder().encode("id: 1\ndata: café 😀\n\n"),
    );

    xhr().finish();
    expect(await it.next()).toEqual({ done: true, value: undefined });
  });

  it("holds back half of a surrogate pair until the rest arrives", async () => {
    const res = await open();
    const emoji = "😀";
    const chunks: Uint8Array[] = [];
    const reading = (async () => {
      for await (const chunk of res.body) chunks.push(chunk);
    })();

    xhr().receive(`data: ${emoji[0]}`);
    xhr().receive(`${emoji[1]}\n\n`);
    xhr().finish();
    await reading;

    expect(bytes(chunks)).toEqual(new TextEncoder().encode("data: 😀\n\n"));
  });

  it("resolves a status other than 200 with an empty body", async () => {
    const pending = xhrStreamOpener(Xhr)(
      URL_,
      HEADERS,
      new AbortController().signal,
    );
    xhr().respond(401);
    const res = await pending;
    expect(res.status).toBe(401);
    xhr().receive('{"detail":"unauthorized"}');
    xhr().finish();
    const chunks: Uint8Array[] = [];
    for await (const chunk of res.body) chunks.push(chunk);
    expect(chunks).toEqual([]);
  });

  it("keeps Retry-After readable on a 429", async () => {
    const pending = xhrStreamOpener(Xhr)(
      URL_,
      HEADERS,
      new AbortController().signal,
    );
    xhr().respond(429, { "retry-after": "12" });
    const res = await pending;
    expect(res.status).toBe(429);
    expect(res.header("Retry-After")).toBe("12");
  });

  it("rejects when the request fails before the headers", async () => {
    const pending = xhrStreamOpener(Xhr)(
      URL_,
      HEADERS,
      new AbortController().signal,
    );
    xhr().fail();
    await expect(pending).rejects.toThrow(/stream request failed/);
  });

  it("throws from the iterator on a network error", async () => {
    const res = await open();
    const it = res.body[Symbol.asyncIterator]();
    xhr().receive("data: a\n\n");
    expect((await it.next()).done).toBe(false);
    const next = it.next();
    xhr().fail();
    await expect(next).rejects.toThrow(/stream request failed/);
  });

  it("throws from the iterator on a timeout", async () => {
    const res = await open();
    const it = res.body[Symbol.asyncIterator]();
    const next = it.next();
    xhr().ontimeout?.();
    await expect(next).rejects.toThrow(/stream request timed out/);
  });

  it("aborts the request when the signal aborts", async () => {
    const controller = new AbortController();
    const res = await open(undefined, controller.signal);
    const it = res.body[Symbol.asyncIterator]();
    const next = it.next();
    controller.abort(new Error("closed"));
    expect(xhr().aborted).toBe(true);
    await expect(next).rejects.toThrow("closed");
  });

  it("aborts and rejects when the signal aborts before the headers", async () => {
    const controller = new AbortController();
    const pending = xhrStreamOpener(Xhr)(URL_, HEADERS, controller.signal);
    controller.abort(new Error("closed"));
    expect(xhr().aborted).toBe(true);
    await expect(pending).rejects.toThrow("closed");
  });

  it("does not send when the signal is already aborted", async () => {
    const controller = new AbortController();
    controller.abort(new Error("closed"));
    await expect(
      xhrStreamOpener(Xhr)(URL_, HEADERS, controller.signal),
    ).rejects.toThrow("closed");
    expect(FakeXhr.last?.sent ?? false).toBe(false);
  });

  it("aborts the request when the iterator is returned", async () => {
    const res = await open();
    const it = res.body[Symbol.asyncIterator]();
    xhr().receive("data: a\n\n");
    await it.next();
    expect(await it.return?.()).toEqual({ done: true, value: undefined });
    expect(xhr().aborted).toBe(true);
    expect(await it.next()).toEqual({ done: true, value: undefined });
  });

  it("ends after the chunk that passes maxBuffer and aborts the request", async () => {
    const res = await open(16);
    const chunks: Uint8Array[] = [];
    const reading = (async () => {
      for await (const chunk of res.body) chunks.push(chunk);
    })();

    xhr().receive("data: 12345678\n\n");
    expect(xhr().aborted).toBe(false);
    xhr().receive("data: 9\n\n");
    expect(xhr().aborted).toBe(true);
    xhr().receive("data: dropped\n\n");
    await reading;

    expect(new TextDecoder().decode(bytes(chunks))).toBe(
      "data: 12345678\n\ndata: 9\n\n",
    );
  });

  it("defaults to the global XMLHttpRequest and a 4 MiB buffer", async () => {
    const original = globalThis.XMLHttpRequest;
    globalThis.XMLHttpRequest = Xhr;
    try {
      const pending = xhrStreamOpener()(
        URL_,
        HEADERS,
        new AbortController().signal,
      );
      xhr().respond(200);
      const res = await pending;
      const it = res.body[Symbol.asyncIterator]();
      xhr().receive("x".repeat(4 * 1024 * 1024));
      expect(xhr().aborted).toBe(false);
      await it.next();
      xhr().receive("x");
      expect(xhr().aborted).toBe(true);
    } finally {
      globalThis.XMLHttpRequest = original;
    }
  });
});
