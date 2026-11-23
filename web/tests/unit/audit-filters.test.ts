import { describe, expect, it } from "vitest";

import {
  activeFilterCount,
  auditFiltersFromParams,
  auditFiltersToParams,
  auditQuery,
  EMPTY_AUDIT_FILTERS,
  exportUrl,
  type AuditFilterValues,
} from "@/lib/audit/filters";

const PROJECT = { kind: "project", project: "shop" } as const;
const ORG = { kind: "org" } as const;

function filters(f: Partial<AuditFilterValues>): AuditFilterValues {
  return { ...EMPTY_AUDIT_FILTERS, ...f };
}

describe("auditFiltersFromParams", () => {
  it("reads empty strings for missing params", () => {
    expect(auditFiltersFromParams(new URLSearchParams())).toEqual(
      EMPTY_AUDIT_FILTERS,
    );
  });

  it("round-trips with auditFiltersToParams", () => {
    const f = filters({
      action: "flag.toggle",
      actor: "admin@example.com",
      resource_type: "flag",
      resource_key: "checkout",
      project: "shop",
      env: "production",
      from: "2026-11-01",
      to: "2026-11-02",
    });
    const p = auditFiltersToParams(f, new URLSearchParams());
    expect(auditFiltersFromParams(p)).toEqual(f);
  });
});

describe("auditFiltersToParams", () => {
  it("keeps unrelated params and drops empty values", () => {
    const base = new URLSearchParams("tab=audit&action=flag.create");
    const p = auditFiltersToParams(filters({ actor: "ana" }), base);
    expect(p.toString()).toBe("tab=audit&actor=ana");
  });

  it("does not change the base params", () => {
    const base = new URLSearchParams("tab=audit");
    auditFiltersToParams(filters({ actor: "ana" }), base);
    expect(base.toString()).toBe("tab=audit");
  });
});

describe("auditQuery", () => {
  it("forces the scope's project, keeps env and ignores f.project", () => {
    const q = auditQuery(
      filters({ project: "other", env: "production", action: "flag.toggle" }),
      PROJECT,
    );
    expect(q).toEqual({
      project: "shop",
      env: "production",
      action: "flag.toggle",
    });
  });

  it("drops env without a project in the org scope", () => {
    expect(auditQuery(filters({ env: "production" }), ORG)).toEqual({});
    expect(
      auditQuery(filters({ project: "shop", env: "production" }), ORG),
    ).toEqual({ project: "shop", env: "production" });
  });

  it("sends whole local days, with an exclusive end", () => {
    const q = auditQuery(
      filters({ from: "2026-11-01", to: "2026-11-02" }),
      ORG,
    );
    expect(q).toEqual({
      from: new Date(2026, 10, 1).toISOString(),
      to: new Date(2026, 10, 3).toISOString(),
    });
  });

  it("ignores dates that do not parse", () => {
    expect(
      auditQuery(filters({ from: "soon", to: "2026-13-45" }), ORG),
    ).toEqual({});
  });
});

describe("exportUrl", () => {
  it("links the export with the query and the format", () => {
    const url = exportUrl(
      filters({ action: "flag.create" }),
      PROJECT,
      "ndjson",
    );
    expect(url.startsWith("/api/audit/export?")).toBe(true);
    expect(url.endsWith("format=ndjson")).toBe(true);
    const p = new URLSearchParams(url.split("?")[1]);
    expect(p.get("project")).toBe("shop");
    expect(p.get("action")).toBe("flag.create");
  });

  it("has only the format without filters", () => {
    expect(exportUrl(EMPTY_AUDIT_FILTERS, ORG, "csv")).toBe(
      "/api/audit/export?format=csv",
    );
  });
});

describe("activeFilterCount", () => {
  it("is 0 without filters", () => {
    expect(activeFilterCount(EMPTY_AUDIT_FILTERS, PROJECT)).toBe(0);
    expect(activeFilterCount(EMPTY_AUDIT_FILTERS, ORG)).toBe(0);
  });

  it("counts every filter that applies", () => {
    const f = filters({
      action: "flag.toggle",
      env: "production",
      from: "2026-11-01",
    });
    expect(activeFilterCount(f, PROJECT)).toBe(3);
  });

  it("does not count the project in the project scope", () => {
    expect(activeFilterCount(filters({ project: "other" }), PROJECT)).toBe(0);
    expect(activeFilterCount(filters({ project: "shop" }), ORG)).toBe(1);
  });

  it("does not count env without a project in the org scope", () => {
    expect(activeFilterCount(filters({ env: "production" }), ORG)).toBe(0);
  });
});
