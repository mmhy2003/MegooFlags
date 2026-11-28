import type { components, paths } from "@/lib/api/schema";

type S = components["schemas"];

/** A flag's lifecycle state, as the list reports it. */
export type Lifecycle = S["FlagSummary"]["lifecycle"];

/** The flag list's filters and page cursor, as kept in the URL. */
export type FlagFilters = {
  q: string;
  tags: string[];
  owner: string;
  lifecycle: Lifecycle | "";
  env: string;
  enabled: "" | "on" | "off";
  cursor: string;
};

/** The flag list query the API accepts. */
export type FlagListQuery = NonNullable<
  paths["/api/projects/{project}/flags"]["get"]["parameters"]["query"]
>;

/** No filters, first page. */
export const EMPTY_FILTERS: FlagFilters = {
  q: "",
  tags: [],
  owner: "",
  lifecycle: "",
  env: "",
  enabled: "",
  cursor: "",
};

/** Flags per list page. */
export const PAGE_SIZE = 50;

const LIFECYCLES: readonly Lifecycle[] = [
  "new",
  "active",
  "permanent",
  "stale",
  "archived",
];

function oneOf<T extends string>(
  value: string | null,
  allowed: readonly T[],
): T | "" {
  return (allowed as readonly string[]).includes(value ?? "")
    ? (value as T)
    : "";
}

/** Reads the filters from the URL; invalid lifecycle and enabled values become empty. */
export function filtersFromParams(p: URLSearchParams): FlagFilters {
  return {
    q: p.get("q") ?? "",
    tags: p.getAll("tag"),
    owner: p.get("owner") ?? "",
    lifecycle: oneOf(p.get("lifecycle"), LIFECYCLES),
    env: p.get("env") ?? "",
    enabled: oneOf(p.get("enabled"), ["on", "off"] as const),
    cursor: p.get("cursor") ?? "",
  };
}

/** Writes the non-default filters to URL params, so the defaults add no query string. */
export function filtersToParams(f: FlagFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  for (const tag of f.tags) p.append("tag", tag);
  if (f.owner) p.set("owner", f.owner);
  if (f.lifecycle) p.set("lifecycle", f.lifecycle);
  if (f.env) p.set("env", f.env);
  if (f.enabled) p.set("enabled", f.enabled);
  if (f.cursor) p.set("cursor", f.cursor);
  return p;
}

/** Applies a change and goes back to the first page, unless the change sets the cursor. */
export function withFilter(
  f: FlagFilters,
  patch: Partial<FlagFilters>,
): FlagFilters {
  return { ...f, cursor: "", ...patch };
}

/** The list query for the filters: empties omitted, env and enabled only together. */
export function filtersToQuery(f: FlagFilters): FlagListQuery {
  const query: FlagListQuery = {};
  if (f.q) query.q = f.q;
  if (f.tags.length > 0) query.tag = f.tags;
  if (f.owner) query.owner = f.owner;
  if (f.lifecycle) query.lifecycle = f.lifecycle;
  if (f.env && f.enabled) {
    query.env = f.env;
    query.enabled = f.enabled === "on";
  }
  if (f.cursor) query.cursor = f.cursor;
  query.limit = PAGE_SIZE;
  return query;
}

/** How many filters narrow the list: each tag, owner, lifecycle and the environment status; not the search. */
export function activeFilterCount(f: FlagFilters): number {
  return (
    f.tags.length +
    (f.owner ? 1 : 0) +
    (f.lifecycle ? 1 : 0) +
    (f.env && f.enabled ? 1 : 0)
  );
}
