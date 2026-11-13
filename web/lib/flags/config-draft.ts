import type { components } from "@/lib/api/schema";
import { weightsValid } from "@/lib/flags/weights";

type S = components["schemas"];
type Scalar = string | number | boolean;

/** What a rule or the default rule serves. */
export type ServeDraft =
  | { mode: "variation"; variation: string }
  | { mode: "rollout"; bucketBy: string; weights: S["WeightIn"][] };
export type ClauseDraft = {
  id: string;
  attribute: string;
  operator: S["Operator"];
  values: Scalar[];
  negate: boolean;
};
export type RuleDraft = {
  id: string;
  description: string;
  clauses: ClauseDraft[];
  serve: ServeDraft;
};
export type PrereqDraft = { id: string; flag: string; variation: string };
/** An environment's targeting config while it is edited; every edit returns a new draft. */
export type ConfigDraft = {
  version: number;
  enabled: boolean;
  /** Context keys by variation; every variation of the flag has a list. */
  targets: Record<string, string[]>;
  rules: RuleDraft[];
  fallthrough: ServeDraft;
  prerequisites: PrereqDraft[];
};
export type DraftProblem = { path: string; message: string };

let lastId = 0;
function newId(): string {
  lastId += 1;
  return `d${lastId}`;
}

function toServe(s: S["Serve"]): ServeDraft {
  return s.rollout
    ? {
        mode: "rollout",
        bucketBy: s.rollout.bucket_by,
        weights: s.rollout.weights.map((w) => ({ ...w })),
      }
    : { mode: "variation", variation: s.variation ?? "" };
}

function fromServe(s: ServeDraft): S["Serve"] {
  return s.mode === "rollout"
    ? {
        rollout: {
          bucket_by: s.bucketBy,
          weights: s.weights.map((w) => ({ ...w })),
        },
      }
    : { variation: s.variation };
}

function newClause(): ClauseDraft {
  return {
    id: newId(),
    attribute: "key",
    operator: "in",
    values: [],
    negate: false,
  };
}

/** An editable draft of a saved config, with a target list for each of `variationKeys`. */
export function toDraft(
  c: S["FlagConfigOut"],
  variationKeys: string[],
): ConfigDraft {
  const targets: Record<string, string[]> = {};
  for (const key of variationKeys) targets[key] = [];
  for (const t of c.targets) targets[t.variation] = [...t.values];
  return {
    version: c.version,
    enabled: c.enabled,
    targets,
    rules: c.rules.map((r) => ({
      id: newId(),
      description: r.description,
      clauses: r.clauses.map((cl) => ({
        id: newId(),
        attribute: cl.attribute,
        operator: cl.operator,
        values: [...cl.values],
        negate: cl.negate,
      })),
      serve: toServe(r),
    })),
    fallthrough: toServe(c.fallthrough),
    prerequisites: c.prerequisites.map((p) => ({ id: newId(), ...p })),
  };
}

/** The save request for a draft: ids stripped, empty target lists dropped. */
export function toConfigIn(d: ConfigDraft): S["FlagConfigIn"] {
  return {
    enabled: d.enabled,
    version: d.version,
    targets: Object.entries(d.targets)
      .filter(([, values]) => values.length > 0)
      .map(([variation, values]) => ({ variation, values: [...values] })),
    rules: d.rules.map((r) => ({
      description: r.description,
      clauses: r.clauses.map((cl) => ({
        attribute: cl.attribute,
        operator: cl.operator,
        values: [...cl.values],
        negate: cl.negate,
      })),
      ...fromServe(r.serve),
    })),
    prerequisites: d.prerequisites.map((p) => ({
      flag: p.flag,
      variation: p.variation,
    })),
    fallthrough: fromServe(d.fallthrough),
  };
}

/** The targeting as JSON (2 spaces), without the version and on/off state, for diffs. */
export function configText(d: ConfigDraft): string {
  const c = toConfigIn(d);
  return JSON.stringify(
    {
      targets: c.targets,
      rules: c.rules,
      prerequisites: c.prerequisites,
      fallthrough: c.fallthrough,
    },
    null,
    2,
  );
}

/** True when the draft's targeting differs from `base`; the on/off state and version don't count. */
export function isDirty(base: ConfigDraft, d: ConfigDraft): boolean {
  return configText(base) !== configText(d);
}

/** The draft with a newer on/off state and version, e.g. after a toggle. */
export function withState(
  d: ConfigDraft,
  state: { enabled: boolean; version: number },
): ConfigDraft {
  return { ...d, enabled: state.enabled, version: state.version };
}

function mapRule(
  d: ConfigDraft,
  ruleId: string,
  edit: (r: RuleDraft) => RuleDraft,
): ConfigDraft {
  return {
    ...d,
    rules: d.rules.map((r) => (r.id === ruleId ? edit(r) : r)),
  };
}

