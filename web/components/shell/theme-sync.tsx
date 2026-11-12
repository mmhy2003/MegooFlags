"use client";

import { useEffect, useRef } from "react";
import { useTheme } from "next-themes";

import type { Theme } from "@/lib/theme";

/** Applies the theme stored on the user once per load, so a choice made on another device
 * follows them here. Later changes come from the theme switcher. */
export function ThemeSync({ stored }: { stored: Theme | null | undefined }) {
  const { theme, setTheme } = useTheme();
  const synced = useRef(false);

  useEffect(() => {
    if (synced.current || !stored) return;
    synced.current = true;
    if (theme !== stored) setTheme(stored);
  }, [stored, theme, setTheme]);

  return null;
}
