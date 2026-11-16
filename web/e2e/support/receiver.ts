import { createServer, type IncomingHttpHeaders } from "node:http";
import type { AddressInfo } from "node:net";

export type ReceivedRequest = {
  method: string;
  headers: IncomingHttpHeaders;
  /** The raw body, as signed. */
  body: string;
};

export type Receiver = {
  /** http://127.0.0.1:<port>/hook; the API needs MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true to reach it. */
  url: string;
  requests: ReceivedRequest[];
  /** The status every later request gets. */
  setStatus(status: number): void;
  close(): Promise<void>;
};

/** A local HTTP server that records every request and answers with `status`. */
export async function startReceiver(status = 200): Promise<Receiver> {
  const requests: ReceivedRequest[] = [];
  let current = status;
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      requests.push({
        method: req.method ?? "",
        headers: req.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      });
      res.writeHead(current, { "Content-Type": "text/plain" });
      res.end(current < 400 ? "ok" : "nope");
    });
  });
  await new Promise<void>((resolve) =>
    server.listen(0, "127.0.0.1", () => resolve()),
  );
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}/hook`,
    requests,
    setStatus(next: number) {
      current = next;
    },
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}
