import { describe, expect, it } from "vitest";

import { lineDiff } from "@/lib/diff";

describe("lineDiff", () => {
  it("numbers same, removed and added lines on each side", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc")).toEqual([
      { kind: "same", text: "a", oldNo: 1, newNo: 1 },
      { kind: "remove", text: "b", oldNo: 2, newNo: null },
      { kind: "add", text: "B", oldNo: null, newNo: 2 },
      { kind: "same", text: "c", oldNo: 3, newNo: 3 },
    ]);
  });

  it("marks every line of identical input as same", () => {
    expect(lineDiff("x\ny", "x\ny")).toEqual([
      { kind: "same", text: "x", oldNo: 1, newNo: 1 },
      { kind: "same", text: "y", oldNo: 2, newNo: 2 },
    ]);
  });

  it("gives one added line when the input was empty", () => {
    expect(lineDiff("", "x")).toEqual([
      { kind: "add", text: "x", oldNo: null, newNo: 1 },
    ]);
  });

  it("adds no empty line for a trailing newline", () => {
    expect(lineDiff("a\n", "a\nb\n")).toEqual([
      { kind: "same", text: "a", oldNo: 1, newNo: 1 },
      { kind: "add", text: "b", oldNo: null, newNo: 2 },
    ]);
  });
});
