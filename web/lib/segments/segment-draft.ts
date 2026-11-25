import type { components } from "@/lib/api/schema";
import {
  newClause,
  newId,
  type ClauseDraft,
  type DraftProblem,
} from "@/lib/flags/config-draft";

type S = components["schemas"];

export type SegmentRuleDraft = {
  id: string;
  description: string;
  clauses: ClauseDraft[];
};
/** A segment's keys and rules while they are edited; every edit returns a new draft. */
export type SegmentDraft = {
  included: string[];
  excluded: string[];
  rules: SegmentRuleDraft[];
};

/** The longest context key the API accepts. */
export const MAX_KEY_LENGTH = 256;

/** An editable draft of a saved segment's keys and rules. */
export function toDraft(s: S["SegmentOut"]): SegmentDraft {
  return {
    included: [...s.included],
    excluded: [...s.excluded],
    rules: s.rules.map((r) => ({
      id: newId(),
      description: r.description,
      clauses: r.clauses.map((cl) => ({
        id: newId(),
        attribute: cl.attribute,
        operator: cl.operator,
        values: [...cl.values],
        negate: cl.negate,
      })),
    })),
  };
}

/** The save request for a draft, client ids stripped. */
export function toConfigIn(d: SegmentDraft): S["SegmentConfigIn"] {
  return {
    included: [...d.included],
    excluded: [...d.excluded],
    rules: d.rules.map((r) => ({
      description: r.description,
      clauses: r.clauses.map((cl) => ({
        attribute: cl.attribute,
        operator: cl.operator,
        values: [...cl.values],
        negate: cl.negate,
      })),
    })),
  };
}

/** True when the draft's keys or rules differ from `base`. */
export function isDirty(base: SegmentDraft, d: SegmentDraft): boolean {
  return JSON.stringify(toConfigIn(base)) !== JSON.stringify(toConfigIn(d));
}

/** Sets a key list: trimmed, empty lines dropped, deduped, order kept. */
export function setKeys(
  d: SegmentDraft,
  list: "included" | "excluded",
  keys: string[],
): SegmentDraft {
  const values = [...new Set(keys.map((k) => k.trim()).filter(Boolean))];
  return { ...d, [list]: values };
}

function mapRule(
  d: SegmentDraft,
  ruleId: string,
  edit: (r: SegmentRuleDraft) => SegmentRuleDraft,
): SegmentDraft {
  return {
    ...d,
    rules: d.rules.map((r) => (r.id === ruleId ? edit(r) : r)),
  };
}

/** Adds a rule with one empty `key is one of` clause. */
export function addRule(d: SegmentDraft): SegmentDraft {
  const rule: SegmentRuleDraft = {
    id: newId(),
    description: "",
    clauses: [newClause()],
  };
  return { ...d, rules: [...d.rules, rule] };
}

export function updateRule(
  d: SegmentDraft,
  ruleId: string,
  patch: { description: string },
): SegmentDraft {
  return mapRule(d, ruleId, (r) => ({ ...r, ...patch }));
}

export function removeRule(d: SegmentDraft, ruleId: string): SegmentDraft {
  return { ...d, rules: d.rules.filter((r) => r.id !== ruleId) };
}

/** Moves a rule up (-1) or down (1); a no-op at either end. */
export function moveRule(
  d: SegmentDraft,
  ruleId: string,
  by: -1 | 1,
): SegmentDraft {
  const from = d.rules.findIndex((r) => r.id === ruleId);
  const to = from + by;
  if (from < 0 || to < 0 || to >= d.rules.length) return d;
  const rules = [...d.rules];
  [rules[from], rules[to]] = [rules[to]!, rules[from]!];
  return { ...d, rules };
}

export function addClause(d: SegmentDraft, ruleId: string): SegmentDraft {
  return mapRule(d, ruleId, (r) => ({
    ...r,
    clauses: [...r.clauses, newClause()],
  }));
}

export function updateClause(
  d: SegmentDraft,
  ruleId: string,
  clauseId: string,
  patch: Partial<Omit<ClauseDraft, "id">>,
): SegmentDraft {
  return mapRule(d, ruleId, (r) => ({
    ...r,
    clauses: r.clauses.map((cl) =>
      cl.id === clauseId ? { ...cl, ...patch } : cl,
    ),
  }));
}

/** Removes a clause, but never a rule's last one. */
export function removeClause(
  d: SegmentDraft,
  ruleId: string,
  clauseId: string,
): SegmentDraft {
  const rule = d.rules.find((r) => r.id === ruleId);
  if (!rule || rule.clauses.length <= 1) return d;
  return mapRule(d, ruleId, (r) => ({
    ...r,
    clauses: r.clauses.filter((cl) => cl.id !== clauseId),
  }));
}

function tooLong(key: string, path: string): DraftProblem[] {
  return key.length > MAX_KEY_LENGTH
    ? [{ path, message: `Keys can be at most ${MAX_KEY_LENGTH} characters` }]
    : [];
}

/** Problems to fix before Save, in page order (included, excluded, rules), with paths like the API's. */
export function validateDraft(d: SegmentDraft): DraftProblem[] {
  const problems: DraftProblem[] = [];
  d.included.forEach((k, i) => problems.push(...tooLong(k, `included[${i}]`)));
  const included = new Set(d.included);
  d.excluded.forEach((k, j) => {
    const path = `excluded[${j}]`;
    problems.push(...tooLong(k, path));
    if (included.has(k)) {
      problems.push({ path, message: `${k} is both included and excluded` });
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
  });
  return problems;
}

const FIELD = /^(?:(included|excluded)\b|(rules\[\d+\](?:\.clauses\[\d+\])?))/;

/** The field a problem path is shown under: a key list, a clause, a rule, or null for the page. */
export function problemField(path: string | null): string | null {
  const match = path ? FIELD.exec(path) : null;
  return match ? (match[1] ?? match[2] ?? null) : null;
}

/** The flags using a segment, each with its environments, in the order given. */
export function usageByFlag(
  usedBy: S["SegmentUsage"][],
): { flag: string; environments: string[] }[] {
  const byFlag = new Map<string, string[]>();
  for (const u of usedBy) {
    const environments = byFlag.get(u.flag);
    if (environments) environments.push(u.environment);
    else byFlag.set(u.flag, [u.environment]);
  }
  return [...byFlag].map(([flag, environments]) => ({ flag, environments }));
}

/** "Not used", "1 flag" or "n flags". */
export function usedByText(flagCount: number): string {
  if (flagCount === 0) return "Not used";
  return flagCount === 1 ? "1 flag" : `${flagCount} flags`;
}
