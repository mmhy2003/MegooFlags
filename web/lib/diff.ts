import { diffLines } from "diff";

export type DiffLine = {
  kind: "add" | "remove" | "same";
  text: string;
  oldNo: number | null;
  newNo: number | null;
};

/** The lines of one diff chunk, without the empty line a trailing newline would give. */
function chunkLines(value: string): string[] {
  const lines = value.split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines;
}

/** A line-by-line diff of two texts, with each line's number on the side(s) it appears on. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const out: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  for (const change of diffLines(before, after)) {
    for (const text of chunkLines(change.value)) {
      if (change.added) {
        out.push({ kind: "add", text, oldNo: null, newNo: ++newNo });
      } else if (change.removed) {
        out.push({ kind: "remove", text, oldNo: ++oldNo, newNo: null });
      } else {
        out.push({ kind: "same", text, oldNo: ++oldNo, newNo: ++newNo });
      }
    }
  }
  return out;
}
