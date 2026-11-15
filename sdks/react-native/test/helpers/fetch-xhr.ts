// An XMLHttpRequest over Node's fetch, enough for xhrStreamOpener: it fires
// readystatechange, progress and load as the response arrives.

type Handler = (() => void) | null;

export class FetchXhr {
  /** Every request made, in order. */
  static readonly all: FetchXhr[] = [];

  readyState = 0;
  status = 0;
  responseText = "";
  aborted = false;
  onreadystatechange: Handler = null;
  onprogress: Handler = null;
  onload: Handler = null;
  onerror: Handler = null;
  ontimeout: Handler = null;

  private method = "GET";
  private url = "";
  private readonly headers: Record<string, string> = {};
  private readonly controller = new AbortController();
  private response: Response | undefined;

  constructor() {
    FetchXhr.all.push(this);
  }

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
    this.readyState = 1;
  }

  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value;
  }

  getResponseHeader(name: string): string | null {
    return this.response?.headers.get(name) ?? null;
  }

  abort(): void {
    this.aborted = true;
    this.controller.abort();
  }

  send(): void {
    void this.run();
  }

  private async run(): Promise<void> {
    try {
      const res = await fetch(this.url, {
        method: this.method,
        headers: this.headers,
        signal: this.controller.signal,
      });
      this.response = res;
      this.status = res.status;
      this.readyState = 2;
      this.onreadystatechange?.();
      const decoder = new TextDecoder();
      const reader = res.body?.getReader();
      for (;;) {
        const r = await reader?.read();
        if (r === undefined || r.done) break;
        this.responseText += decoder.decode(r.value, { stream: true });
        this.readyState = 3;
        this.onreadystatechange?.();
        this.onprogress?.();
      }
      this.readyState = 4;
      this.onreadystatechange?.();
      this.onload?.();
    } catch {
      if (!this.aborted) {
        this.readyState = 4;
        this.status = 0;
        this.onerror?.();
      }
    }
  }
}
