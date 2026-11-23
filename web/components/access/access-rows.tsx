"use client";

import { Plus, Trash2 } from "lucide-react";

import { ScopePicker } from "@/components/access/scope-picker";
import { useRoles } from "@/components/access/use-catalogue";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { AccessRow } from "@/lib/access/bindings";
import type { Level } from "@/lib/access/permissions";

const PROJECT_LEVELS: Level[] = ["project", "environment"];

/** The role selects of one row: the current role first if the list doesn't have it (yet). */
function roleOptions(
  roles: { key: string; name: string }[] | null,
  current: string,
): { key: string; name: string }[] {
  const list = roles ?? [];
  return current && !list.some((r) => r.key === current)
    ? [{ key: current, name: current }, ...list]
    : list;
}

/**
 * Roles to grant, each at a scope, as create forms collect them. With `lockedProject` every row
 * stays in that project.
 */
export function AccessRows({
  value,
  onChange,
  lockedProject,
  idPrefix,
}: {
  value: AccessRow[];
  onChange: (value: AccessRow[]) => void;
  lockedProject?: string;
  /** Makes the field ids unique when several lists share a page. */
  idPrefix: string;
}) {
  const roles = useRoles();
  const update = (index: number, row: AccessRow) =>
    onChange(value.map((r, i) => (i === index ? row : r)));
  const add = () =>
    onChange([
      ...value,
      lockedProject
        ? { role: "", scope: "project", project: lockedProject, env: null }
        : { role: "", scope: "org", project: null, env: null },
    ]);

  return (
    <div className="flex flex-col gap-4">
      {value.map((row, index) => {
        const n = index + 1;
        const id = `${idPrefix}-${index}`;
        return (
          <fieldset
            key={index}
            className="flex flex-col gap-3 rounded-md border p-3"
          >
            {/* A legend names the group only as its first child, so the visible title is a copy. */}
            <legend className="sr-only">Access {n}</legend>
            <div className="flex items-center justify-between gap-2">
              <span aria-hidden className="text-sm font-medium">
                Access {n}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Remove access ${n}`}
                className="min-h-11 min-w-11"
                onClick={() => onChange(value.filter((_, i) => i !== index))}
              >
                <Trash2 aria-hidden />
              </Button>
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <Label htmlFor={`${id}-role`}>Role</Label>
              <NativeSelect
                id={`${id}-role`}
                value={row.role}
                onChange={(e) =>
                  update(index, { ...row, role: e.target.value })
                }
              >
                <option value="">Pick a role</option>
                {roleOptions(roles, row.role).map((role) => (
                  <option key={role.key} value={role.key}>
                    {role.name}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <ScopePicker
              value={row}
              onChange={(scope) => update(index, { ...row, ...scope })}
              levels={lockedProject ? PROJECT_LEVELS : undefined}
              lockedProject={lockedProject}
              idPrefix={id}
            />
          </fieldset>
        );
      })}
      <div>
        <Button type="button" variant="outline" onClick={add}>
          <Plus aria-hidden />
          Add access
        </Button>
      </div>
    </div>
  );
}
