import type { components } from "@/lib/api/client";

type S = components["schemas"];

/** Whole units since `iso`, largest first, e.g. "3 min". Null under 10 s. */
function age(iso: string, now: number): string | null {
  const seconds = Math.floor((now - Date.parse(iso)) / 1000);
  if (seconds < 10) return null;
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} d`;
}

export function relativeTime(iso: string | null, now = Date.now()): string {
  if (iso === null) return "Never";
  const text = age(iso, now);
  return text === null ? "just now" : `${text} ago`;
}

/** The snapshot column: the version a worker has applied, and how it compares. */
export function snapshotText(
  node: S["NodeOut"],
  current: number,
  now = Date.now(),
): string {
  if (node.snapshot_version === null) return "—";
  const version = `v${node.snapshot_version}`;
  if (node.status === "stale" && node.synced_at !== null) {
    return `${version} · ${age(node.synced_at, now) ?? "0 s"} old`;
  }
  if (node.syncing) return `${version} · syncing`;
  if (node.snapshot_version >= current) return `${version} · current`;
  return version;
}

/** The `.env` lines that join a worker to this control plane. */
export function envLines(origin: string, token: string): string {
  return [
    "MEGOOFLAGS_ROLE=worker",
    `MEGOOFLAGS_CONTROL_PLANE_URL=${origin}`,
    `MEGOOFLAGS_NODE_TOKEN=${token}`,
  ].join("\n");
}
