"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { toast } from "sonner";

import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { PageHeader } from "@/components/patterns/page-header";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useApiData } from "@/hooks/use-api-data";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import { projectPath, projectTrail } from "@/lib/routes";

type Project = components["schemas"]["ProjectOut"];
type ProjectUpdate = components["schemas"]["ProjectUpdate"];

const NAME_MAX = 200;
const STALE_MIN = 1;
const STALE_MAX = 365;

type Field = "name" | "description" | "stale_after_days";
type FieldErrors = Partial<Record<Field, string>>;

/** The days typed, or null when it is not a whole number from 1 to 365. */
function staleDays(text: string): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const n = Number(text);
  return n >= STALE_MIN && n <= STALE_MAX ? n : null;
}

/** The project's name, description and stale-flag threshold; read-only without project.manage. */
export function ProjectSettings({ project }: { project: string }) {
  const perms = useProjectPermissions(project, null, null);
  const canManage = can(perms, "project.manage");
  const loaded = useApiData<Project>(`project:${project}`, () =>
    must(
      api.GET("/api/projects/{project}", {
        params: { path: { project } },
      }),
    ),
  );
  // What the last save returned; reloading would drop the other card's unsaved edits.
  const [saved, setSaved] = useState<Project | null>(null);
  const current = saved ?? loaded.data;

  const header = (
    <PageHeader
      title="Project settings"
      trail={[
        ...projectTrail(project),
        { label: "Project settings", href: projectPath(project, "settings") },
      ]}
    />
  );

  if (loaded.failure)
    return (
      <>
        {header}
        <LoadFailed
          title="Couldn't load the project"
          failure={loaded.failure}
          onRetry={loaded.reload}
        />
      </>
    );
  // Wait for the permissions too, so the fields don't turn editable under the cursor.
  if (!current || !perms)
    return (
      <>
        {header}
        <LoadingRows label="Loading the project settings" />
      </>
    );

  return (
    <>
      {header}
      <div className="flex max-w-2xl min-w-0 flex-col gap-6">
        <SettingsForm
          project={current}
          canManage={canManage}
          title="Details"
          onSaved={setSaved}
        />
        <SettingsForm
          project={current}
          canManage={canManage}
          title="Stale flags"
          onSaved={setSaved}
        />
      </div>
    </>
  );
}

/** One card of settings with its own Save. */
function SettingsForm({
  project,
  canManage,
  title,
  onSaved,
}: {
  project: Project;
  canManage: boolean;
  title: "Details" | "Stale flags";
  onSaved: (saved: Project) => void;
}) {
  const id = useId();
  const router = useRouter();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [days, setDays] = useState(String(project.stale_after_days));
  const [server, setServer] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const details = title === "Details";

  const count = staleDays(days);
  const local: FieldErrors = {};
  if (details) {
    if (name.trim() === "") local.name = "Enter a name";
    else if (name.length > NAME_MAX)
      local.name = `Use at most ${NAME_MAX} characters`;
  } else if (count === null) {
    local.stale_after_days = `Enter ${STALE_MIN} to ${STALE_MAX}`;
  }
  const errors: FieldErrors = { ...server, ...local };

  const body: ProjectUpdate = details
    ? { name: name.trim(), description }
    : { stale_after_days: count };
  const changed = details
    ? name.trim() !== project.name || description !== project.description
    : count !== project.stale_after_days;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    setServer({});
    setFailure(null);
    try {
      const saved = await must(
        api.PATCH("/api/projects/{project}", {
          params: { path: { project: project.key } },
          body,
        }),
      );
      toast("Project saved");
      // The project switcher is loaded on the server.
      router.refresh();
      onSaved(saved);
    } catch (err) {
      if (!(err instanceof ApiError)) {
        console.error("saving the project failed", err);
        setFailure("Couldn't reach the server");
        return;
      }
      const f = err.failure;
      const path = f.path as Field | null;
      if (f.status === 422 && path !== null && path in body)
        setServer({ [path]: f.message });
      else setFailure(formFailureText(f));
    } finally {
      setBusy(false);
    }
  };

  const invalid = (which: Field) =>
    errors[which]
      ? { "aria-invalid": true, "aria-describedby": `${id}-${which}-error` }
      : {};

  const error = (which: Field) =>
    errors[which] && (
      <p id={`${id}-${which}-error`} className="text-sm text-destructive">
        {errors[which]}
      </p>
    );

  const field = (
    which: Field | "key",
    label: string,
    control: React.ReactNode,
  ) => (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={`${id}-${which}`}>{label}</Label>
      {control}
      {which !== "key" && error(which)}
    </div>
  );

  return (
    <Card
      role="region"
      aria-labelledby={`${id}-title`}
      className="min-w-0 px-4"
    >
      <CardHeader className="px-0">
        <CardTitle>
          <h2 id={`${id}-title`}>{title}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-0">
        <form noValidate onSubmit={submit} className="flex flex-col gap-4">
          {details ? (
            <>
              {field(
                "name",
                "Name",
                <Input
                  id={`${id}-name`}
                  autoComplete="off"
                  value={name}
                  readOnly={!canManage}
                  onChange={(e) => {
                    setName(e.target.value);
                    setServer((s) => ({ ...s, name: undefined }));
                  }}
                  {...invalid("name")}
                />,
              )}
              {field(
                "key",
                "Key",
                <Input
                  id={`${id}-key`}
                  className="font-mono"
                  value={project.key}
                  readOnly
                />,
              )}
              {field(
                "description",
                "Description",
                <Textarea
                  id={`${id}-description`}
                  className="md:text-base"
                  value={description}
                  readOnly={!canManage}
                  onChange={(e) => {
                    setDescription(e.target.value);
                    setServer((s) => ({ ...s, description: undefined }));
                  }}
                  {...invalid("description")}
                />,
              )}
            </>
          ) : (
            <div className="flex min-w-0 flex-col gap-2">
              <Label htmlFor={`${id}-stale_after_days`}>
                Stale after (days)
              </Label>
              <Input
                id={`${id}-stale_after_days`}
                type="number"
                inputMode="numeric"
                min={STALE_MIN}
                max={STALE_MAX}
                step={1}
                className="w-28"
                value={days}
                readOnly={!canManage}
                onChange={(e) => {
                  setDays(e.target.value);
                  setServer((s) => ({ ...s, stale_after_days: undefined }));
                }}
                {...invalid("stale_after_days")}
                aria-describedby={[
                  `${id}-stale-hint`,
                  errors.stale_after_days && `${id}-stale_after_days-error`,
                ]
                  .filter(Boolean)
                  .join(" ")}
              />
              <p
                id={`${id}-stale-hint`}
                className="text-sm text-muted-foreground"
              >
                A flag no environment has evaluated for this many days is marked
                stale.
              </p>
              {error("stale_after_days")}
            </div>
          )}
          {failure && (
            <p role="alert" className="text-sm text-destructive">
              {failure}
            </p>
          )}
          {canManage && (
            <div>
              <Button
                type="submit"
                className="min-h-11"
                disabled={busy || !changed || Object.keys(local).length > 0}
              >
                Save
              </Button>
            </div>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
