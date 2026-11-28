// Serves e2e/page on http://127.0.0.1:4173, another origin than the API, so
// the end-to-end test exercises CORS. Run: node --experimental-strip-types e2e/serve.ts
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("./page/", import.meta.url));
const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".map": "application/json",
};

createServer((req, res) => {
  const path = new URL(req.url ?? "/", "http://127.0.0.1").pathname;
  const file = normalize(join(ROOT, path === "/" ? "index.html" : path));
  if (!file.startsWith(ROOT)) {
    res.writeHead(403).end();
    return;
  }
  readFile(file).then(
    (body) => {
      res.writeHead(200, {
        "Content-Type": TYPES[extname(file)] ?? "application/octet-stream",
        "Cache-Control": "no-store",
      });
      res.end(body);
    },
    () => res.writeHead(404).end(),
  );
}).listen(4173, "127.0.0.1");
