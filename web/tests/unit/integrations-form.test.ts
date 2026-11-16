import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/client";
import {
  draftField,
  draftFrom,
  emptyDraft,
  type IntegrationDraft,
  toCreate,
  toUpdate,
  validateDraft,
} from "@/lib/integrations/form";

type S = components["schemas"];

const original: S["IntegrationOut"] = {
  id: "00000000-0000-0000-0000-000000000001",
  kind: "slack",
  name: "Alerts",
  url_hint: "https://hooks.slack.com/…",
  event_groups: ["flags", "segments"],
  all_environments: false,
  environments: ["production"],
  enabled: true,
  last_delivery_at: null,
  last_delivery_ok: null,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};

const draft: IntegrationDraft = {
  kind: "webhook",
  name: "Deploys",
  url: "https://example.com/hook",
  eventGroups: ["flags"],
  allEnvironments: true,
  environments: [],
  enabled: true,
};

describe("emptyDraft", () => {
  it.each(["webhook", "slack", "teams"] as const)(
    "starts a %s with every group and environment, enabled",
    (kind) => {
      expect(emptyDraft(kind)).toEqual({
        kind,
        name: "",
        url: "",
        eventGroups: ["flags", "targeting", "segments", "change_requests"],
        allEnvironments: true,
        environments: [],
        enabled: true,
      });
    },
  );
});

describe("draftFrom", () => {
  it.each([
    [original],
    [{ ...original, kind: "teams", enabled: false, all_environments: true }],
  ] as const)("copies the integration without its URL", (i) => {
    expect(draftFrom(i)).toEqual({
      kind: i.kind,
      name: i.name,
      url: "",
      eventGroups: i.event_groups,
      allEnvironments: i.all_environments,
      environments: i.environments,
      enabled: i.enabled,
    });
  });
});

describe("validateDraft", () => {
  it.each<[string, Partial<IntegrationDraft>, boolean, Record<string, string>]>(
    [
      ["a valid webhook", {}, false, {}],
      ["a blank name", { name: "  " }, false, { name: "Enter a name" }],
      ["a missing URL", { url: "" }, false, { url: "Enter a URL" }],
      ["a missing URL when editing", { url: "" }, true, {}],
      [
        "a plain http URL",
        { url: "http://example.com/hook" },
        false,
        { url: "Use an https:// URL" },
      ],
      [
        "a plain http URL when editing",
        { url: "http://example.com/hook" },
        true,
        { url: "Use an https:// URL" },
      ],
      ["not a URL", { url: "hooks" }, false, { url: "Use an https:// URL" }],
      ["http to localhost", { url: "http://localhost:8080/hook" }, false, {}],
      ["http to 127.0.0.1", { url: "http://127.0.0.1:9000/x" }, false, {}],
      [
        "http to a localhost lookalike",
        { url: "http://localhost.example.com/x" },
        false,
        { url: "Use an https:// URL" },
      ],
      [
        "a Slack URL on another host",
        { kind: "slack", url: "https://example.com/services/x" },
        false,
        { url: "Use a Slack incoming webhook URL (hooks.slack.com)" },
      ],
      [
        "a Slack URL on hooks.slack.com",
        { kind: "slack", url: "https://hooks.slack.com/services/T/B/x" },
        false,
        {},
      ],
      ["a Slack edit without a URL", { kind: "slack", url: "" }, true, {}],
      [
        "a Teams URL on any https host",
        { kind: "teams", url: "https://prod.westus.logic.azure.com/x" },
        false,
        {},
      ],
      [
        "no event groups",
        { eventGroups: [] },
        false,
        { eventGroups: "Choose at least one event group" },
      ],
      [
        "no environments when not all",
        { allEnvironments: false, environments: [] },
        false,
        { environments: "Choose at least one environment" },
      ],
      [
        "some environments",
        { allEnvironments: false, environments: ["production"] },
        false,
        {},
      ],
      [
        "several problems",
        { name: "", url: "", eventGroups: [] },
        false,
        {
          name: "Enter a name",
          url: "Enter a URL",
          eventGroups: "Choose at least one event group",
        },
      ],
    ],
  )("reports %s", (_, change, editing, errors) => {
    expect(validateDraft({ ...draft, ...change }, editing)).toEqual(errors);
  });
});

describe("toCreate", () => {
  it.each<[Partial<IntegrationDraft>, S["IntegrationCreate"]]>([
    [
      {},
      {
        kind: "webhook",
        name: "Deploys",
        url: "https://example.com/hook",
        event_groups: ["flags"],
        all_environments: true,
        environments: [],
        enabled: true,
      },
    ],
    [
      {
        kind: "teams",
        name: "  Team chat ",
        url: " https://example.com/t ",
        allEnvironments: false,
        environments: ["staging"],
        enabled: false,
      },
      {
        kind: "teams",
        name: "Team chat",
        url: "https://example.com/t",
        event_groups: ["flags"],
        all_environments: false,
        environments: ["staging"],
        enabled: false,
      },
    ],
    [
      { allEnvironments: true, environments: ["staging"] },
      {
        kind: "webhook",
        name: "Deploys",
        url: "https://example.com/hook",
        event_groups: ["flags"],
        all_environments: true,
        environments: [],
        enabled: true,
      },
    ],
  ])("maps the draft %#", (change, create) => {
    expect(toCreate({ ...draft, ...change })).toEqual(create);
  });
});

describe("toUpdate", () => {
  it.each<[string, Partial<IntegrationDraft>, S["IntegrationUpdate"]]>([
    ["nothing changed", {}, {}],
    ["a new name", { name: "Ops " }, { name: "Ops" }],
    ["the same name with spaces", { name: " Alerts " }, {}],
    [
      "a new URL",
      { url: " https://hooks.slack.com/services/new " },
      { url: "https://hooks.slack.com/services/new" },
    ],
    ["a blank URL", { url: "  " }, {}],
    [
      "new groups",
      { eventGroups: ["flags", "targeting"] },
      { event_groups: ["flags", "targeting"] },
    ],
    [
      "the same groups in another order",
      { eventGroups: ["segments", "flags"] },
      {},
    ],
    ["all environments", { allEnvironments: true }, { all_environments: true }],
    [
      "other environments",
      { environments: ["production", "staging"] },
      { environments: ["production", "staging"] },
    ],
    ["the same environments", { environments: ["production"] }, {}],
    ["paused", { enabled: false }, { enabled: false }],
  ])("sends only what changed: %s", (_, change, update) => {
    expect(toUpdate({ ...draftFrom(original), ...change }, original)).toEqual(
      update,
    );
  });
});

describe("draftField", () => {
  it.each([
    ["name", "name"],
    ["url", "url"],
    ["event_groups", "eventGroups"],
    ["event_groups[1]", "eventGroups"],
    ["all_environments", "allEnvironments"],
    ["environments", "environments"],
    ["environments[0]", "environments"],
    ["enabled", "enabled"],
  ] as const)("maps %s to %s", (path, field) => {
    expect(draftField(path)).toBe(field);
  });

  it.each([null, "", "secret", "kind"])("gives null for %s", (path) => {
    expect(draftField(path)).toBeNull();
  });
});
