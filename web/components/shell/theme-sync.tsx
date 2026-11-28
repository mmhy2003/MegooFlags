"use client";

import { useEffect, useRef } from "react";
import { useTheme } from "next-themes";

import { readCookie } from "@/lib/cookie";
import { THEME_UNSAVED_COOKIE, type Theme, themeToSync } from "@/lib/theme";

/** Applies the theme stored on the user once per load, so a choice made on another device
 * follows them here, unless this browser's last choice could not be saved. Later changes
 * come from the theme switcher. */
export function ThemeSync({ stored }: { stored: Theme | null | undefined }) {
  const { theme, setTheme } = useTheme();
  const synced = useRef(false);

  useEffect(() => {
    if (synced.current || !stored) return;
    synced.current = true;
    const next = themeToSync(
      theme,
      stored,
      readCookie(THEME_UNSAVED_COOKIE) !== undefined,
    );
    if (next) setTheme(next);
  }, [stored, theme, setTheme]);

  return null;
}
