"use client";

import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { ClauseRow } from "@/components/flags/clause-row";
import { ServeEditor } from "@/components/flags/serve-editor";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import {
  addClause,
  addPrerequisite,
  addRule,
  type ConfigDraft,
  moveRule,
  type PrereqDraft,
  removeClause,
  removePrerequisite,
  removeRule,
  type RuleDraft,
  setFallthrough,
  setTargets,
  updateClause,
  updatePrerequisite,
  updateRule,
} from "@/lib/flags/config-draft";
import { must } from "@/lib/flags/errors";

type FlagOut = components["schemas"]["FlagOut"];

/**
 * The editor field a problem path belongs to, such as "prerequisites[0]" for
 * "prerequisites[0].variation"; null when the editor has no field for it.
 */
export function problemField(path: string | null): string | null {
  if (!path) return null;
  const prerequisite = path.match(/^prerequisites\[\d+\]/);
  if (prerequisite) return prerequisite[0];
  const clause = path.match(/^rules\[\d+\]\.clauses\[\d+\]/);
  if (clause) return clause[0];
  const rule = path.match(/^rules\[\d+\]/);
  if (rule)
    return path.startsWith(`${rule[0]}.rollout.weights`)
      ? `${rule[0]}.rollout.weights`
      : rule[0];
  if (path.startsWith("fallthrough.rollout.weights"))
    return "fallthrough.rollout.weights";
  if (path.startsWith("fallthrough")) return "fallthrough";
  if (path.startsWith("targets")) return "targets";
  return null;
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-4"
    >
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function FieldError({ id, error }: { id: string; error: string | undefined }) {
  if (!error) return null;
  return (
    <p id={id} className="text-sm text-destructive">
      {error}
    </p>
  );
}

/** One prerequisite: a flag of the project and the variation it must serve. */
function PrerequisiteRow({
  project,
  index,
  prerequisite,
  flags,
  readOnly,
  error,
  onChange,
  onRemove,
}: {
  project: string;
  index: number;
  prerequisite: PrereqDraft;
  /** The project's other flags, or null while they load. */
  flags: string[] | null;
  readOnly: boolean;
  error: string | undefined;
  onChange: (patch: Partial<Omit<PrereqDraft, "id">>) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const n = index + 1;
  const chosen = prerequisite.flag;
  const target = useApiData(chosen ? `flag:${project}/${chosen}` : null, () =>
    must(
      api.GET("/api/projects/{project}/flags/{flag}", {
        params: { path: { project, flag: chosen } },
      }),
    ),
  );
  const flagOptions =
    flags && (!chosen || flags.includes(chosen))
      ? flags
      : [...(flags ?? []), ...(chosen ? [chosen] : [])];
  const variations = target.data?.variations.map((v) => v.key) ?? [];
  if (prerequisite.variation && !variations.includes(prerequisite.variation))
    variations.push(prerequisite.variation);
  const errorId = `${id}-error`;
  const invalid = error
    ? { "aria-invalid": true, "aria-describedby": errorId }
    : {};

  return (
    <li className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-0 flex-1 basis-48 flex-col gap-2">
          <Label htmlFor={`${id}-flag`}>Prerequisite {n} flag</Label>
          <NativeSelect
            id={`${id}-flag`}
            value={chosen}
            disabled={readOnly}
            onChange={(e) => onChange({ flag: e.target.value, variation: "" })}
            {...invalid}
          >
            <option value="">Choose a flag</option>
            {flagOptions.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex min-w-0 flex-1 basis-40 flex-col gap-2">
          <Label htmlFor={`${id}-variation`}>Prerequisite {n} variation</Label>
          <NativeSelect
            id={`${id}-variation`}
            value={prerequisite.variation}
            disabled={readOnly || !chosen}
            onChange={(e) => onChange({ variation: e.target.value })}
            {...invalid}
          >
            <option value="">Choose a variation</option>
            {variations.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </NativeSelect>
        </div>
        {!readOnly && (
          <Button
            variant="outline"
            size="icon"
            className="min-h-11 min-w-11"
            aria-label={`Remove prerequisite ${n}`}
            onClick={onRemove}
          >
            <Trash2 aria-hidden />
          </Button>
        )}
      </div>
      <FieldError id={errorId} error={error} />
    </li>
  );
}

/** The context keys served one variation, one per line; applied to the draft on blur. */
function TargetsField({
  variation,
  keys,
  readOnly,
  onApply,
}: {
  variation: string;
  keys: string[];
  readOnly: boolean;
  onApply: (keys: string[]) => void;
}) {
  const id = useId();
  const joined = keys.join("\n");
  const [text, setText] = useState(joined);
  const [shown, setShown] = useState(joined);
  // Show the draft's keys when they change from outside the field (Discard, another field).
  if (shown !== joined) {
    setShown(joined);
    setText(joined);
  }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>Context keys served {variation}</Label>
      <Textarea
        id={id}
        className="min-h-20 font-mono text-base"
        value={text}
        readOnly={readOnly}
        placeholder="One key per line"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => onApply(text.split("\n"))}
      />
    </div>
  );
}

type MoveButtons = Map<string, HTMLButtonElement | null>;

/** One rule: a description, its clauses (all must match), what it serves, and move and remove. */
function RuleCard({
  rule,
  index,
  count,
  flag,
  segments,
  readOnly,
  problems,
  moveButtons,
  onChange,
  onMove,
  onRemove,
}: {
  rule: RuleDraft;
  index: number;
  count: number;
  flag: FlagOut;
  segments: string[];
  readOnly: boolean;
  problems: Record<string, string>;
  /** The move buttons by `<rule id>:up|down`, so focus can follow a moved rule. */
  moveButtons: React.RefObject<MoveButtons>;
  onChange: (edit: (d: ConfigDraft) => ConfigDraft) => void;
  onMove: (by: -1 | 1) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const n = index + 1;
  const path = `rules[${index}]`;
  const button = (dir: "up" | "down") => (el: HTMLButtonElement | null) => {
    moveButtons.current.set(`${rule.id}:${dir}`, el);
  };

  return (
    <Card
      role="group"
      id={`rule-${n}`}
      aria-labelledby={`${id}-title`}
      className="min-w-0 px-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`${id}-title`} className="font-semibold">
          Rule {n}
        </h3>
        {!readOnly && (
          <div className="flex gap-2">
            <Button
              ref={button("up")}
              variant="outline"
              size="icon"
              className="min-h-11 min-w-11"
              aria-label={`Move rule ${n} up`}
              disabled={index === 0}
              onClick={() => onMove(-1)}
            >
              <ArrowUp aria-hidden />
            </Button>
            <Button
              ref={button("down")}
              variant="outline"
              size="icon"
              className="min-h-11 min-w-11"
              aria-label={`Move rule ${n} down`}
              disabled={index === count - 1}
              onClick={() => onMove(1)}
            >
              <ArrowDown aria-hidden />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="min-h-11 min-w-11"
              aria-label={`Remove rule ${n}`}
              onClick={onRemove}
            >
              <Trash2 aria-hidden />
            </Button>
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-description`}>Description</Label>
        <Input
          id={`${id}-description`}
          className="h-11 text-base"
          value={rule.description}
          readOnly={readOnly}
          onChange={(e) =>
            onChange((d) =>
              updateRule(d, rule.id, { description: e.target.value }),
            )
          }
        />
      </div>
      <ol className="flex min-w-0 flex-col gap-3">
        {rule.clauses.map((clause, j) => (
          <li key={clause.id} className="flex min-w-0 flex-col gap-3">
            {j > 0 && (
              <span className="text-sm font-medium text-muted-foreground uppercase">
                and
              </span>
            )}
            <ClauseRow
              clause={clause}
              segments={segments}
              readOnly={readOnly}
              canRemove={rule.clauses.length > 1}
              error={problems[`${path}.clauses[${j}]`]}
              onChange={(patch) =>
                onChange((d) => updateClause(d, rule.id, clause.id, patch))
              }
              onRemove={() =>
                onChange((d) => removeClause(d, rule.id, clause.id))
              }
            />
          </li>
        ))}
      </ol>
      {!readOnly && (
        <Button
          variant="outline"
          className="min-h-11 w-fit"
          onClick={() => onChange((d) => addClause(d, rule.id))}
        >
          <Plus aria-hidden />
          Add clause
        </Button>
      )}
      <ServeEditor
        serve={rule.serve}
        onChange={(serve) => onChange((d) => updateRule(d, rule.id, { serve }))}
        type={flag.type}
        variations={flag.variations.map((v) => v.key)}
        readOnly={readOnly}
        weightsError={problems[`${path}.rollout.weights`]}
      />
      <FieldError id={`${id}-error`} error={problems[path]} />
    </Card>
  );
}

/**
 * The Targeting tab's editor of one environment's draft: prerequisites, individual targets,
 * rules and the default rule. Every edit hands a new draft to `onChange`.
 */
export function RulesEditor({
  project,
  flag,
  draft,
  onChange,
  readOnly,
  problems,
  segments,
}: {
  project: string;
  flag: FlagOut;
  draft: ConfigDraft;
  onChange: (draft: ConfigDraft) => void;
  readOnly: boolean;
  /** Problems by editor field (see `problemField`). */
  problems: Record<string, string>;
  /** The project's segment keys. */
  segments: string[];
}) {
  const moveButtons = useRef<MoveButtons>(new Map());
  // The move button to focus once a moved rule has re-rendered in its new place.
  const focusAfterMove = useRef<{ ruleId: string; dir: "up" | "down" } | null>(
    null,
  );
  useEffect(() => {
    const pending = focusAfterMove.current;
    if (!pending) return;
    focusAfterMove.current = null;
    const other = pending.dir === "up" ? "down" : "up";
    const same = moveButtons.current.get(`${pending.ruleId}:${pending.dir}`);
    // At an end the pressed direction is disabled, so focus the other one.
    const target =
      same && !same.disabled
        ? same
        : moveButtons.current.get(`${pending.ruleId}:${other}`);
    target?.focus();
  }, [draft.rules]);

  const list = useApiData(`flags:${project}:all`, () =>
    must(
      api.GET("/api/projects/{project}/flags", {
        params: { path: { project }, query: { limit: 500 } },
      }),
    ),
  );
  const others = list.data
    ? list.data.items.map((f) => f.key).filter((key) => key !== flag.key)
    : null;
  const variations = flag.variations.map((v) => v.key);

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Section title="Prerequisites">
        {draft.prerequisites.length === 0 ? (
          <p className="text-muted-foreground">No prerequisites.</p>
        ) : (
          <ol className="flex flex-col gap-4">
            {draft.prerequisites.map((p, i) => (
              <PrerequisiteRow
                key={p.id}
                project={project}
                index={i}
                prerequisite={p}
                flags={others}
                readOnly={readOnly}
                error={problems[`prerequisites[${i}]`]}
                onChange={(patch) =>
                  onChange(updatePrerequisite(draft, p.id, patch))
                }
                onRemove={() => onChange(removePrerequisite(draft, p.id))}
              />
            ))}
          </ol>
        )}
        {!readOnly && (
          <Button
            variant="outline"
            className="min-h-11 w-fit"
            onClick={() => onChange(addPrerequisite(draft))}
          >
            <Plus aria-hidden />
            Add prerequisite
          </Button>
        )}
      </Section>
      <Section title="Individual targets">
        <div className="grid min-w-0 gap-4 md:grid-cols-2">
          {variations.map((v) => (
            <TargetsField
              key={v}
              variation={v}
              keys={draft.targets[v] ?? []}
              readOnly={readOnly}
              onApply={(keys) => {
                const next = setTargets(draft, v, keys);
                if (
                  next.targets[v]?.join("\n") !== draft.targets[v]?.join("\n")
                )
                  onChange(next);
              }}
            />
          ))}
        </div>
        <FieldError id="targets-error" error={problems.targets} />
      </Section>
      <Section title="Rules">
        {draft.rules.length === 0 ? (
          <p className="text-muted-foreground">
            No rules. Everyone who is not targeted gets the default rule.
          </p>
        ) : (
          <div className="flex min-w-0 flex-col gap-4">
            {draft.rules.map((r, i) => (
              <RuleCard
                key={r.id}
                rule={r}
                index={i}
                count={draft.rules.length}
                flag={flag}
                segments={segments}
                readOnly={readOnly}
                problems={problems}
                moveButtons={moveButtons}
                onChange={(edit) => onChange(edit(draft))}
                onMove={(by) => {
                  focusAfterMove.current = {
                    ruleId: r.id,
                    dir: by < 0 ? "up" : "down",
                  };
                  onChange(moveRule(draft, r.id, by));
                }}
                onRemove={() => onChange(removeRule(draft, r.id))}
              />
            ))}
          </div>
        )}
        {!readOnly && (
          <Button
            variant="outline"
            className="min-h-11 w-fit"
            onClick={() => onChange(addRule(draft, variations[0] ?? ""))}
          >
            <Plus aria-hidden />
            Add rule
          </Button>
        )}
      </Section>
      <Section title="Default rule">
        <ServeEditor
          serve={draft.fallthrough}
          onChange={(serve) => onChange(setFallthrough(draft, serve))}
          type={flag.type}
          variations={variations}
          readOnly={readOnly}
          weightsError={problems["fallthrough.rollout.weights"]}
        />
        <FieldError id="fallthrough-error" error={problems.fallthrough} />
      </Section>
    </div>
  );
}
