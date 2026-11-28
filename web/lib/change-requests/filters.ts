import type { components } from "@/lib/api/schema";
import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";

type Status = components["schemas"]["ChangeRequestStatus"];

/** The change request list's filters, as kept in the URL. */
export type CrFilters = { status: Status | "all"; env: string; flag: string };

/** Change requests per list page. */
const PAGE_SIZE = 50;

const STATUSES: readonly (Status | "all")[] = [
  "open",
  "applied",
  "rejected",
  "cancelled",
  "failed",
  "all",
];

function isStatus(value: string | null): value is Status | "all" {
  return STATUSES.includes(value as Status | "all");
}

/** The filters in the URL; a missing or unknown status is "open". */
export function crFiltersFromParams(p: URLSearchParams): CrFilters {
  const status = p.get("status");
  return {
    status: isStatus(status) ? status : "open",
    env: p.get("env") ?? "",
    flag: p.get("flag") ?? "",
  };
}

/** The filters as URL params, without the defaults. */
export function crFiltersToParams(f: CrFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.status !== "open") p.set("status", f.status);
  if (f.env) p.set("env", f.env);
  if (f.flag) p.set("flag", f.flag);
  return p;
}

/** The list query the API accepts; "all" sends no status. */
export function crListQuery(
  f: CrFilters,
  cursor?: string,
): {
  status?: Status;
  env?: string;
  flag?: string;
  cursor?: string;
  limit: number;
} {
  return {
    ...(f.status !== "all" && { status: f.status }),
    ...(f.env && { env: f.env }),
    ...(f.flag && { flag: f.flag }),
    ...(cursor && { cursor }),
    limit: PAGE_SIZE,
  };
}

/** The author can comment, and so can anyone who can change flags or approve in `env`. */
export function canComment(
  perms: MePermissions | null,
  env: string,
  isAuthor: boolean,
): boolean {
  return (
    isAuthor ||
    can(perms, "flag.toggle", env) ||
    can(perms, "flag.config.write", env) ||
    can(perms, "change.approve", env)
  );
}
