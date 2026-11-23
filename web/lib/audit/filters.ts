/** Whose audit log a page shows: one project's, or the whole organization's. */
export type AuditScope = { kind: "project"; project: string } | { kind: "org" };

/** The audit log's filters, as kept in the URL; dates are "YYYY-MM-DD". */
export type AuditFilterValues = {
  action: string;
  actor: string;
  resource_type: string;
  resource_key: string;
  project: string;
  env: string;
  from: string;
  to: string;
};

/** No filters. */
export const EMPTY_AUDIT_FILTERS: AuditFilterValues = {
  action: "",
  actor: "",
  resource_type: "",
  resource_key: "",
  project: "",
  env: "",
  from: "",
  to: "",
};

const KEYS = Object.keys(EMPTY_AUDIT_FILTERS) as (keyof AuditFilterValues)[];

/** The filters in the URL; a missing one is "". */
export function auditFiltersFromParams(p: URLSearchParams): AuditFilterValues {
  const f = { ...EMPTY_AUDIT_FILTERS };
  for (const key of KEYS) f[key] = p.get(key) ?? "";
  return f;
}

/** `base` (such as `tab`) with the filters set and empty ones removed. */
export function auditFiltersToParams(
  f: AuditFilterValues,
  base: URLSearchParams,
): URLSearchParams {
  const p = new URLSearchParams(base);
  for (const key of KEYS) {
    if (f[key]) p.set(key, f[key]);
    else p.delete(key);
  }
  return p;
}

/** Local midnight of a "YYYY-MM-DD" date, `days` later; null when it does not parse. */
function localMidnight(value: string, days = 0): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]) - 1, Number(m[3])];
  const date = new Date(y, mo, d);
  if (date.getMonth() !== mo || date.getDate() !== d) return null;
  return new Date(y, mo, d + days);
}

/**
 * The query `GET /api/audit` accepts. A project scope forces its project; `env` is only
 * sent with a project; `from` is the start of that local day and `to` the start of the
 * day after it, because the API's `to` is exclusive.
 */
export function auditQuery(
  f: AuditFilterValues,
  scope: AuditScope,
): Record<string, string> {
  const project = scope.kind === "project" ? scope.project : f.project;
  const from = localMidnight(f.from);
  const to = localMidnight(f.to, 1);
  return {
    ...(f.action && { action: f.action }),
    ...(f.actor && { actor: f.actor }),
    ...(f.resource_type && { resource_type: f.resource_type }),
    ...(f.resource_key && { resource_key: f.resource_key }),
    ...(project && { project }),
    ...(project && f.env && { env: f.env }),
    ...(from && { from: from.toISOString() }),
    ...(to && { to: to.toISOString() }),
  };
}

/** The download link for every entry that matches the filters. */
export function exportUrl(
  f: AuditFilterValues,
  scope: AuditScope,
  format: "csv" | "ndjson",
): string {
  const p = new URLSearchParams(auditQuery(f, scope));
  p.set("format", format);
  return `/api/audit/export?${p.toString()}`;
}

/** How many filters the person set that apply in `scope` (not the scope's own project). */
export function activeFilterCount(
  f: AuditFilterValues,
  scope: AuditScope,
): number {
  const query = auditQuery(f, scope);
  const forced = scope.kind === "project" ? 1 : 0;
  return Object.keys(query).length - forced;
}
