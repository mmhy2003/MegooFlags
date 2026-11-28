// The pure parts of sync.mjs: reading the manifest and turning a repository
// Markdown file into a site page.
import { readFileSync, statSync } from "node:fs";
import { posix } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = "https://github.com/mmhy2003/MegooFlags";
const BASE = "/MegooFlags";
const ROOT = fileURLToPath(new URL("../../", import.meta.url));

/**
 * @typedef {{ source: string, dest: string, title: string, description: string }} Entry
 */

/** @returns {Entry[]} */
export function loadManifest(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

/** Calls `fn` on each run of lines outside fenced code and joins the result. */
function outsideFences(markdown, fn) {
  /** @type {{ code: boolean, lines: string[] }[]} */
  const parts = [];
  let fence = null;
  for (const line of markdown.split("\n")) {
    let code = fence !== null;
    if (fence === null) {
      const open = /^\s*(`{3,}|~{3,})/.exec(line);
      if (open) {
        fence = open[1];
        code = true;
      }
    } else if (
      new RegExp(`^\\s*${fence[0]}{${fence.length},}\\s*$`).test(line)
    ) {
      fence = null;
    }
    const last = parts.at(-1);
    if (last && last.code === code) last.lines.push(line);
    else parts.push({ code, lines: [line] });
  }
  return parts
    .map((p) => (p.code ? p.lines.join("\n") : fn(p.lines.join("\n"))))
    .join("\n");
}

function isDirectory(path) {
  try {
    return statSync(posix.join(ROOT, path)).isDirectory();
  } catch {
    return false;
  }
}

/** The site or GitHub URL for one link target in `sourcePath`. */
function target(link, sourcePath, manifest) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(link) || link.startsWith("#")) return link;
  const hash = link.indexOf("#");
  const anchor = hash < 0 ? "" : link.slice(hash);
  const raw = hash < 0 ? link : link.slice(0, hash);
  const path = posix
    .normalize(posix.join(posix.dirname(sourcePath), raw))
    .replace(/\/$/, "");
  const entry = manifest.find(
    (e) => e.source === path || e.source === `${path}/README.md`,
  );
  if (entry) return `${BASE}/${entry.dest.replace(/\.md$/, "")}/${anchor}`;
  const kind = raw.endsWith("/") || isDirectory(path) ? "tree" : "blob";
  return `${REPO}/${kind}/main/${path}${anchor}`;
}

/**
 * Rewrites the Markdown links `[t](x)` of the file at `sourcePath` (relative
 * to the repository root) so they work on the site. Fenced code stays as is.
 * @param {string} markdown
 * @param {string} sourcePath
 * @param {Entry[]} manifest
 */
export function rewriteLinks(markdown, sourcePath, manifest) {
  return outsideFences(markdown, (prose) =>
    prose.replace(
      /(\]\()([^)\s]+)((?:\s+"[^"]*")?\))/g,
      (_, open, link, close) =>
        `${open}${target(link, sourcePath, manifest)}${close}`,
    ),
  );
}

/** Removes the first `# ` heading (and the blank lines after it). */
export function stripTitle(markdown) {
  let done = false;
  return outsideFences(markdown, (prose) => {
    if (done) return prose;
    const match = /^# .*\n(?:[ \t]*\n)*/m.exec(prose);
    if (!match) return prose;
    done = true;
    return (
      prose.slice(0, match.index) + prose.slice(match.index + match[0].length)
    );
  });
}

/** @param {Entry} entry */
export function frontMatter(entry) {
  return [
    "---",
    `title: ${JSON.stringify(entry.title)}`,
    `description: ${JSON.stringify(entry.description)}`,
    `editUrl: ${REPO}/edit/main/${entry.source}`,
    "---",
    "",
    "",
  ].join("\n");
}
