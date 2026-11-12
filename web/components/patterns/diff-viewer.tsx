import { cn } from "cn";

import { lineDiff, type DiffLine } from "@/lib/diff";

const MARK = {
  add: { sign: "+", words: "added", tint: "bg-success/10" },
  remove: { sign: "−", words: "removed", tint: "bg-destructive/10" },
} as const;

function Pane({ title, lines }: { title: string; lines: DiffLine[] }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span aria-hidden className="text-sm font-medium">
        {title}
      </span>
      <pre
        tabIndex={0}
        role="region"
        aria-label={title}
        className="max-w-full overflow-x-auto rounded-lg border bg-muted py-3 font-mono text-sm"
      >
        <span className="inline-block min-w-full">
          {lines.map((line, i) => {
            const mark = line.kind === "same" ? null : MARK[line.kind];
            return (
              <span
                key={i}
                data-kind={line.kind}
                className={cn("block px-3", mark?.tint)}
              >
                <span aria-hidden className="inline-block w-4 select-none">
                  {mark?.sign ?? " "}
                </span>
                {mark && <span className="sr-only">{mark.words}</span>}
                {line.text}
              </span>
            );
          })}
        </span>
      </pre>
    </div>
  );
}

/**
 * Two texts compared line by line: Before (same and removed lines) beside After (same and
 * added lines) from 1024 px, stacked below it. Each change is marked by a sign and words,
 * not by its tint alone.
 */
export function DiffViewer({
  before,
  after,
  label,
}: {
  before: string;
  after: string;
  label?: string;
}) {
  const lines = lineDiff(before, after);
  return (
    <div
      role="group"
      aria-label={label ?? "Diff"}
      className="grid min-w-0 gap-3 lg:grid-cols-2"
    >
      <Pane title="Before" lines={lines.filter((l) => l.kind !== "add")} />
      <Pane title="After" lines={lines.filter((l) => l.kind !== "remove")} />
    </div>
  );
}
