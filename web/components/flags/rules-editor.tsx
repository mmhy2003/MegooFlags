"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { ServeEditor } from "@/components/flags/serve-editor";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import {
  addPrerequisite,
  type ConfigDraft,
  type PrereqDraft,
  removePrerequisite,
  setFallthrough,
  setTargets,
  updatePrerequisite,
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
}: {
  project: string;
  flag: FlagOut;
  draft: ConfigDraft;
  onChange: (draft: ConfigDraft) => void;
  readOnly: boolean;
  /** Problems by editor field (see `problemField`). */
  problems: Record<string, string>;
}) {
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
      <Section title="Rules">{null}</Section>
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
