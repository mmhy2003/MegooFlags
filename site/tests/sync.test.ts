import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  frontMatter,
  loadManifest,
  rewriteLinks,
  stripTitle,
} from "../scripts/sync-lib.mjs";

const site = fileURLToPath(new URL("..", import.meta.url));
const blob = "https://github.com/mmhy2003/MegooFlags/blob/main";
const tree = "https://github.com/mmhy2003/MegooFlags/tree/main";

const manifest = [
  {
    source: "sdks/js/README.md",
    dest: "sdks/js.md",
    title: "JavaScript (browser)",
    description: "Browser SDK.",
  },
  {
    source: "spec/evaluation.md",
    dest: "reference/evaluation.md",
    title: "Evaluation spec",
    description: "How flags evaluate.",
  },
];

describe("rewriteLinks", () => {
  const rewrite = (md: string, from: string) =>
    rewriteLinks(md, from, manifest);

  it("points a synced README with an anchor at its site page", () => {
    expect(
      rewrite("[x](../js/README.md#bootstrap)", "sdks/react/README.md"),
    ).toBe("[x](/MegooFlags/sdks/js/#bootstrap)");
  });

  it("points a directory whose README is synced at its site page", () => {
    expect(rewrite("[x](../js)", "sdks/react/README.md")).toBe(
      "[x](/MegooFlags/sdks/js/)",
    );
  });

  it("points a synced file in the same directory at its site page", () => {
    expect(rewrite("see [spec](evaluation.md).", "spec/delivery.md")).toBe(
      "see [spec](/MegooFlags/reference/evaluation/).",
    );
  });

  it("points other files at GitHub blob URLs", () => {
    expect(rewrite("[x](../../AGENTS.md)", "sdks/go/README.md")).toBe(
      `[x](${blob}/AGENTS.md)`,
    );
    expect(rewrite("[x](LICENSE)", "sdks/go/README.md")).toBe(
      `[x](${blob}/sdks/go/LICENSE)`,
    );
  });

  it("keeps the anchor on GitHub URLs", () => {
    expect(rewrite("[x](AGENTS.md#9-local-checks)", "CONTRIBUTING.md")).toBe(
      `[x](${blob}/AGENTS.md#9-local-checks)`,
    );
  });

  it("points directories at GitHub tree URLs", () => {
    expect(rewrite("[x](conformance/)", "spec/README.md")).toBe(
      `[x](${tree}/spec/conformance)`,
    );
    expect(rewrite("[x](../go)", "sdks/react/README.md")).toBe(
      `[x](${tree}/sdks/go)`,
    );
  });

  it("keeps links with a scheme and anchor-only links", () => {
    const md =
      "[a](https://openfeature.dev) [b](mailto:a@b.c) [c](#offline-and-bootstrap)";
    expect(rewrite(md, "sdks/go/README.md")).toBe(md);
  });

  it("rewrites image links too", () => {
    expect(rewrite("![logo](../js)", "sdks/react/README.md")).toBe(
      "![logo](/MegooFlags/sdks/js/)",
    );
  });

  it("leaves fenced code untouched", () => {
    const md = [
      "[a](LICENSE)",
      "```md",
      "[b](LICENSE)",
      "```",
      "~~~",
      "[c](LICENSE)",
      "~~~",
      "[d](LICENSE)",
    ].join("\n");
    expect(rewrite(md, "sdks/go/README.md").split("\n")).toEqual([
      `[a](${blob}/sdks/go/LICENSE)`,
      "```md",
      "[b](LICENSE)",
      "```",
      "~~~",
      "[c](LICENSE)",
      "~~~",
      `[d](${blob}/sdks/go/LICENSE)`,
    ]);
  });
});

describe("stripTitle", () => {
  it("removes only the first level-one heading", () => {
    expect(stripTitle("# One\n\nText\n\n## Two\n\n# Three\n")).toBe(
      "Text\n\n## Two\n\n# Three\n",
    );
  });

  it("leaves a heading inside code alone", () => {
    expect(stripTitle("Intro\n```sh\n# comment\n```\n")).toBe(
      "Intro\n```sh\n# comment\n```\n",
    );
  });
});

describe("frontMatter", () => {
  it("writes the title, description and edit link", () => {
    expect(frontMatter(manifest[0])).toBe(
      [
        "---",
        'title: "JavaScript (browser)"',
        'description: "Browser SDK."',
        "editUrl: https://github.com/mmhy2003/MegooFlags/edit/main/sdks/js/README.md",
        "---",
        "",
        "",
      ].join("\n"),
    );
  });
});

describe("sync.mjs", () => {
  it("exits 1 and names a missing source", () => {
    const dir = mkdtempSync(join(tmpdir(), "mf-sync-"));
    const path = join(dir, "manifest.json");
    writeFileSync(
      path,
      JSON.stringify([
        {
          source: "sdks/nope/README.md",
          dest: "sdks/nope.md",
          title: "Nope",
          description: "Missing.",
        },
      ]),
    );
    const run = spawnSync(process.execPath, ["scripts/sync.mjs", path], {
      cwd: site,
      encoding: "utf8",
    });
    expect(run.status).toBe(1);
    expect(run.stderr).toContain("sdks/nope/README.md");
  });
});

describe("the real manifest", () => {
  const real = loadManifest(join(site, "sync.manifest.json"));

  it("lists the synced pages", () => {
    expect(real.map((e) => e.dest)).toContain("sdks/go.md");
    expect(real.map((e) => e.dest)).toContain("project/release-notes.md");
  });

  it.each(real.map((e) => e.dest))("git-ignores %s", (dest) => {
    const run = spawnSync(
      "git",
      ["check-ignore", "-q", `src/content/docs/${dest}`],
      { cwd: site },
    );
    expect(run.status).toBe(0);
  });
});
