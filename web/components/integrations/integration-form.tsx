"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  draftField,
  draftFrom,
  emptyDraft,
  type IntegrationDraft,
  toCreate,
  toUpdate,
  validateDraft,
} from "@/lib/integrations/form";
import { EVENT_GROUPS, KIND_LABELS } from "@/lib/integrations/groups";

type S = components["schemas"];
type Errors = Partial<Record<keyof IntegrationDraft, string>>;

const KINDS: S["IntegrationKind"][] = ["webhook", "slack", "teams"];

const URL_HELP: Record<S["IntegrationKind"], string> = {
  webhook: "Where MegooFlags sends a signed POST for each event",
  slack: "Incoming webhook URL (hooks.slack.com)",
  teams: "Workflow URL from Microsoft Teams",
};

/** A labelled switch with a 44 px hit area. */
function LabelledSwitch({
  checked,
  onCheckedChange,
  label,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <label
      data-hit-area
      className="inline-flex min-h-11 min-w-11 items-center gap-2"
    >
      <Switch
        checked={checked}
        onCheckedChange={(value) => onCheckedChange(value)}
        aria-label={label}
      />
      <span className="text-sm font-medium">{label}</span>
    </label>
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

function Form({
  project,
  integration,
  onSaved,
}: {
  project: string;
  integration: S["IntegrationOut"] | null;
  onSaved: (created: S["IntegrationCreated"] | null) => void;
}) {
  const id = useId();
  const editing = integration !== null;
  const [draft, setDraft] = useState<IntegrationDraft>(() =>
    integration ? draftFrom(integration) : emptyDraft("webhook"),
  );
  // Field errors show only after the first Save.
  const [checked, setChecked] = useState(false);
  const [serverErrors, setServerErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const environments = useApiData(`environments:${project}`, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ),
  );

  const local = validateDraft(draft, editing);
  const errors: Errors = { ...serverErrors, ...(checked ? local : {}) };
  const update = (patch: Partial<IntegrationDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setServerErrors((e) => {
      const next = { ...e };
      for (const field of Object.keys(patch)) {
        delete next[field as keyof IntegrationDraft];
      }
      return next;
    });
  };
  const errorId = (field: keyof IntegrationDraft) => `${id}-${field}-error`;
  const invalid = (field: keyof IntegrationDraft) =>
    errors[field]
      ? { "aria-invalid": true, "aria-describedby": errorId(field) }
      : {};

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    try {
      if (integration) {
        await must(
          api.PATCH("/api/projects/{project}/integrations/{id}", {
            params: { path: { project, id: integration.id } },
            body: toUpdate(draft, integration),
          }),
        );
        toast("Saved");
        onSaved(null);
      } else {
        const created = await must(
          api.POST("/api/projects/{project}/integrations", {
            params: { path: { project } },
            body: toCreate(draft),
          }),
        );
        toast("Integration added");
        onSaved(created);
      }
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) {
        console.error("saving the integration failed", error);
        toast.error("Couldn't reach the server");
        return;
      }
      const { failure } = error;
      const field = failure.status === 422 ? draftField(failure.path) : null;
      if (field) setServerErrors({ [field]: failure.message });
      else toast.error(failureText(failure));
    }
  };

  const envList = environments.data ?? [];

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-sm font-medium">Type</legend>
        <div
          role="radiogroup"
          aria-label="Type"
          className="flex flex-wrap gap-x-6"
        >
          {KINDS.map((kind) => (
            <label
              key={kind}
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2"
            >
              <input
                type="radio"
                name={`${id}-kind`}
                className="size-5 shrink-0 accent-primary"
                checked={draft.kind === kind}
                disabled={editing}
                onChange={() => update({ kind })}
              />
              {KIND_LABELS[kind]}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-name`}>Name</Label>
        <Input
          id={`${id}-name`}
          value={draft.name}
          onChange={(e) => update({ name: e.target.value })}
          {...invalid("name")}
        />
        <FieldError id={errorId("name")} error={errors.name} />
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-url`}>URL</Label>
        <Input
          id={`${id}-url`}
          type="url"
          inputMode="url"
          autoCapitalize="none"
          spellCheck={false}
          value={draft.url}
          placeholder={integration?.url_hint}
          onChange={(e) => update({ url: e.target.value })}
          aria-invalid={errors.url ? true : undefined}
          aria-describedby={[`${id}-url-help`, errors.url && errorId("url")]
            .filter(Boolean)
            .join(" ")}
        />
        <p id={`${id}-url-help`} className="text-sm text-muted-foreground">
          {editing
            ? "Leave empty to keep the current URL"
            : URL_HELP[draft.kind]}
        </p>
        <FieldError id={errorId("url")} error={errors.url} />
      </div>
      <fieldset
        className="flex flex-col gap-1"
        aria-describedby={
          errors.eventGroups ? errorId("eventGroups") : undefined
        }
      >
        <legend className="mb-1 text-sm font-medium">Events</legend>
        {EVENT_GROUPS.map((group) => (
          <div key={group.id} className="flex flex-col">
            <label
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2"
            >
              <Checkbox
                checked={draft.eventGroups.includes(group.id)}
                aria-describedby={`${id}-${group.id}-description`}
                onChange={(e) =>
                  update({
                    eventGroups: e.target.checked
                      ? [...draft.eventGroups, group.id]
                      : draft.eventGroups.filter((g) => g !== group.id),
                  })
                }
              />
              {group.label}
            </label>
            <p
              id={`${id}-${group.id}-description`}
              className="pl-7 text-sm text-muted-foreground"
            >
              {group.description}
            </p>
          </div>
        ))}
        <FieldError id={errorId("eventGroups")} error={errors.eventGroups} />
      </fieldset>
      <fieldset
        className="flex flex-col gap-1"
        aria-describedby={
          errors.environments ? errorId("environments") : undefined
        }
      >
        <legend className="mb-1 text-sm font-medium">Environments</legend>
        <LabelledSwitch
          label="All environments"
          checked={draft.allEnvironments}
          onCheckedChange={(allEnvironments) => update({ allEnvironments })}
        />
        {!draft.allEnvironments &&
          (environments.failure ? (
            <p className="text-sm text-destructive">
              Couldn&apos;t load environments:{" "}
              {failureText(environments.failure)}
            </p>
          ) : (
            envList.map((env) => (
              <label
                key={env.key}
                data-hit-area
                className="inline-flex min-h-11 min-w-11 items-center gap-2"
              >
                <Checkbox
                  checked={draft.environments.includes(env.key)}
                  onChange={(e) =>
                    update({
                      environments: e.target.checked
                        ? [...draft.environments, env.key]
                        : draft.environments.filter((k) => k !== env.key),
                    })
                  }
                />
                {env.name}
              </label>
            ))
          ))}
        <FieldError id={errorId("environments")} error={errors.environments} />
      </fieldset>
      <LabelledSwitch
        label="Active"
        checked={draft.enabled}
        onCheckedChange={(enabled) => update({ enabled })}
      />
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          {editing ? "Save" : "Add integration"}
        </Button>
      </div>
    </form>
  );
}

/** Adds an integration, or edits `integration`. A new webhook's secret goes to `onSaved`. */
export function IntegrationForm({
  project,
  integration,
  open,
  onOpenChange,
  onSaved,
}: {
  project: string;
  /** Null to add one. */
  integration: S["IntegrationOut"] | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** After a save; `created` is the new integration, null after an edit. */
  onSaved: (created: S["IntegrationCreated"] | null) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={integration ? "Edit integration" : "Add integration"}
    >
      {open && (
        <Form
          key={integration?.id ?? "new"}
          project={project}
          integration={integration}
          onSaved={onSaved}
        />
      )}
    </ResponsiveDialog>
  );
}
