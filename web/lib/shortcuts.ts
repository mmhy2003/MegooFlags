type ShortcutTarget = { closest?: (selector: string) => unknown };

export type ShortcutEvent = {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  target?: ShortcutTarget | EventTarget | null;
};

/** ⌘K or Ctrl+K in any case, except inside a code editor, which keeps the keys for itself. */
export function isPaletteShortcut(e: ShortcutEvent): boolean {
  if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "k") return false;
  const target = e.target as ShortcutTarget | null | undefined;
  return !target?.closest?.("[data-code-editor]");
}
