// A text/event-stream parser over a byte stream (the stream response body).

/** One dispatched server-sent event. name is "message" by default. */
export interface SseEvent {
  name: string;
  data: string;
  id: string;
}

export interface SseCallbacks {
  /** onLine runs for every line, comments included (the dead timer). */
  onLine(): void;
  /** onRetry receives a valid retry field, in milliseconds. */
  onRetry(ms: number): void;
  /** onEvent receives each event; an error it throws stops reading. */
  onEvent(event: SseEvent): void;
}

const LF = 0x0a;
const CR = 0x0d;

/**
 * readSse parses chunks until they end. Lines are split on the LF byte
 * before decoding, so a character split across chunks decodes whole, and a
 * line of any length is accepted. An unterminated last line or event is
 * discarded. A read error, or an error thrown by a callback, rejects the
 * returned promise unchanged.
 */
export async function readSse(
  chunks: AsyncIterable<Uint8Array>,
  cb: SseCallbacks,
): Promise<void> {
  const decoder = new TextDecoder("utf-8", { ignoreBOM: true });
  const parser = new Parser(cb);
  let pending: Uint8Array[] = [];
  let pendingLength = 0;
  for await (const chunk of chunks) {
    let start = 0;
    for (;;) {
      const end = chunk.indexOf(LF, start);
      if (end < 0) {
        if (start < chunk.length) {
          pending.push(chunk.slice(start));
          pendingLength += chunk.length - start;
        }
        break;
      }
      let line = chunk.subarray(start, end);
      if (pendingLength > 0) {
        pending.push(line);
        line = concat(pending, pendingLength + line.length);
        pending = [];
        pendingLength = 0;
      }
      if (line.length > 0 && line[line.length - 1] === CR) {
        line = line.subarray(0, line.length - 1);
      }
      parser.line(decoder.decode(line));
      start = end + 1;
    }
  }
}

function concat(parts: Uint8Array[], length: number): Uint8Array {
  const out = new Uint8Array(length);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

class Parser {
  private name = "";
  private id = "";
  private data: string[] = [];
  private seen = false;

  constructor(private readonly cb: SseCallbacks) {}

  line(line: string): void {
    this.cb.onLine();
    if (line === "") {
      this.dispatch();
      return;
    }
    if (line.startsWith(":")) {
      return;
    }
    let field = line;
    let value = "";
    const colon = line.indexOf(":");
    if (colon >= 0) {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
    }
    switch (field) {
      case "event":
        this.name = value;
        break;
      case "data":
        this.data.push(value);
        this.seen = true;
        break;
      case "id":
        if (!value.includes("\0")) {
          this.id = value;
        }
        break;
      case "retry":
        if (/^\d+$/.test(value)) {
          this.cb.onRetry(Number(value));
        }
        break;
    }
  }

  private dispatch(): void {
    const event = this.seen
      ? {
          name: this.name || "message",
          data: this.data.join("\n"),
          id: this.id,
        }
      : undefined;
    this.name = "";
    this.id = "";
    this.data = [];
    this.seen = false;
    if (event !== undefined) {
      this.cb.onEvent(event);
    }
  }
}
