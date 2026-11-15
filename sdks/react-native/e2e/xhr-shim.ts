// A minimal XMLHttpRequest over node:http and node:https, enough for
// xhrStreamOpener against a real server: readyState goes 1 to 4,
// responseText grows as data arrives, and readystatechange, progress, load
// and error fire as they do in React Native.

import { request as httpRequest, type ClientRequest } from "node:http";
import { request as httpsRequest } from "node:https";

type Handler = (() => void) | null;

export class ShimXhr {
  /** Every request made, in order. */
  static readonly all: ShimXhr[] = [];

  readyState = 0;
  status = 0;
  responseText = "";
  aborted = false;
  onreadystatechange: Handler = null;
  onprogress: Handler = null;
  onload: Handler = null;
  onerror: Handler = null;
  ontimeout: Handler = null;

  #method = "GET";
  #url = "";
  readonly #headers: Record<string, string> = {};
  #responseHeaders: Record<string, string | string[] | undefined> = {};
  #req: ClientRequest | undefined;

  constructor() {
    ShimXhr.all.push(this);
  }

  open(method: string, url: string): void {
    this.#method = method;
    this.#url = url;
    this.#setState(1);
  }

  setRequestHeader(name: string, value: string): void {
    this.#headers[name] = value;
  }

  getResponseHeader(name: string): string | null {
    const v = this.#responseHeaders[name.toLowerCase()];
    if (v === undefined) return null;
    return Array.isArray(v) ? v.join(", ") : v;
  }

  abort(): void {
    if (this.aborted) return;
    this.aborted = true;
    this.#req?.destroy();
  }

  send(): void {
    const url = new URL(this.#url);
    const request = url.protocol === "https:" ? httpsRequest : httpRequest;
    const req = request(url, { method: this.#method, headers: this.#headers });
    this.#req = req;
    req.on("response", (res) => {
      if (this.aborted) return;
      this.status = res.statusCode ?? 0;
      this.#responseHeaders = res.headers;
      this.#setState(2);
      res.setEncoding("utf8");
      res.on("data", (text: string) => {
        if (this.aborted) return;
        this.responseText += text;
        this.#setState(3);
        this.onprogress?.();
      });
      res.on("end", () => {
        if (this.aborted) return;
        this.#setState(4);
        this.onload?.();
      });
      res.on("error", () => this.#fail());
    });
    req.on("error", () => this.#fail());
    req.end();
  }

  #fail(): void {
    if (this.aborted || this.readyState === 4) return;
    this.status = 0;
    this.#setState(4);
    this.onerror?.();
  }

  #setState(state: number): void {
    this.readyState = state;
    this.onreadystatechange?.();
  }
}
