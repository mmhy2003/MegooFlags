import type { components } from "@/lib/api/client";
import { EVENT_GROUPS } from "@/lib/integrations/groups";

type S = components["schemas"];

/** The Add or Edit integration form. `url` is blank on edit: the API never returns it. */
export type IntegrationDraft = {
  kind: S["IntegrationKind"];
  name: string;
  url: string;
  eventGroups: S["EventGroup"][];
  allEnvironments: boolean;
  environments: string[];
  enabled: boolean;
};

export function emptyDraft(kind: S["IntegrationKind"]): IntegrationDraft {
  return {
    kind,
    name: "",
    url: "",
    eventGroups: EVENT_GROUPS.map((g) => g.id),
    allEnvironments: true,
    environments: [],
    enabled: true,
  };
}

export function draftFrom(i: S["IntegrationOut"]): IntegrationDraft {
  return {
    kind: i.kind,
    name: i.name,
    url: "",
    eventGroups: i.event_groups,
    allEnvironments: i.all_environments,
    environments: i.environments,
    enabled: i.enabled,
  };
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1"]);

/** The URL problem, if any. http:// to a local host passes; the server decides whether it may. */
function urlError(kind: S["IntegrationKind"], text: string): string | null {
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return "Use an https:// URL";
  }
  const local = url.protocol === "http:" && LOCAL_HOSTS.has(url.hostname);
  if (url.protocol !== "https:" && !local) return "Use an https:// URL";
  if (kind === "slack" && url.hostname !== "hooks.slack.com") {
    return "Use a Slack incoming webhook URL (hooks.slack.com)";
  }
  return null;
}

/** Field errors for the form; an empty object means it can be sent. */
export function validateDraft(
  d: IntegrationDraft,
  editing: boolean,
): Partial<Record<keyof IntegrationDraft, string>> {
  const errors: Partial<Record<keyof IntegrationDraft, string>> = {};
  if (!d.name.trim()) errors.name = "Enter a name";
  const url = d.url.trim();
  if (url) {
    const error = urlError(d.kind, url);
    if (error) errors.url = error;
  } else if (!editing) {
    errors.url = "Enter a URL";
  }
  if (d.eventGroups.length === 0) {
    errors.eventGroups = "Choose at least one event group";
  }
  if (!d.allEnvironments && d.environments.length === 0) {
    errors.environments = "Choose at least one environment";
  }
  return errors;
}

export function toCreate(d: IntegrationDraft): S["IntegrationCreate"] {
  return {
    kind: d.kind,
    name: d.name.trim(),
    url: d.url.trim(),
    event_groups: d.eventGroups,
    all_environments: d.allEnvironments,
    environments: d.allEnvironments ? [] : d.environments,
    enabled: d.enabled,
  };
}

function sameSet(a: string[], b: string[]): boolean {
  const set = new Set(a);
  return set.size === new Set(b).size && b.every((x) => set.has(x));
}

/** Only the fields the user changed; a blank URL keeps the current one. */
export function toUpdate(
  d: IntegrationDraft,
  original: S["IntegrationOut"],
): S["IntegrationUpdate"] {
  const update: S["IntegrationUpdate"] = {};
  const name = d.name.trim();
  if (name !== original.name) update.name = name;
  const url = d.url.trim();
  if (url) update.url = url;
  if (!sameSet(d.eventGroups, original.event_groups)) {
    update.event_groups = d.eventGroups;
  }
  if (d.allEnvironments !== original.all_environments) {
    update.all_environments = d.allEnvironments;
  }
  if (!sameSet(d.environments, original.environments)) {
    update.environments = d.environments;
  }
  if (d.enabled !== original.enabled) update.enabled = d.enabled;
  return update;
}
