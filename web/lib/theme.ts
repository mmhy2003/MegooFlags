export type Theme = "light" | "dark" | "system";

export const THEME_COOKIE = "mf_theme";
export const PROJECT_COOKIE = "mf_project";
export const SIDEBAR_COOKIE = "sidebar_state";

const THEMES: readonly string[] = ["light", "dark", "system"];

export function parseTheme(value: string | undefined | null): Theme | null {
  return value && THEMES.includes(value) ? (value as Theme) : null;
}

/** The stored theme to copy into the cookie when the browser has none yet, else null. */
export function themeCookieToSet(
  existing: string | undefined,
  stored: Theme | undefined | null,
): Theme | null {
  return existing === undefined && stored ? stored : null;
}

/** A "true"/"false" cookie wins; otherwise the stored preference; otherwise open. */
export function sidebarOpen(
  cookie: string | undefined,
  collapsed: boolean | undefined,
): boolean {
  if (cookie === "true") return true;
  if (cookie === "false") return false;
  if (cookie === undefined && collapsed !== undefined) return !collapsed;
  return true;
}
