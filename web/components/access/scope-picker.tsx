"use client";

import {
  useEnvironmentKeys,
  useProjectKeys,
} from "@/components/access/use-catalogue";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { ScopeValue } from "@/lib/access/bindings";
import { type Level, LEVEL_TEXT } from "@/lib/access/permissions";

const ALL_LEVELS: Level[] = ["org", "project", "environment"];

/** The options for a select: the current value first if the list doesn't have it (yet). */
function withCurrent(keys: string[] | null, current: string | null): string[] {
  const list = keys ?? [];
  return current && !list.includes(current) ? [current, ...list] : list;
}

/**
 * Where a role applies: a level, then a project and an environment as the level needs them.
 * Changing the level or the project clears the fields below it. With `lockedProject` the project
 * is fixed.
 */
export function ScopePicker({
  value,
  onChange,
  levels = ALL_LEVELS,
  lockedProject,
  idPrefix,
  label = "Level",
  levelText = LEVEL_TEXT,
}: {
  value: ScopeValue;
  onChange: (value: ScopeValue) => void;
  /** The levels to offer, such as only project and environment on a project's page. */
  levels?: Level[];
  lockedProject?: string;
  /** Makes the field ids unique when several pickers share a page. */
  idPrefix: string;
  /** The level select's label, such as "Where it works". */
  label?: string;
  /** Each level's option text and help, when the roles' wording doesn't fit. */
  levelText?: Record<Level, { label: string; where: string }>;
}) {
  const projects = useProjectKeys();
  const project = lockedProject ?? value.project;
  const envs = useEnvironmentKeys(
    value.scope === "environment" ? project : null,
  );
  const id = (field: string) => `${idPrefix}-${field}`;

  return (
    <div className="grid gap-3 md:grid-cols-3">
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={id("level")}>{label}</Label>
        <NativeSelect
          id={id("level")}
          value={value.scope}
          aria-describedby={id("level-help")}
          onChange={(e) => {
            const scope = e.target.value as Level;
            onChange({
              scope,
              project: scope === "org" ? null : (lockedProject ?? null),
              env: null,
            });
          }}
        >
          {levels.map((level) => (
            <option key={level} value={level}>
              {levelText[level].label}
            </option>
          ))}
        </NativeSelect>
        <p id={id("level-help")} className="text-sm text-muted-foreground">
          {levelText[value.scope].where}
        </p>
      </div>
      {value.scope !== "org" && (
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor={id("project")}>Project</Label>
          <NativeSelect
            id={id("project")}
            value={project ?? ""}
            disabled={lockedProject !== undefined}
            onChange={(e) =>
              onChange({ ...value, project: e.target.value || null, env: null })
            }
          >
            <option value="">Pick a project</option>
            {withCurrent(lockedProject ? [] : projects, project).map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </NativeSelect>
        </div>
      )}
      {value.scope === "environment" && (
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor={id("env")}>Environment</Label>
          <NativeSelect
            id={id("env")}
            value={value.env ?? ""}
            disabled={!project}
            onChange={(e) =>
              onChange({ ...value, project, env: e.target.value || null })
            }
          >
            <option value="">Pick an environment</option>
            {withCurrent(envs, value.env).map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </NativeSelect>
        </div>
      )}
    </div>
  );
}
