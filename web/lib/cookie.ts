const YEAR = 60 * 60 * 24 * 365;

/** Browser only. Sets a site-wide cookie for a year, or deletes it when `value` is null. */
export function writeCookie(name: string, value: string | null): void {
  document.cookie =
    value === null
      ? `${name}=; path=/; max-age=0; samesite=lax`
      : `${name}=${value}; path=/; max-age=${YEAR}; samesite=lax`;
}

/** Browser only. */
export function readCookie(name: string): string | undefined {
  for (const part of document.cookie.split("; ")) {
    const eq = part.indexOf("=");
    if (part.slice(0, eq) === name) return part.slice(eq + 1);
  }
  return undefined;
}
