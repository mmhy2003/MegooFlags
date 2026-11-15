// A scripted HTTP server on 127.0.0.1 for the evaluate, stream and events
// tests. Every response carries the API's CORS headers, so a browser page can
// call it too.

import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

export const EVALUATE = "/sdk/v1/evaluate";
export const STREAM = "/sdk/v1/stream";
export const EVENTS = "/sdk/v1/events";

/** The CORS headers the API sends on the /sdk/v1 routes. */
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "Authorization, Content-Type, If-None-Match, Last-Event-ID",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Expose-Headers": "ETag, Retry-After",
};

/** A canned response: status, headers and body, sent after delayMs. */
export interface Scripted {
  status: number;
  headers?: Record<string, string>;
  body?: string;
  delayMs?: number;
}

/**
 * A response held open as text/event-stream. status defaults to 200; the
 * test writes to it through the SseConnection the server hands out.
 */
export interface Stream {
  stream: true;
  status?: number;
  headers?: Record<string, string>;
}

/**
 * A function answering each request it receives, possibly later: for
 * replies that depend on the body, or a response held back.
 */
export type Responder = (req: Received) => Scripted | Promise<Scripted>;

export type Reply = Scripted | Stream | Responder;

/** A request the server received, with its whole body. */
export interface Received {
  method: string;
  path: string;
  url: string;
  headers: Record<string, string | string[] | undefined>;
  body: string;
  /** at is when the request arrived, in epoch milliseconds. */
  at: number;
}

/** evaluated is a 200 evaluate reply with these results and an ETag. */
export function evaluated(
  results: unknown[],
  opts: { etag?: string; delayMs?: number } = {},
): Scripted {
  return {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      ...(opts.etag === undefined ? {} : { ETag: opts.etag }),
    },
    body: JSON.stringify({ results }),
    ...(opts.delayMs === undefined ? {} : { delayMs: opts.delayMs }),
  };
}

/** An open event-stream response. */
export class SseConnection {
  /** Resolves when the client or the server closed the connection. */
  readonly closed: Promise<void>;

  constructor(
    readonly request: Received,
    private readonly res: ServerResponse,
  ) {
    this.closed = new Promise((resolve) => res.on("close", () => resolve()));
  }

  /** lastEventId is the Last-Event-ID header the client sent, or null. */
  get lastEventId(): string | null {
    const v = this.request.headers["last-event-id"];
    return typeof v === "string" ? v : null;
  }

  /** send writes text as is. */
  send(text: string | Uint8Array): void {
    this.res.write(text);
  }

  /** end finishes the response cleanly. */
  end(): void {
    this.res.end();
  }

  /** destroy drops the connection without finishing the response. */
  destroy(): void {
    this.res.destroy();
  }
}

/**
 * FakeServer answers each path from its queue of replies. The last reply of
 * a queue repeats, so one reply serves every request; a path without replies
 * gets 404. OPTIONS requests get a 204 CORS preflight answer.
 */
export class FakeServer {
  readonly requests: Received[] = [];
  readonly streams: SseConnection[] = [];
  private readonly routes = new Map<string, Reply[]>();
  private readonly streamWaiters: ((c: SseConnection) => void)[] = [];
  private readonly timers = new Set<ReturnType<typeof setTimeout>>();
  private server: Server | undefined;
  private port = 0;
  private taken = 0;

  static async start(): Promise<FakeServer> {
    const s = new FakeServer();
    await s.listen();
    return s;
  }

  /** url is the base URL, for example http://127.0.0.1:41234. */
  get url(): string {
    return `http://127.0.0.1:${this.port}`;
  }

  /** reply adds replies to the queue of path. */
  reply(path: string, ...replies: Reply[]): void {
    const queue = this.routes.get(path) ?? [];
    queue.push(...replies);
    this.routes.set(path, queue);
  }

  /** received lists the requests for path, OPTIONS preflights excluded. */
  received(path: string): Received[] {
    return this.requests.filter(
      (r) => r.path === path && r.method !== "OPTIONS",
    );
  }

  /** bodies parses the JSON body of every request for path. */
  bodies(path: string): unknown[] {
    return this.received(path).map((r) => JSON.parse(r.body) as unknown);
  }

  /** lastEventIds lists the Last-Event-ID of every stream request, or null. */
  lastEventIds(): (string | null)[] {
    return this.received(STREAM).map((r) => {
      const v = r.headers["last-event-id"];
      return typeof v === "string" ? v : null;
    });
  }

  /**
   * nextStream resolves with the first event-stream connection not yet
   * handed out, waiting for it when needed.
   */
  nextStream(): Promise<SseConnection> {
    const open = this.streams[this.taken];
    if (open !== undefined) {
      this.taken++;
      return Promise.resolve(open);
    }
    return new Promise((resolve) => this.streamWaiters.push(resolve));
  }

  /** closeConnections drops every open connection, streams included. */
  closeConnections(): void {
    for (const s of this.streams) {
      s.destroy();
    }
    this.server?.closeAllConnections();
  }

  /** stop drops every connection and closes the listener. */
  async stop(): Promise<void> {
    const server = this.server;
    if (server === undefined) {
      return;
    }
    this.server = undefined;
    for (const t of this.timers) {
      clearTimeout(t);
    }
    this.timers.clear();
    const done = new Promise<void>((resolve) => server.close(() => resolve()));
    for (const s of this.streams) {
      s.destroy();
    }
    server.closeAllConnections();
    await done;
  }

  private listen(): Promise<void> {
    const server = createServer((req, res) => this.handle(req, res));
    this.server = server;
    return new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => {
        this.port = (server.address() as AddressInfo).port;
        resolve();
      });
    });
  }

  private handle(req: IncomingMessage, res: ServerResponse): void {
    const at = Date.now();
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const url = req.url ?? "/";
      const received: Received = {
        method: req.method ?? "",
        path: new URL(url, "http://localhost").pathname,
        url,
        headers: req.headers,
        body: Buffer.concat(chunks).toString("utf8"),
        at,
      };
      this.requests.push(received);
      if (received.method === "OPTIONS") {
        res.writeHead(204, CORS).end();
        return;
      }
      this.answer(received, res);
    });
  }

  private answer(req: Received, res: ServerResponse): void {
    const queue = this.routes.get(req.path);
    const reply = queue && queue.length > 1 ? queue.shift() : queue?.[0];
    if (reply === undefined) {
      res.writeHead(404, CORS).end();
      return;
    }
    if (typeof reply === "function") {
      void Promise.resolve(reply(req)).then((r) => this.send(res, r));
      return;
    }
    if ("stream" in reply) {
      res.writeHead(reply.status ?? 200, {
        ...CORS,
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        ...reply.headers,
      });
      if ((reply.status ?? 200) !== 200) {
        res.end();
        return;
      }
      res.flushHeaders();
      const conn = new SseConnection(req, res);
      this.streams.push(conn);
      const waiter = this.streamWaiters.shift();
      if (waiter !== undefined) {
        this.taken++;
        waiter(conn);
      }
      return;
    }
    this.send(res, reply);
  }

  private send(res: ServerResponse, r: Scripted): void {
    const write = () => {
      if (!res.destroyed) {
        res.writeHead(r.status, { ...CORS, ...r.headers }).end(r.body ?? "");
      }
    };
    if (!r.delayMs) {
      write();
      return;
    }
    const t = setTimeout(() => {
      this.timers.delete(t);
      write();
    }, r.delayMs);
    this.timers.add(t);
  }
}