/** Adds a rule with one empty `key is one of` clause, serving `firstVariation`. */
export function addRule(d: ConfigDraft, firstVariation: string): ConfigDraft {
  const rule: RuleDraft = {
    id: newId(),
    description: "",
    clauses: [newClause()],
    serve: { mode: "variation", variation: firstVariation },
  };
  return { ...d, rules: [...d.rules, rule] };
}

export function updateRule(
  d: ConfigDraft,
  ruleId: string,
  patch: Partial<Omit<RuleDraft, "id">>,
): ConfigDraft {
  return mapRule(d, ruleId, (r) => ({ ...r, ...patch }));
}

export function removeRule(d: ConfigDraft, ruleId: string): ConfigDraft {
  return { ...d, rules: d.rules.filter((r) => r.id !== ruleId) };
}

/** Moves a rule up (-1) or down (1); a no-op at either end. */
export function moveRule(
  d: ConfigDraft,
  ruleId: string,
  by: -1 | 1,
): ConfigDraft {
  const from = d.rules.findIndex((r) => r.id === ruleId);
  const to = from + by;
  if (from < 0 || to < 0 || to >= d.rules.length) return d;
  const rules = [...d.rules];
  [rules[from], rules[to]] = [rules[to]!, rules[from]!];
  return { ...d, rules };
}

export function addClause(d: ConfigDraft, ruleId: string): ConfigDraft {
  return mapRule(d, ruleId, (r) => ({
    ...r,
    clauses: [...r.clauses, newClause()],
  }));
}

export function updateClause(
  d: ConfigDraft,
  ruleId: string,
  clauseId: string,
  patch: Partial<Omit<ClauseDraft, "id">>,
): ConfigDraft {
  return mapRule(d, ruleId, (r) => ({
    ...r,
    clauses: r.clauses.map((cl) =>
      cl.id === clauseId ? { ...cl, ...patch } : cl,
    ),
  }));
}

/** Removes a clause, but never a rule's last one. */
export function removeClause(
  d: ConfigDraft,
  ruleId: string,
  clauseId: string,
): ConfigDraft {
  const rule = d.rules.find((r) => r.id === ruleId);
  if (!rule || rule.clauses.length <= 1) return d;
  return mapRule(d, ruleId, (r) => ({
    ...r,
    clauses: r.clauses.filter((cl) => cl.id !== clauseId),
  }));
}

/** Sets the keys served `variation` (trimmed, deduped) and removes them from other variations. */
export function setTargets(
  d: ConfigDraft,
  variation: string,
  keys: string[],
): ConfigDraft {
  const values = [...new Set(keys.map((k) => k.trim()).filter(Boolean))];
  const taken = new Set(values);
  const targets: Record<string, string[]> = {};
  for (const [v, list] of Object.entries(d.targets)) {
    targets[v] = list.filter((k) => !taken.has(k));
  }
  targets[variation] = values;
  return { ...d, targets };
}

export function addPrerequisite(d: ConfigDraft): ConfigDraft {
  return {
    ...d,
    prerequisites: [
      ...d.prerequisites,
      { id: newId(), flag: "", variation: "" },
    ],
  };
}

export function updatePrerequisite(
  d: ConfigDraft,
  id: string,
  patch: Partial<Omit<PrereqDraft, "id">>,
): ConfigDraft {
  return {
    ...d,
    prerequisites: d.prerequisites.map((p) =>
      p.id === id ? { ...p, ...patch } : p,
    ),
  };
}

export function removePrerequisite(d: ConfigDraft, id: string): ConfigDraft {
  return {
    ...d,
    prerequisites: d.prerequisites.filter((p) => p.id !== id),
  };
}

export function setFallthrough(d: ConfigDraft, serve: ServeDraft): ConfigDraft {
  return { ...d, fallthrough: serve };
}

function serveProblems(serve: ServeDraft, path: string): DraftProblem[] {
  return serve.mode === "rollout" && !weightsValid(serve.weights)
    ? [
        {
          path: `${path}.rollout.weights`,
          message: "Weights must add up to 100 %",
        },
      ]
    : [];
}

/** Problems to fix before Review, in editor order, with paths like the API's. */
export function validateDraft(d: ConfigDraft): DraftProblem[] {
  const problems: DraftProblem[] = [];
  d.prerequisites.forEach((p, i) => {
    if (!p.flag || !p.variation) {
      problems.push({
        path: `prerequisites[${i}]`,
        message: "Choose a flag and a variation",
      });
    }
  });
  d.rules.forEach((r, i) => {
    r.clauses.forEach((cl, j) => {
      if (cl.values.length === 0) {
        problems.push({
          path: `rules[${i}].clauses[${j}].values`,
          message: "Add at least one value",
        });
      }
    });
    problems.push(...serveProblems(r.serve, `rules[${i}]`));
  });
  problems.push(...serveProblems(d.fallthrough, "fallthrough"));
  return problems;
}
