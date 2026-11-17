import type { components } from "@/lib/api/schema";

export type SystemVersion = components["schemas"]["SystemVersionOut"];

/** The running version as the sidebar footer shows it: "1.0.0" becomes "v1.0.0". */
export function versionLabel(version: string): string {
  return `v${version}`;
}

/** The text of the update pill. */
export function updateLabel(update: { version: string }): string {
  return `Update: ${update.version}`;
}

/** The tooltip of the collapsed update pill. */
export function updateTooltip(update: { version: string }): string {
  return `Update available: ${update.version}`;
}
