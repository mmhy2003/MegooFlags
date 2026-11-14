// A scripted HTTP server on 127.0.0.1 for the delivery, events and provider
// tests.

import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

/** A canned response: status, headers and body. */
export interface Scripted {
  status: number;
  headers?: Record<string, string>;
  body?: string;
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

export type Reply = Scripted | Stream;

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

  /** write sends text as is. */
  write(text: string | Uint8Array): void {
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
 * gets 404.
 */
export class FakeServer {
  readonly requests: Received[] = [];
  readonly streams: SseConnection[] = [];
  private readonly routes = new Map<string, Reply[]>();
  private readonly streamWaiters: ((c: SseConnection) => void)[] = [];
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

  /** received lists the requests for path. */
  received(path: string): Received[] {
    return this.requests.filter((r) => r.path === path);
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
      this.answer(received, res);
    });
  }

  private answer(req: Received, res: ServerResponse): void {
    const queue = this.routes.get(req.path);
    const reply = queue && queue.length > 1 ? queue.shift() : queue?.[0];
    if (reply === undefined) {
      res.writeHead(404).end();
      return;
    }
    if ("stream" in reply) {
      res.writeHead(reply.status ?? 200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        ...reply.headers,
      });
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
    res.writeHead(reply.status, reply.headers).end(reply.body ?? "");
  }
}
