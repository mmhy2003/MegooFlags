import { afterEach, describe, expect, it } from "vitest";
import { readSse, type SseEvent } from "../../src/internal/delivery/sse.js";
import { FakeServer } from "../helpers/fake-server.js";

const enc = new TextEncoder();

async function* chunks(...parts: (string | Uint8Array)[]) {
  for (const p of parts) {
    yield typeof p === "string" ? enc.encode(p) : p;
  }
}

interface Seen {
  lines: number;
  retries: number[];
  events: SseEvent[];
}

async function read(...parts: (string | Uint8Array)[]): Promise<Seen> {
  const seen: Seen = { lines: 0, retries: [], events: [] };
  await readSse(chunks(...parts), {
    onLine: () => seen.lines++,
    onRetry: (ms) => seen.retries.push(ms),
    onEvent: (e) => seen.events.push(e),
  });
  return seen;
}

describe("readSse", () => {
  it.each<[string, string, number, SseEvent[]]>([
    [
      "named event with id",
      'event: put\nid: 42\ndata: {"flags":{}}\n\n',
      4,
      [{ name: "put", data: '{"flags":{}}', id: "42" }],
    ],
    [
      "default name",
      "data: x\n\n",
      2,
      [{ name: "message", data: "x", id: "" }],
    ],
    [
      "multi-line data",
      "data: a\ndata: b\ndata:c\n\n",
      4,
      [{ name: "message", data: "a\nb\nc", id: "" }],
    ],
    [
      "CRLF endings",
      "event: put\r\nid: 7\r\ndata: x\r\n\r\n",
      4,
      [{ name: "put", data: "x", id: "7" }],
    ],
    [
      "unknown event name passed through",
      "event: mystery\ndata: 1\n\n",
      3,
      [{ name: "mystery", data: "1", id: "" }],
    ],
    [
      "unterminated last event discarded",
      "data: a\n\nevent: put\ndata: b\n",
      4,
      [{ name: "message", data: "a", id: "" }],
    ],
    [
      "unterminated last line discarded",
      "data: a\n\ndata: b",
      2,
      [{ name: "message", data: "a", id: "" }],
    ],
    [
      "state resets after dispatch",
      "event: put\nid: 1\ndata: a\n\ndata: b\n\n",
      6,
      [
        { name: "put", data: "a", id: "1" },
        { name: "message", data: "b", id: "" },
      ],
    ],
    [
      "empty line without data dispatches nothing",
      "event: put\n\ndata: a\n\n",
      4,
      [{ name: "message", data: "a", id: "" }],
    ],
    [
      "id with NUL ignored",
      "id: a\0b\ndata: x\n\n",
      3,
      [{ name: "message", data: "x", id: "" }],
    ],
    [
      "field without colon has an empty value",
      "data\ndata\n\n",
      3,
      [{ name: "message", data: "\n", id: "" }],
    ],
    [
      "only one leading space removed, unknown field ignored",
      "foo: bar\ndata:  two\n\n",
      3,
      [{ name: "message", data: " two", id: "" }],
    ],
    [
      "colon inside the value kept",
      "data: a: b\n\n",
      2,
      [{ name: "message", data: "a: b", id: "" }],
    ],
  ])("%s", async (_name, input, lines, events) => {
    const seen = await read(input);
    expect(seen.events).toEqual(events);
    expect(seen.lines).toBe(lines);
    expect(seen.retries).toEqual([]);
  });

  it("comments call onLine only", async () => {
    const seen = await read(": ping\n:\n\n");
    expect(seen).toEqual({ lines: 3, retries: [], events: [] });
  });

  it("a valid retry calls onRetry in milliseconds", async () => {
    const seen = await read("retry: 3000\n\n");
    expect(seen).toEqual({ lines: 2, retries: [3000], events: [] });
  });

  it.each(["retry: x\n", "retry: -1\n", "retry: 1.5\n", "retry:\n"])(
    "an invalid retry %j is ignored",
    async (input) => {
      expect((await read(input)).retries).toEqual([]);
    },
  );

  it("reads a 1 MiB data line", async () => {
    const big = "x".repeat(1 << 20);
    const parts: string[] = ["event: put\ndata: "];
    for (let i = 0; i < big.length; i += 16_384) {
      parts.push(big.slice(i, i + 16_384));
    }
    parts.push("\n\n");
    const seen = await read(...parts);
    expect(seen.events).toHaveLength(1);
    expect(seen.events[0]?.data).toBe(big);
  });

  it("decodes a multi-byte character split across two chunks", async () => {
    const bytes = enc.encode("data: é€😀\n\n");
    const at = bytes.indexOf(0xe2) + 1;
    const seen = await read(bytes.slice(0, at), bytes.slice(at));
    expect(seen.events).toEqual([{ name: "message", data: "é€😀", id: "" }]);
  });

  it("splits lines that arrive in one chunk or byte by byte", async () => {
    const text = "event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n";
    const one = await read(text);
    const bytes = enc.encode(text);
    const split = await read(...Array.from(bytes, (b) => Uint8Array.of(b)));
    expect(split).toEqual(one);
    expect(one.events.map((e) => e.data)).toEqual(["a", "b"]);
  });

  it("an error thrown by onEvent stops reading", async () => {
    const names: string[] = [];
    const stop = new Error("stop");
    await expect(
      readSse(chunks("event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n"), {
        onLine: () => {},
        onRetry: () => {},
        onEvent: (e) => {
          names.push(e.name);
          throw stop;
        },
      }),
    ).rejects.toBe(stop);
    expect(names).toEqual(["revoked"]);
  });

  it("a read error is passed through", async () => {
    const boom = new Error("boom");
    async function* failing() {
      yield enc.encode(": ping\n");
      throw boom;
    }
    let lines = 0;
    await expect(
      readSse(failing(), {
        onLine: () => lines++,
        onRetry: () => {},
        onEvent: () => {},
      }),
    ).rejects.toBe(boom);
    expect(lines).toBe(1);
  });

  describe("over HTTP", () => {
    let server: FakeServer | undefined;

    afterEach(async () => {
      await server?.stop();
    });

    it("reads a fetch response body as it arrives", async () => {
      server = await FakeServer.start();
      server.reply("/sdk/v1/stream", { stream: true });
      const res = await fetch(`${server.url}/sdk/v1/stream`);
      expect(res.headers.get("content-type")).toBe("text/event-stream");
      const conn = await server.nextStream();
      const events: SseEvent[] = [];
      const done = readSse(res.body ?? chunks(), {
        onLine: () => {},
        onRetry: () => {},
        onEvent: (e) => {
          events.push(e);
          if (events.length === 1) {
            conn.write("event: put\nid: 2\ndata: b\n\n");
            conn.end();
          }
        },
      });
      conn.write(": ping\n\nevent: put\nid: 1\ndata: a\n\n");
      await done;
      expect(events).toEqual([
        { name: "put", data: "a", id: "1" },
        { name: "put", data: "b", id: "2" },
      ]);
      expect(server.received("/sdk/v1/stream")).toHaveLength(1);
    });
  });
});
