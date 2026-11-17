// Copies the texts that live elsewhere in the repository into the site, as
// listed in sync.manifest.json (or the manifest path given as the argument).
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  frontMatter,
  loadManifest,
  rewriteLinks,
  stripTitle,
} from "./sync-lib.mjs";

const site = fileURLToPath(new URL("..", import.meta.url));
const root = join(site, "..");
const docs = join(site, "src", "content", "docs");

const manifest = loadManifest(
  process.argv[2] ?? join(site, "sync.manifest.json"),
);

const missing = manifest.filter((e) => !existsSync(join(root, e.source)));
if (missing.length > 0) {
  for (const e of missing) console.error(`sync: missing source ${e.source}`);
  process.exit(1);
}

for (const entry of manifest) {
  const markdown = readFileSync(join(root, entry.source), "utf8");
  const body = rewriteLinks(stripTitle(markdown), entry.source, manifest);
  const dest = join(docs, entry.dest);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, frontMatter(entry) + body);
}
console.log(`sync: ${manifest.length} pages`);
